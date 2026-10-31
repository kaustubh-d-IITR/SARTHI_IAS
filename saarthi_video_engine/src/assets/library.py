"""Local catalogue of clips that already passed a license check.

A later lesson looks here before asking Commons for the same file again.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    provider TEXT,
    page_url TEXT,
    download_url TEXT,
    local_path TEXT,
    license TEXT,
    license_url TEXT,
    artist TEXT,
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    codec TEXT,
    sha256 TEXT,
    description TEXT,
    tags TEXT,
    subjects TEXT,
    thumbnails TEXT,
    embedding TEXT,
    quality REAL,
    relevance REAL
);
CREATE TABLE IF NOT EXISTS usage (
    asset_id TEXT,
    lesson TEXT,
    video_id TEXT,
    start_seconds REAL,
    end_seconds REAL
);
"""


class Catalogue:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def upsert(self, row: dict) -> None:
        asset_id = row.get("page_url") or row.get("sha256") or row.get("local_path")
        if not asset_id:
            return
        payload = {
            "asset_id": asset_id,
            "provider": row.get("provider") or "wikimedia",
            "page_url": row.get("page_url") or "",
            "download_url": row.get("download_url") or "",
            "local_path": row.get("local_path") or "",
            "license": row.get("license") or "",
            "license_url": row.get("license_url") or "",
            "artist": row.get("artist") or "",
            "duration_seconds": row.get("duration_seconds"),
            "width": row.get("width"),
            "height": row.get("height"),
            "codec": row.get("codec") or "",
            "sha256": row.get("sha256") or "",
            "description": row.get("description") or row.get("title") or "",
            "tags": json.dumps(row.get("tags") or []),
            "subjects": json.dumps(row.get("subjects") or []),
            "thumbnails": json.dumps(row.get("thumbnails") or []),
            "embedding": json.dumps(row.get("embedding") or []),
            "quality": row.get("quality"),
            "relevance": row.get("relevance"),
        }
        columns = ", ".join(payload)
        marks = ", ".join(f":{name}" for name in payload)
        updates = ", ".join(f"{name}=excluded.{name}" for name in payload if name != "asset_id")
        self._db.execute(
            f"INSERT INTO assets ({columns}) VALUES ({marks}) ON CONFLICT(asset_id) DO UPDATE SET {updates}",
            payload,
        )
        self._db.commit()

    def find_by_hash(self, digest: str) -> dict | None:
        found = self._db.execute("SELECT * FROM assets WHERE sha256 = ?", (digest,)).fetchone()
        return dict(found) if found else None

    def find_by_page(self, page_url: str) -> dict | None:
        found = self._db.execute("SELECT * FROM assets WHERE page_url = ?", (page_url,)).fetchone()
        return dict(found) if found else None

    def search_tag(self, tag: str) -> list[dict]:
        needle = f"%{tag}%"
        rows = self._db.execute(
            "SELECT * FROM assets WHERE tags LIKE ? OR subjects LIKE ? OR description LIKE ?",
            (needle, needle, needle),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_use(self, asset_id: str, lesson: str, video_id: str, start: float, end: float) -> None:
        self._db.execute(
            "INSERT INTO usage (asset_id, lesson, video_id, start_seconds, end_seconds) VALUES (?, ?, ?, ?, ?)",
            (asset_id, lesson, video_id, start, end),
        )
        self._db.commit()

    def uses(self, asset_id: str) -> list[dict]:
        rows = self._db.execute("SELECT * FROM usage WHERE asset_id = ?", (asset_id,)).fetchall()
        return [dict(row) for row in rows]
