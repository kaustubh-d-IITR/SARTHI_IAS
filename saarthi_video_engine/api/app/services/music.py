"""Background music: the bundled library and the track a plan version uses.

Library tracks are files shipped with the app under assets/music_library/, each with its
licence, author and source page in library.json. They were taken only from Wikimedia
Commons files that passed the engine's licence gate (CC0 / CC BY / CC BY-SA / public
domain). The default track is the licensed instrumental the renderer has always used.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import ENGINE_ROOT
from app.errors import StudioError

LIBRARY_DIR = ENGINE_ROOT / "assets" / "music_library"
MANIFEST = LIBRARY_DIR / "library.json"
DEFAULT_RECORD = ENGINE_ROOT / "outputs" / "dr_shivin_phase5" / "asset_cache" / "candidates.json"
CATEGORIES = (
    "inspirational",
    "motivational",
    "cinematic",
    "documentary",
    "calm_educational",
    "serious_current_affairs",
    "energetic",
    "minimal_ambience",
)
PUBLIC = ("track_id", "title", "artist", "category", "duration_seconds", "license", "license_url", "page_url", "default")


def _default_track() -> dict | None:
    if not DEFAULT_RECORD.is_file():
        return None
    for row in json.loads(DEFAULT_RECORD.read_text(encoding="utf-8")):
        if row.get("kind") != "audio" or row.get("rejected"):
            continue
        name = str(row.get("local_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        path = DEFAULT_RECORD.parent / name
        if path.is_file():
            return {
                "track_id": "default-inspire-action",
                "title": str(row.get("title") or "Inspire Action").removeprefix("File:").rsplit(".", 1)[0],
                "artist": row.get("artist") or "",
                "category": "inspirational",
                "duration_seconds": row.get("duration_seconds"),
                "license": row.get("license") or "",
                "license_url": row.get("license_url") or "",
                "page_url": row.get("page_url") or "",
                "file": str(path),
                "default": True,
            }
    return None


def tracks() -> list[dict]:
    rows = []
    default = _default_track()
    if default:
        rows.append(default)
    if MANIFEST.is_file():
        for row in json.loads(MANIFEST.read_text(encoding="utf-8")).get("tracks") or []:
            path = LIBRARY_DIR / row["file"]
            if path.is_file() and row.get("category") in CATEGORIES:
                rows.append({**row, "file": str(path), "default": False})
    return rows


def public_tracks() -> list[dict]:
    return [{key: row.get(key) for key in PUBLIC} for row in tracks()]


def library_track(track_id: str) -> dict:
    for row in tracks():
        if row["track_id"] == track_id:
            return row
    raise StudioError(422, "unknown_track", "That music track is not in the library.")


def track_path(track_id: str) -> Path:
    path = Path(library_track(track_id)["file"])
    if not path.is_file():
        raise StudioError(404, "track_missing", "That music track is missing on the server.")
    return path
