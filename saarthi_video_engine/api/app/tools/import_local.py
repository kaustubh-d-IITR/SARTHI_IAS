"""Import sessions made before the database existed. Run by hand only; nothing calls it automatically.

The older studio kept uploads/<id>/meta.json, sessions/<id>.json, work/ and renders/
under one data directory. This copies those files into the configured object storage
under the same keys and writes the database rows. The source directory is only read.

Without --apply it lists what it would do and changes nothing.

    python -m app.tools.import_local --source ../data/studio
    python -m app.tools.import_local --source ../data/studio --owner-email manager@example.org --apply

--owner-email assigns every imported upload and session to that account (password
mode). Without it each record keeps its browser client id (development mode).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import inspect, select

from app.config import Settings
from app.db import migrate, studio_sessions, uploads, users
from app.objects import StorageError, check_key
from app.storage import CONTENT_TYPES, StudioStore

IDENTITY = {"upload_id", "kind", "client_id", "filename", "stored_name", "validation_status", "object_key", "sha256"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower()) or {".json": "application/json", ".jpg": "image/jpeg", ".png": "image/png", ".wav": "audio/wav"}.get(path.suffix.lower(), "application/octet-stream")


class Importer:
    def __init__(self, store: StudioStore, source: Path, owner: str | None, apply: bool) -> None:
        self.store = store
        self.source = source
        self.owner = owner
        self.apply = apply
        self.counts = {"uploads": 0, "sessions": 0, "files": 0, "skipped": 0}
        self.skipped_keys: list[str] = []
        self.unmigrated = "studio_sessions" not in inspect(store.db).get_table_names()

    def say(self, text: str) -> None:
        print(("" if self.apply else "[dry run] ") + text)

    def _exists(self, table, row_id: str) -> bool:
        if self.unmigrated:
            return False
        with self.store.db.connect() as conn:
            return conn.execute(select(table.c.id).where(table.c.id == row_id)).first() is not None

    def _put(self, key: str, path: Path) -> None:
        try:
            check_key(key)
        except StorageError:
            self.skipped_keys.append(key)
            return
        self.counts["files"] += 1
        if self.apply:
            self.store.objects.put_file(key, path, _content_type(path))

    def _put_tree(self, prefix: str) -> None:
        folder = self.source / prefix
        if not folder.is_dir():
            return
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                self._put(path.relative_to(self.source).as_posix(), path)

    def uploads(self) -> None:
        for meta_path in sorted((self.source / "uploads").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            upload_id = meta["upload_id"]
            if self._exists(uploads, upload_id):
                self.counts["skipped"] += 1
                continue
            media = meta_path.parent / meta.get("stored_name", "")
            if not media.is_file():
                self.say(f"upload {upload_id}: media file missing, not imported")
                continue
            key = f"uploads/{upload_id}/{media.name}"
            size = media.stat().st_size
            self.say(f"upload {upload_id} ({meta.get('kind')}, {size} bytes) -> {key}")
            self._put(key, media)
            self.counts["uploads"] += 1
            if self.apply:
                extra = {name: value for name, value in meta.items() if name not in IDENTITY}
                status = "valid" if meta.get("validation_status") == "valid" else "rejected"
                self.store._insert_upload(
                    upload_id=upload_id, owner=self.owner or meta["client_id"], kind=meta["kind"], status=status, key=key,
                    filename=meta.get("filename") or media.name, declared=size, meta=extra, size=size, sha256=_sha256(media),
                )

    def sessions(self) -> None:
        from app.processing.jobs import recover_interrupted
        from app.services.rendering.jobs import ACTIVE as RENDER_ACTIVE, mark_render_failed
        from app.worker import sync_index

        imported: set[str] = set()
        for path in sorted((self.source / "sessions").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            session_id = record["session_id"]
            if self._exists(studio_sessions, session_id):
                self.counts["skipped"] += 1
                continue
            self.say(f"session {session_id} ({record.get('mode')}, {record.get('status')})")
            for prefix in (f"work/{session_id}", f"renders/{session_id}"):
                self._put_tree(prefix)
            self.counts["sessions"] += 1
            if not self.apply:
                continue
            if self.owner:
                record["client_id"] = self.owner
            self.store.write_session(record)
            imported.add(session_id)
            self.store.pull_work(session_id)
            sync_index(self.store, session_id, record["client_id"])
            render = record.get("render") or {}
            if render.get("status") in RENDER_ACTIVE:
                mark_render_failed(self.store, session_id, record["client_id"], render.get("render_id"), "Rendering was interrupted before the import.")
        if imported:
            # A session imported mid-job has no worker behind it; mark it failed so it can be retried.
            recover_interrupted(self.store, only=imported)

    def caches(self) -> None:
        for prefix in ("transcript_cache", "visual"):
            self._put_tree(prefix)


def _owner_id(store: StudioStore, email: str | None) -> str | None:
    if not email:
        return None
    if "users" not in inspect(store.db).get_table_names():
        raise SystemExit("The database has no accounts yet. Run python -m app.cli migrate and create-user first.")
    from app.auth import normalize_email

    with store.db.connect() as conn:
        row = conn.execute(select(users.c.id).where(users.c.email == normalize_email(email))).first()
    if row is None:
        raise SystemExit("No account with that email. Create it first with python -m app.cli create-user.")
    return row.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="The older studio data directory (holds uploads/ and sessions/).")
    parser.add_argument("--owner-email", help="Assign everything imported to this account.")
    parser.add_argument("--apply", action="store_true", help="Write to storage and the database. Without it nothing changes.")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    if not (source / "uploads").is_dir() and not (source / "sessions").is_dir():
        raise SystemExit(f"{source} has no uploads/ or sessions/ folder.")
    settings = Settings.from_env()
    if args.apply:
        migrate(settings.resolved_database_url)
    store = StudioStore(settings)
    importer = Importer(store, source, _owner_id(store, args.owner_email), args.apply)
    importer.uploads()
    importer.sessions()
    importer.caches()
    c = importer.counts
    print(f"{'imported' if args.apply else 'would import'}: {c['uploads']} uploads, {c['sessions']} sessions, {c['files']} files; already present: {c['skipped']}")
    if importer.skipped_keys:
        print(f"{len(importer.skipped_keys)} files have names storage keys do not allow and were left out, e.g. {importer.skipped_keys[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
