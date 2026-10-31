"""Owner commands. Passwords are typed at a prompt; they are never arguments or log lines.

    python -m app.cli migrate
    python -m app.cli check-schema
    python -m app.cli create-user --email manager@example.org --name "Manager"
    python -m app.cli user-status --email manager@example.org
    python -m app.cli verify-login --email manager@example.org
    python -m app.cli set-password --email manager@example.org
    python -m app.cli disable-user --email manager@example.org
    python -m app.cli enable-user --email manager@example.org

Every account command first prints the database it is using (host and name only).
user-status and verify-login only read: verify-login checks the typed password against
the stored scrypt hash without signing in, so it creates no session and does not count
towards the sign-in lockout.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from app.auth import LOCKOUT_MINUTES, _recent_failures, create_user, normalize_email, set_active, set_password, verify_password
from app.config import Settings
from app.db import database_label, get_engine, migrate, schema_state, users
from app.errors import StudioError


def _password() -> str:
    first = getpass.getpass("Password (at least 12 characters): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("The passwords do not match.")
    return first


def _account(db, email: str):
    with db.connect() as conn:
        return conn.execute(select(users.c.id, users.c.is_active, users.c.created_at, users.c.last_login_at, users.c.password_hash).where(users.c.email == email)).first()


def _user_status(db, email: str, max_failures: int) -> int:
    with db.connect() as conn:
        total = conn.execute(select(func.count()).select_from(users)).scalar() or 0
    row = _account(db, email)
    print(f"Accounts in this database: {total}.")
    if row is None:
        print(f"No account for {email}. Create it with: python -m app.cli create-user --email {email}")
        return 1
    last = row.last_login_at.isoformat(timespec="minutes") if row.last_login_at else "never"
    print(f"Account {email}: {'active' if row.is_active else 'disabled'}, created {row.created_at.isoformat(timespec='minutes')}, last sign-in {last}.")
    failures = _recent_failures(db, email)
    if failures >= max_failures:
        print(f"Sign-in is locked for this email after {failures} failed attempts; it unlocks {LOCKOUT_MINUTES} minutes after the last one.")
    return 0


def _verify_login(db, email: str) -> int:
    row = _account(db, email)
    password = getpass.getpass("Password to check: ")
    if row is None:
        print(f"No account for {email}.")
        return 1
    if not verify_password(password, row.password_hash):
        print("The password does not match this account.")
        return 1
    if not row.is_active:
        print("The password matches, but the account is disabled. Run enable-user.")
        return 1
    print("The password matches and the account is active. Sign-in will succeed unless the email is locked out (see user-status).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("check-schema")
    for name in ("create-user", "user-status", "verify-login", "set-password", "disable-user", "enable-user"):
        command = sub.add_parser(name)
        command.add_argument("--email", required=True)
        if name == "create-user":
            command.add_argument("--name", default="")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    url = settings.resolved_database_url
    if args.command in ("migrate", "check-schema"):
        if args.command == "migrate":
            migrate(url)
        state = schema_state(get_engine(url))
        print(f"Database {database_label(url)}: revision {state['revision']}, code expects {state['head']}, missing tables {state['missing_tables'] or 'none'}.")
        return 0 if state["ok"] else 1
    if settings.auto_migrate:
        migrate(url)
    db = get_engine(url)
    print(f"Database: {database_label(url)} ({settings.environment})")
    try:
        if args.command == "create-user":
            user_id = create_user(db, args.email, _password(), args.name)
            print(f"Created user {user_id}.")
        elif args.command == "user-status":
            return _user_status(db, normalize_email(args.email), settings.login_attempts_per_window)
        elif args.command == "verify-login":
            return _verify_login(db, normalize_email(args.email))
        elif args.command == "set-password":
            set_password(db, args.email, _password())
            print("Password changed. Existing sign-ins for that user were ended.")
        elif args.command == "disable-user":
            set_active(db, args.email, False)
            print("User disabled and signed out.")
        elif args.command == "enable-user":
            set_active(db, args.email, True)
            print("User enabled.")
    except StudioError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
