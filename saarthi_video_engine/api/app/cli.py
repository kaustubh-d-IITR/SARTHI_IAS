"""Owner commands. Passwords are typed at a prompt; they are never arguments or log lines.

    python -m app.cli migrate
    python -m app.cli check-schema
    python -m app.cli create-user --email manager@example.org --name "Manager"
    python -m app.cli set-password --email manager@example.org
    python -m app.cli disable-user --email manager@example.org
    python -m app.cli enable-user --email manager@example.org
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.auth import create_user, set_active, set_password
from app.config import Settings
from app.db import database_label, get_engine, migrate, schema_state
from app.errors import StudioError


def _password() -> str:
    first = getpass.getpass("Password (at least 12 characters): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("The passwords do not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("check-schema")
    for name in ("create-user", "set-password", "disable-user", "enable-user"):
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
    try:
        if args.command == "create-user":
            user_id = create_user(db, args.email, _password(), args.name)
            print(f"Created user {user_id}.")
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
