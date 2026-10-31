"""Owner account commands: first account, duplicates, password checks, and what the account can do."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import cli
from app.config import Settings
from app.db import auth_sessions, get_engine, login_attempts, users
from app.factory import create_app

PASSWORD = "a long enough test passphrase"


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    database = f"sqlite:///{(tmp_path / 'studio.db').as_posix()}"
    monkeypatch.setenv("STUDIO_ENV", "development")
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("STUDIO_AUTO_MIGRATE", "true")
    return {"tmp": tmp_path, "url": database}


def _prompts(monkeypatch, *answers: str) -> list[str]:
    asked: list[str] = []
    queue = list(answers)

    def fake(prompt: str = "") -> str:
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(cli.getpass, "getpass", fake)
    return asked


def _rows(url: str) -> list:
    with get_engine(url).connect() as conn:
        return conn.execute(select(users)).all()


def test_first_account_is_created_with_a_scrypt_hash_and_can_sign_in(env, monkeypatch, capsys):
    asked = _prompts(monkeypatch, PASSWORD, PASSWORD)
    assert cli.main(["create-user", "--email", "  Owner@Example.org ", "--name", "Owner"]) == 0
    out = capsys.readouterr().out
    assert len(asked) == 2 and PASSWORD not in out
    assert "Database: sqlite:studio.db (development)" in out
    rows = _rows(env["url"])
    assert len(rows) == 1
    row = rows[0]
    assert row.email == "owner@example.org" and row.is_active and row.display_name == "Owner"
    assert row.password_hash.startswith("scrypt$") and PASSWORD not in row.password_hash

    settings = Settings(
        data_dir=env["tmp"] / "data", max_file_bytes=1_000_000, max_session_bytes=2_000_000, cors_origins=(),
        database_url=env["url"], auth_mode="password", session_secret="s" * 40,
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/sessions").status_code == 401
    signed_in = client.post("/api/auth/login", json={"email": "OWNER@example.org", "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.text
    csrf = signed_in.json()["csrf_token"]
    assert client.get("/api/auth/me").json()["user"]["email"] == "owner@example.org"
    assert client.get("/api/sessions").status_code == 200
    created = client.post("/api/uploads/initiate", headers={"X-CSRF-Token": csrf}, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": 1000})
    assert created.status_code == 200, created.text
    wrong = TestClient(create_app(settings)).post("/api/auth/login", json={"email": "owner@example.org", "password": "not the password"})
    assert wrong.status_code == 401


def test_duplicate_email_is_refused_and_the_existing_account_is_untouched(env, monkeypatch, capsys):
    _prompts(monkeypatch, PASSWORD, PASSWORD, "another long passphrase", "another long passphrase")
    assert cli.main(["create-user", "--email", "owner@example.org"]) == 0
    before = _rows(env["url"])
    assert cli.main(["create-user", "--email", "OWNER@example.org "]) == 1
    assert "already exists" in capsys.readouterr().err
    after = _rows(env["url"])
    assert len(after) == 1 and after[0].password_hash == before[0].password_hash and after[0].id == before[0].id


def test_mismatched_or_short_passwords_create_nothing(env, monkeypatch):
    _prompts(monkeypatch, PASSWORD, PASSWORD + "x")
    with pytest.raises(SystemExit, match="do not match"):
        cli.main(["create-user", "--email", "owner@example.org"])
    _prompts(monkeypatch, "short", "short")
    assert cli.main(["create-user", "--email", "owner@example.org"]) == 1
    assert _rows(env["url"]) == []


def test_user_status_and_verify_login_only_read(env, monkeypatch, capsys):
    assert cli.main(["user-status", "--email", "owner@example.org"]) == 1
    assert "Accounts in this database: 0" in capsys.readouterr().out
    _prompts(monkeypatch, PASSWORD, PASSWORD)
    cli.main(["create-user", "--email", "owner@example.org"])
    capsys.readouterr()

    assert cli.main(["user-status", "--email", "Owner@example.org"]) == 0
    out = capsys.readouterr().out
    assert "Accounts in this database: 1" in out and "active" in out and "last sign-in never" in out
    assert "scrypt" not in out

    _prompts(monkeypatch, PASSWORD)
    assert cli.main(["verify-login", "--email", "owner@example.org"]) == 0
    _prompts(monkeypatch, "not the password")
    assert cli.main(["verify-login", "--email", "owner@example.org"]) == 1
    _prompts(monkeypatch, PASSWORD)
    assert cli.main(["verify-login", "--email", "nobody@example.org"]) == 1
    out = capsys.readouterr().out
    assert "password matches" in out.lower() and "does not match" in out and PASSWORD not in out
    with get_engine(env["url"]).connect() as conn:
        assert conn.execute(select(func.count()).select_from(login_attempts)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(auth_sessions)).scalar() == 0
        assert conn.execute(select(users.c.last_login_at)).scalar() is None

    cli.main(["disable-user", "--email", "owner@example.org"])
    _prompts(monkeypatch, PASSWORD)
    assert cli.main(["verify-login", "--email", "owner@example.org"]) == 1
    assert "disabled" in capsys.readouterr().out


def test_user_status_reports_a_locked_out_email(env, monkeypatch, capsys):
    _prompts(monkeypatch, PASSWORD, PASSWORD)
    cli.main(["create-user", "--email", "owner@example.org"])
    settings = Settings(
        data_dir=env["tmp"] / "data", max_file_bytes=1_000_000, max_session_bytes=2_000_000, cors_origins=(),
        database_url=env["url"], auth_mode="password", session_secret="s" * 40,
    )
    client = TestClient(create_app(settings))
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "owner@example.org", "password": f"wrong {uuid.uuid4()}"})
    assert client.post("/api/auth/login", json={"email": "owner@example.org", "password": PASSWORD}).status_code == 429
    capsys.readouterr()
    assert cli.main(["user-status", "--email", "owner@example.org"]) == 0
    assert "locked" in capsys.readouterr().out


def test_production_refuses_to_run_account_commands_without_postgres(tmp_path: Path, monkeypatch):
    asked = _prompts(monkeypatch, PASSWORD, PASSWORD)
    monkeypatch.setenv("STUDIO_ENV", "production")
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="production requires DATABASE_URL for PostgreSQL"):
        cli.main(["create-user", "--email", "owner@example.org"])
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'local.db').as_posix()}")
    with pytest.raises(RuntimeError, match="production requires DATABASE_URL for PostgreSQL"):
        cli.main(["create-user", "--email", "owner@example.org"])
    assert asked == [] and not (tmp_path / "local.db").exists()
