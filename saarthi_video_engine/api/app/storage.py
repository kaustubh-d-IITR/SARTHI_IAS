"""Studio uploads and sessions.

Metadata lives in the database (SQLite locally, PostgreSQL in production). Media and
work files live in object storage under server-built keys and are copied into a
local workspace only when a job needs them.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import get_engine, studio_sessions, uploads, utcnow
from app.errors import StudioError
from app.media_probe import probe_audio, probe_video
from app.names import clean_description, display_filename, require_extension, require_music_extension
from app.objects import ObjectStorage, StorageError, build_storage
from app.workspace import Workspace

CHUNK = 1024 * 1024
CONTENT_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
}
MUSIC_MAX_BYTES = 50 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise StudioError(404, "not_found", f"That {what} was not found.") from exc


class StudioStore:
    def __init__(self, settings: Settings, objects: ObjectStorage | None = None) -> None:
        self.settings = settings
        self.db = get_engine(settings.resolved_database_url)
        self.objects = objects or build_storage(settings)
        self.root = settings.work_root
        self.workspace = Workspace(self.objects, self.root)
        self.uploads = self.root / "uploads"
        self.renders = self.root / "renders"
        self.incoming = self.root / "incoming"

    # ------------------------------------------------------------------ health

    def ensure(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.renders.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ uploads

    @staticmethod
    def _meta(row) -> dict:
        meta = dict(row.meta or {})
        meta.update(
            {
                "upload_id": row.id,
                "kind": row.kind,
                "client_id": row.owner_id,
                "filename": row.filename,
                "object_key": row.object_key,
                "stored_name": row.object_key.rsplit("/", 1)[-1],
                "validation_status": "valid" if row.status == "valid" else row.status,
                "sha256": row.sha256,
            }
        )
        if row.size_bytes is not None:
            meta["size_bytes"] = row.size_bytes
        return meta

    def _row(self, upload_id: str):
        upload_id = _uuid(upload_id, "upload")
        with self.db.connect() as conn:
            row = conn.execute(select(uploads).where(uploads.c.id == upload_id)).first()
        if row is None or row.status in {"deleted"}:
            raise StudioError(404, "not_found", "That upload was not found.")
        return row

    def get_upload(self, upload_id: str, client_id: str) -> dict:
        row = self._row(upload_id)
        if row.owner_id != client_id:
            raise StudioError(404, "not_found", "That upload was not found.")
        return self._meta(row)

    def _insert_upload(self, *, upload_id: str, owner: str, kind: str, status: str, key: str, filename: str, declared: int | None, meta: dict, size: int | None = None, sha256: str | None = None) -> None:
        now = utcnow()
        with self.db.begin() as conn:
            conn.execute(
                insert(uploads).values(
                    id=upload_id, owner_id=owner, kind=kind, status=status, object_key=key, filename=filename,
                    declared_size=declared, size_bytes=size, sha256=sha256, meta=meta, created_at=now, updated_at=now,
                )
            )

    def _update_upload(self, upload_id: str, **values) -> None:
        values["updated_at"] = utcnow()
        with self.db.begin() as conn:
            conn.execute(update(uploads).where(uploads.c.id == upload_id).values(**values))

    def set_upload_sha(self, upload_id: str, sha256: str) -> None:
        self._update_upload(upload_id, sha256=sha256)

    async def save_upload(self, upload: UploadFile, *, client_id: str, kind: str, description: str | None = None) -> dict:
        """Upload through the API. Used locally; production uses direct uploads (initiate/complete)."""
        suffix = require_extension(upload.filename)
        filename = display_filename(upload.filename)
        text = clean_description(description) if kind == "broll" else ""
        upload_id = str(uuid.uuid4())
        self.incoming.mkdir(parents=True, exist_ok=True)
        partial = self.incoming / f"{upload_id}.partial"
        key = f"uploads/{upload_id}/media{suffix}"
        try:
            size, digest = await self._stream(upload, partial)
            if size <= 0:
                raise StudioError(400, "empty_upload", "The file is empty.")
            probed = probe_video(partial)
            if not self.workspace.passthrough:
                self.objects.put_file(key, partial, CONTENT_TYPES.get(suffix, "application/octet-stream"))
            dest = self.workspace.path(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial.replace(dest)
            meta = {
                "size_bytes": size,
                "content_type": CONTENT_TYPES.get(suffix, upload.content_type or "application/octet-stream"),
                "description": text,
                **probed,
                "created_at": _now(),
            }
            self._insert_upload(upload_id=upload_id, owner=client_id, kind=kind, status="valid", key=key, filename=filename, declared=size, meta=meta, size=size, sha256=digest)
            return self.get_upload(upload_id, client_id)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    async def _stream(self, upload: UploadFile, dest: Path) -> tuple[int, str]:
        total = 0
        declared = upload.size
        if isinstance(declared, int) and declared > self.settings.max_file_bytes:
            raise StudioError(413, "file_too_large", "That file is larger than the configured limit.")
        digest = hashlib.sha256()
        with dest.open("wb") as handle:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.settings.max_file_bytes:
                    raise StudioError(413, "file_too_large", "That file is larger than the configured limit.")
                digest.update(chunk)
                handle.write(chunk)
        return total, digest.hexdigest()

    def _limit(self, kind: str) -> int:
        return min(self.settings.max_file_bytes, MUSIC_MAX_BYTES) if kind == "music" else self.settings.max_file_bytes

    def initiate_upload(self, client_id: str, kind: str, filename: str, size_bytes: int, description: str | None) -> dict:
        """Reserve an object key and return a short-lived signed PUT for it."""
        suffix = require_music_extension(filename) if kind == "music" else require_extension(filename)
        display = display_filename(filename)
        text = clean_description(description) if kind == "broll" else ""
        if size_bytes <= 0:
            raise StudioError(400, "empty_upload", "The file is empty.")
        if size_bytes > self._limit(kind):
            raise StudioError(413, "file_too_large", "That file is larger than the configured limit.")
        upload_id = str(uuid.uuid4())
        key = f"uploads/{upload_id}/media{suffix}"
        content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
        meta = {"content_type": content_type, "description": text, "created_at": _now()}
        self._insert_upload(upload_id=upload_id, owner=client_id, kind=kind, status="pending", key=key, filename=display, declared=size_bytes, meta=meta)
        target = self.objects.presign_put(key, self.settings.signed_url_seconds, content_type)
        return {"upload_id": upload_id, "upload": target, "max_bytes": self.settings.max_file_bytes}

    def local_put_target(self, key: str) -> tuple[Path, Path, int]:
        """Local storage only: where a signed PUT may write, and how many bytes it may send."""
        with self.db.connect() as conn:
            row = conn.execute(select(uploads).where(uploads.c.object_key == key)).first()
        if row is None or row.status != "pending":
            raise StudioError(404, "not_found", "That upload was not found.")
        limit = min(self.settings.max_file_bytes, int(row.declared_size or self.settings.max_file_bytes))
        dest = self.objects.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest.with_name(dest.name + ".part"), dest, limit

    def complete_upload(self, upload_id: str, client_id: str) -> dict:
        """Check what actually arrived in storage. Browser-supplied metadata is not trusted."""
        row = self._row(upload_id)
        if row.owner_id != client_id:
            raise StudioError(404, "not_found", "That upload was not found.")
        if row.status == "valid":
            return self._meta(row)
        if row.status != "pending":
            raise StudioError(409, "upload_not_pending", "That upload cannot be completed.")
        info = self.objects.head(row.object_key)
        if info is None:
            raise StudioError(409, "upload_missing", "The file has not reached storage yet.")

        def reject(status: int, code: str, message: str) -> None:
            self._discard(row.object_key)
            self._update_upload(row.id, status="rejected")
            raise StudioError(status, code, message)

        if info.size <= 0:
            reject(400, "empty_upload", "The file is empty.")
        if info.size > self._limit(row.kind):
            reject(413, "file_too_large", "That file is larger than the configured limit.")
        if row.declared_size and info.size != row.declared_size:
            reject(422, "size_mismatch", "The stored file is not the size that was announced.")
        target = str(self.workspace.path(row.object_key)) if self.workspace.passthrough else self.objects.presign_get(row.object_key, 300)
        try:
            probed = probe_audio(target) if row.kind == "music" else probe_video(target)
        except StudioError as exc:
            reject(exc.status, exc.code, exc.message)
        meta = dict(row.meta or {})
        meta.update({"size_bytes": info.size, **probed})
        self._update_upload(row.id, status="valid", size_bytes=info.size, meta=meta)
        return self.get_upload(row.id, client_id)

    def _discard(self, key: str) -> None:
        try:
            self.objects.delete(key)
        except StorageError:
            pass
        local = self.workspace.path(key)
        local.unlink(missing_ok=True)
        try:
            local.parent.rmdir()
        except OSError:
            pass

    def delete_upload(self, upload_id: str, client_id: str) -> None:
        meta = self.get_upload(upload_id, client_id)
        if self._referenced(client_id, meta["upload_id"]):
            raise StudioError(409, "upload_in_session", "That file is already part of a prepared session.")
        self._discard(meta["object_key"])
        with self.db.begin() as conn:
            conn.execute(delete(uploads).where(uploads.c.id == meta["upload_id"]))

    def _referenced(self, client_id: str, upload_id: str) -> bool:
        for data in self.list_session_records(client_id):
            raw = data.get("raw_video") or {}
            if raw.get("upload_id") == upload_id:
                return True
            if any(item.get("asset_id") == upload_id for item in data.get("broll") or []):
                return True
        return False

    def media_file(self, upload_id: str) -> Path:
        """Local path of an upload's media, copied from storage when needed."""
        row = self._row(upload_id)
        path = self.workspace.pull(row.object_key)
        if path is None:
            raise StudioError(404, "media_missing", "The uploaded file is missing from storage.")
        return path

    # ------------------------------------------------------------------ sessions

    def create_session(self, client_id: str, mode: str, raw_video_id: str, broll: list[dict], title: str | None = None) -> dict:
        from app.processing.titles import clean_title, title_record

        title_text = clean_title(title)
        raw = self.get_upload(raw_video_id, client_id)
        if raw.get("kind") != "raw" or raw.get("validation_status") != "valid":
            raise StudioError(422, "raw_video_required", "Choose a validated raw lesson video.")
        if mode == "auto_edit" and broll:
            raise StudioError(422, "mode_does_not_accept_broll", "AI Auto-Edit does not take B-roll uploads. Use Edit with My B-Roll for your own clips.")
        if mode == "my_broll" and not broll:
            raise StudioError(422, "broll_required", "Add at least one B-roll clip and a description of what it shows.")
        seen: set[str] = set()
        assets = []
        total = int(raw["size_bytes"])
        for item in broll:
            asset_id = str(item["asset_id"])
            if asset_id in seen:
                raise StudioError(422, "duplicate_asset", "Each B-roll clip can be added once.")
            seen.add(asset_id)
            meta = self.get_upload(asset_id, client_id)
            if meta.get("kind") != "broll" or meta.get("validation_status") != "valid":
                raise StudioError(422, "invalid_broll", "One of the B-roll clips is not a validated upload.")
            description = clean_description(item.get("description"))
            total += int(meta["size_bytes"])
            if total > self.settings.max_session_bytes:
                raise StudioError(413, "session_too_large", "These files together are larger than the configured session limit.")
            if meta.get("description") != description:
                meta["description"] = description
                self._write_meta(meta)
            assets.append(self.public_asset(meta))
        if int(raw["size_bytes"]) > self.settings.max_session_bytes:
            raise StudioError(413, "session_too_large", "These files together are larger than the configured session limit.")
        session_id = str(uuid.uuid4())
        record = {
            "session_id": session_id,
            "client_id": client_id,
            "mode": mode,
            "status": "prepared",
            "raw_video": self.public_upload(raw),
            "broll": assets,
            "total_bytes": total,
            "note": "Assets are stored. Transcription, matching, and rendering have not started.",
            "created_at": _now(),
            "title": title_record(title_text, "user"),
        }
        self.write_session(record)
        return record

    def write_session(self, record: dict) -> None:
        session_id = _uuid(record["session_id"], "session")
        now = utcnow()
        values = {
            "owner_id": record["client_id"],
            "mode": record["mode"],
            "status": record["status"],
            "plan_version_id": record.get("plan_version"),
            "record": record,
            "updated_at": now,
        }
        with self.db.begin() as conn:
            changed = conn.execute(update(studio_sessions).where(studio_sessions.c.id == session_id).values(**values)).rowcount
            if not changed:
                try:
                    conn.execute(insert(studio_sessions).values(id=session_id, created_at=now, **values))
                except IntegrityError:
                    conn.execute(update(studio_sessions).where(studio_sessions.c.id == session_id).values(**values))

    def get_session(self, session_id: str, client_id: str) -> dict:
        session_id = _uuid(session_id, "session")
        with self.db.connect() as conn:
            row = conn.execute(select(studio_sessions.c.owner_id, studio_sessions.c.record).where(studio_sessions.c.id == session_id)).first()
        if row is None or row.owner_id != client_id:
            raise StudioError(404, "not_found", "That session was not found.")
        return dict(row.record)

    def session_owner(self, session_id: str) -> str | None:
        session_id = _uuid(session_id, "session")
        with self.db.connect() as conn:
            row = conn.execute(select(studio_sessions.c.owner_id).where(studio_sessions.c.id == session_id)).first()
        return row.owner_id if row else None

    def list_session_records(self, client_id: str | None = None) -> list[dict]:
        query = select(studio_sessions.c.record).order_by(studio_sessions.c.created_at.desc())
        if client_id is not None:
            query = query.where(studio_sessions.c.owner_id == client_id)
        with self.db.connect() as conn:
            return [dict(row.record) for row in conn.execute(query)]

    def work_dir(self, session_id: str) -> Path:
        session_id = _uuid(session_id, "session")
        path = self.root / "work" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pull_work(self, session_id: str, include_footage: bool = False) -> Path:
        """Bring a session's work files into the workspace. Footage clips are skipped unless asked for."""
        session_id = _uuid(session_id, "session")
        prefix = f"work/{session_id}/"
        exclude = () if include_footage else (f"{prefix}footage/", f"{prefix}library.sqlite")
        self.workspace.pull_prefix(prefix, exclude)
        return self.work_dir(session_id)

    def push_work(self, session_id: str) -> None:
        session_id = _uuid(session_id, "session")
        self.workspace.push_prefix(f"work/{session_id}/", (f"work/{session_id}/library.sqlite",))

    def _write_meta(self, meta: dict) -> None:
        with self.db.connect() as conn:
            row = conn.execute(select(uploads).where(uploads.c.id == meta["upload_id"])).first()
        stored = dict(row.meta or {})
        stored["description"] = meta.get("description") or ""
        self._update_upload(meta["upload_id"], meta=stored)

    @staticmethod
    def public_upload(meta: dict) -> dict:
        return {
            "upload_id": meta["upload_id"],
            "filename": meta["filename"],
            "size_bytes": meta.get("size_bytes"),
            "content_type": meta.get("content_type") or "application/octet-stream",
            "duration_seconds": meta.get("duration_seconds"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "frame_rate": meta.get("frame_rate"),
            "validation_status": meta["validation_status"],
        }

    @staticmethod
    def public_asset(meta: dict) -> dict:
        body = StudioStore.public_upload(meta)
        body["asset_id"] = meta["upload_id"]
        body["description"] = meta.get("description") or ""
        return body

