"""Migrations and worker registration against a real PostgreSQL server.

Set STUDIO_TEST_POSTGRES_URL to an admin URL (for example
postgresql://postgres@127.0.0.1:54329/postgres). Each test creates and drops its own
database. Without the variable these tests are skipped; SQLite cannot show
PostgreSQL's transactional DDL.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect, select, text, update

from app import cli, jobqueue
from app.config import Settings
from app.db import _alembic_config, get_engine, head_revision, jobs, metadata, migrate, schema_state, studio_sessions, utcnow, workers
from app.storage import StudioStore
from app.worker import wait_for_schema

ADMIN_URL = os.environ.get("STUDIO_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="STUDIO_TEST_POSTGRES_URL is not set")


def _admin():
    url = Settings(data_dir=Settings.from_env().data_dir, max_file_bytes=1, max_session_bytes=1, cors_origins=(), database_url=ADMIN_URL).resolved_database_url
    return create_engine(url, isolation_level="AUTOCOMMIT", future=True), url


@pytest.fixture
def pg_url():
    """A fresh, empty database. Returned in the postgres:// form Render hands out."""
    admin, admin_url = _admin()
    name = f"studio_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    base = admin_url.rsplit("/", 1)[0].replace("postgresql+psycopg://", "postgres://", 1)
    try:
        yield f"{base}/{name}"
    finally:
        for engine_url in list(_engines_for(name)):
            get_engine(engine_url).dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _engines_for(name: str):
    from app.db import _ENGINES

    return [url for url in _ENGINES if url.endswith(f"/{name}")]


def _settings(url: str) -> Settings:
    return Settings(data_dir=Settings.from_env().data_dir, max_file_bytes=1, max_session_bytes=1, cors_origins=(), database_url=url)


def _fresh_inspector(url: str):
    """A new engine, so nothing uncommitted from the migration connection can be seen."""
    engine = create_engine(_settings(url).resolved_database_url, future=True)
    try:
        yield inspect(engine)
    finally:
        engine.dispose()


def _snapshot(url: str) -> dict:
    """Every table's columns, types, defaults, keys, constraints, and indexes."""
    engine = create_engine(_settings(url).resolved_database_url, future=True)
    try:
        insp = inspect(engine)
        shape = {}
        for table in sorted(insp.get_table_names(schema="public")):
            shape[table] = {
                "columns": [(c["name"], str(c["type"]), c["nullable"], str(c.get("default"))) for c in insp.get_columns(table)],
                "pk": insp.get_pk_constraint(table)["constrained_columns"],
                "fks": sorted((tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]), str(f.get("options"))) for f in insp.get_foreign_keys(table)),
                "unique": sorted((u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)),
                "indexes": sorted((i["name"], tuple(i["column_names"]), i["unique"]) for i in insp.get_indexes(table)),
            }
        return shape
    finally:
        engine.dispose()


def test_repair_module_builds_exactly_the_0001_schema(pg_url: str):
    """0002 creates tables from migrations/initial_schema.py; they must match 0001_initial.py exactly."""
    from alembic import command

    url = _settings(pg_url).resolved_database_url
    command.upgrade(_alembic_config(url), "0001_initial")
    original = _snapshot(pg_url)
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        for table in ("workers", "audit_events", "renders", "plan_versions", "jobs", "studio_sessions", "uploads", "login_attempts", "auth_sessions", "users"):
            conn.execute(text(f"DROP TABLE {table}"))
    engine.dispose()
    migrate(url)
    repaired = _snapshot(pg_url)
    assert set(original) == set(metadata.tables) | {"alembic_version"}
    assert repaired == original


def test_fresh_postgres_database_gets_every_table_and_revision(pg_url: str):
    url = _settings(pg_url).resolved_database_url
    migrate(url)
    for insp in _fresh_inspector(pg_url):
        tables = set(insp.get_table_names(schema="public"))
        assert set(metadata.tables) <= tables, sorted(set(metadata.tables) - tables)
        columns = {column["name"] for column in insp.get_columns("workers", schema="public")}
        assert columns == {column.name for column in workers.columns}
    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head_revision() == "0002_repair_missing_tables"
    engine.dispose()
    assert schema_state(get_engine(url)) == {"ok": True, "revision": head_revision(), "head": head_revision(), "missing_tables": []}


def test_record_worker_inserts_then_updates(pg_url: str):
    url = _settings(pg_url).resolved_database_url
    migrate(url)
    db = get_engine(url)
    jobqueue.record_worker(db, "worker-a", "phase5-1")
    with db.connect() as conn:
        first = conn.execute(select(workers).where(workers.c.id == "worker-a")).one()
    assert first.version == "phase5-1" and first.current_job_id is None
    jobqueue.record_worker(db, "worker-a", "phase5-1", "job-1")
    with db.connect() as conn:
        rows = conn.execute(select(workers)).all()
    assert len(rows) == 1 and rows[0].current_job_id == "job-1" and rows[0].last_seen_at >= first.last_seen_at
    assert [row["id"] for row in jobqueue.live_workers(db)] == ["worker-a"]


def test_rerunning_migrations_keeps_schema_and_data(pg_url: str):
    """A complete 0001 database (the state the API pre-deploy left) upgrades to head as a no-op."""
    from alembic import command

    url = _settings(pg_url).resolved_database_url
    command.upgrade(_alembic_config(url), "0001_initial")
    db = get_engine(url)
    session_id, owner = str(uuid.uuid4()), str(uuid.uuid4())
    now = utcnow()
    with db.begin() as conn:
        conn.execute(studio_sessions.insert().values(id=session_id, owner_id=owner, mode="auto_edit", status="prepared", record={"session_id": session_id}, created_at=now, updated_at=now))
    job, created = jobqueue.enqueue(db, session_id=session_id, owner=owner, kind="process", idempotency_key="key-1")
    assert created
    at_0001 = _snapshot(pg_url)
    migrate(url)
    at_0002 = _snapshot(pg_url)
    assert at_0002 == at_0001
    migrate(url)
    migrate(url)
    assert _snapshot(pg_url) == at_0002
    assert schema_state(db)["revision"] == "0002_repair_missing_tables"
    with db.connect() as conn:
        assert conn.execute(select(studio_sessions.c.id)).scalars().all() == [session_id]
        assert conn.execute(select(jobs.c.id, jobs.c.status)).all() == [(job["id"], "queued")]
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1


def _seed(db) -> tuple[str, str]:
    session_id, owner = str(uuid.uuid4()), str(uuid.uuid4())
    now = utcnow()
    with db.begin() as conn:
        conn.execute(studio_sessions.insert().values(id=session_id, owner_id=owner, mode="my_broll", status="plan_ready", record={"session_id": session_id, "title": "kept"}, created_at=now, updated_at=now))
    job, _ = jobqueue.enqueue(db, session_id=session_id, owner=owner, kind="render", payload={"render_id": "r1"}, idempotency_key="render-key")
    return session_id, job["id"]


def test_repair_migration_restores_missing_workers_table_and_keeps_rows(pg_url: str):
    """The production state: stamped at 0001_initial, but without the workers table."""
    from alembic import command

    url = _settings(pg_url).resolved_database_url
    command.upgrade(_alembic_config(url), "0001_initial")
    db = get_engine(url)
    session_id, job_id = _seed(db)
    with db.begin() as conn:
        conn.execute(text("DROP TABLE workers"))
        conn.execute(text("DROP INDEX ix_jobs_status_created"))
    state = schema_state(db)
    assert not state["ok"] and state["revision"] == "0001_initial" and state["missing_tables"] == ["workers"]
    with pytest.raises(Exception, match="workers"):
        jobqueue.record_worker(db, "worker-c", "phase5-1")

    migrate(url)

    assert schema_state(db)["ok"]
    jobqueue.record_worker(db, "worker-c", "phase5-1")
    for insp in _fresh_inspector(pg_url):
        assert "ix_jobs_status_created" in {index["name"] for index in insp.get_indexes("jobs")}
        assert {column["name"] for column in insp.get_columns("workers")} == {column.name for column in workers.columns}
    with db.connect() as conn:
        assert conn.execute(select(studio_sessions.c.id, studio_sessions.c.record)).all() == [(session_id, {"session_id": session_id, "title": "kept"})]
        assert conn.execute(select(jobs.c.id, jobs.c.idempotency_key, jobs.c.status)).all() == [(job_id, "render-key", "queued")]


def test_worker_waits_for_migration_then_starts(pg_url: str):
    url = _settings(pg_url).resolved_database_url
    store = StudioStore(_settings(pg_url))
    with pytest.raises(SystemExit, match="Run `python -m app.cli migrate`"):
        wait_for_schema(store, timeout=0)
    calls = []

    def migrate_during_wait(_seconds: float) -> None:
        calls.append(1)
        migrate(url)

    state = wait_for_schema(store, timeout=60, interval=0, sleep=migrate_during_wait)
    assert state["ok"] and calls == [1]
    jobqueue.record_worker(store.db, "worker-d", "phase5-1")


def test_cli_migrate_and_check_schema_report_the_revision(pg_url: str, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("STUDIO_ENV", "development")
    assert cli.main(["check-schema"]) == 1
    assert cli.main(["migrate"]) == 0
    assert cli.main(["check-schema"]) == 0
    out = capsys.readouterr().out
    assert "missing tables none" in out and head_revision() in out
    assert "postgres@" not in out and "password" not in out.lower()


def test_first_owner_account_on_postgres(pg_url: str, monkeypatch, capsys):
    """The Render shell procedure: migrate, create-user, user-status, verify-login, against PostgreSQL."""
    from app.auth import login

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("STUDIO_ENV", "development")
    answers = iter(["a long enough test passphrase"] * 3)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt="": next(answers))
    assert cli.main(["migrate"]) == 0
    assert cli.main(["create-user", "--email", "Owner@Example.org", "--name", "Owner"]) == 0
    assert cli.main(["user-status", "--email", "owner@example.org"]) == 0
    assert cli.main(["verify-login", "--email", "owner@example.org"]) == 0
    out = capsys.readouterr().out
    assert f"Database: postgresql://127.0.0.1:" in out and "passphrase" not in out and "scrypt" not in out
    db = get_engine(_settings(pg_url).resolved_database_url)
    with db.connect() as conn:
        stored = conn.execute(text("SELECT email, password_hash LIKE 'scrypt$%' FROM users")).all()
    assert stored == [("owner@example.org", True)]
    user, token = login(db, "owner@example.org", "a long enough test passphrase", "127.0.0.1", "test", 1, 5)
    assert user["email"] == "owner@example.org" and token


def test_worker_loop_steps_work_on_postgres(pg_url: str):
    """The worker's first calls: register, reap expired leases, claim a job."""
    url = _settings(pg_url).resolved_database_url
    migrate(url)
    db = get_engine(url)
    session_id, owner = str(uuid.uuid4()), str(uuid.uuid4())
    now = utcnow()
    with db.begin() as conn:
        conn.execute(studio_sessions.insert().values(id=session_id, owner_id=owner, mode="auto_edit", status="queued", record={"session_id": session_id}, created_at=now, updated_at=now))
    jobqueue.record_worker(db, "worker-b", "phase5-1")
    assert jobqueue.claim(db, "worker-b", 60) is None
    job, _ = jobqueue.enqueue(db, session_id=session_id, owner=owner, kind="process")
    claimed = jobqueue.claim(db, "worker-b", 60)
    assert claimed["id"] == job["id"] and claimed["status"] == "running"
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(lease_expires_at=utcnow() - timedelta(seconds=5)))
    assert [row["id"] for row in jobqueue.expired(db)] == [job["id"]]


def test_api_and_worker_resolve_the_same_database(pg_url: str, monkeypatch):
    """Both processes build Settings from the same variables; the Render URL form is normalised once."""
    monkeypatch.setenv("DATABASE_URL", pg_url)
    api = Settings.from_env().resolved_database_url
    worker = Settings.from_env().resolved_database_url
    assert api == worker and api.startswith("postgresql+psycopg://") and api.endswith(pg_url.rsplit("/", 1)[1])
