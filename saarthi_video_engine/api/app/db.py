"""Database schema and engine. SQLite locally and in tests, PostgreSQL in production.

Large media never goes in the database: rows hold object keys, hashes, and metadata.
The schema is created and upgraded only through Alembic migrations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("display_name", String(200), nullable=False, default=""),
    Column("password_hash", String(400), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("last_login_at", DateTime(timezone=True)),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("user_agent", String(300), nullable=False, default=""),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(320), nullable=False, index=True),
    Column("ip", String(64), nullable=False, default=""),
    Column("success", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow, index=True),
)

uploads = Table(
    "uploads",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_id", String(64), nullable=False, index=True),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("object_key", String(300), nullable=False),
    Column("filename", String(300), nullable=False),
    Column("declared_size", Integer),
    Column("size_bytes", Integer),
    Column("sha256", String(64)),
    Column("meta", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

studio_sessions = Table(
    "studio_sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_id", String(64), nullable=False, index=True),
    Column("mode", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("plan_version_id", String(40)),
    Column("record", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("owner_id", String(64), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("active_slot", String(16)),
    Column("idempotency_key", String(100), unique=True),
    Column("payload", JSON, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=1),
    Column("lease_owner", String(100)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("last_error", Text, nullable=False, default=""),
    Column("failure_category", String(40), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("session_id", "active_slot", name="uq_jobs_one_active_per_slot"),
)
Index("ix_jobs_status_created", jobs.c.status, jobs.c.created_at)

plan_versions = Table(
    "plan_versions",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("session_id", String(36), ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("number", Integer, nullable=False),
    Column("parent_version_id", String(40)),
    Column("origin", String(20), nullable=False),
    Column("validation_ok", Boolean, nullable=False),
    Column("title", JSON),
    Column("object_key", String(300), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    UniqueConstraint("session_id", "number", name="uq_plan_version_number"),
)

renders = Table(
    "renders",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), ForeignKey("studio_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("job_id", String(36)),
    Column("plan_version_id", String(40)),
    Column("status", String(24), nullable=False),
    Column("object_prefix", String(300), nullable=False),
    Column("details", JSON, nullable=False),
    Column("error", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("completed_at", DateTime(timezone=True)),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(64)),
    Column("session_id", String(36)),
    Column("action", String(60), nullable=False),
    Column("detail", JSON, nullable=False),
    Column("request_id", String(64), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow, index=True),
)

workers = Table(
    "workers",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("hostname", String(200), nullable=False, default=""),
    Column("version", String(40), nullable=False, default=""),
    Column("started_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("current_job_id", String(36)),
)

_ENGINES: dict[str, Engine] = {}


def get_engine(url: str) -> Engine:
    if url not in _ENGINES:
        kwargs: dict = {"future": True, "pool_pre_ping": True, "json_serializer": lambda value: json.dumps(value, ensure_ascii=False)}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(engine, "connect")
            def _sqlite_pragmas(connection, _record):  # noqa: ANN001
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

        _ENGINES[url] = engine
    return _ENGINES[url]


def _alembic_config(url: str | None = None):
    from alembic.config import Config

    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "migrations"))
    if url is not None:
        # ConfigParser treats % as interpolation; URL-encoded passwords contain it.
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def migrate(url: str) -> None:
    """Upgrade the database to the latest Alembic revision."""
    from alembic import command

    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(url), "head")


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def schema_state(db: Engine) -> dict:
    """Where the database is against the code, read without changing anything."""
    from sqlalchemy import inspect, text

    with db.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar() if "alembic_version" in tables else None
    head = head_revision()
    missing = sorted(set(metadata.tables) - tables)
    return {"ok": revision == head and not missing, "revision": revision, "head": head, "missing_tables": missing}


def database_label(url: str) -> str:
    """Host, port, and database name for logs. Never the user or password."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return f"sqlite:{Path(parsed.database or '').name}"
    return f"{parsed.get_backend_name()}://{parsed.host}:{parsed.port or 5432}/{parsed.database}"


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
