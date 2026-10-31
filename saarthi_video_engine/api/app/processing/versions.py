"""Immutable editing-plan versions under work/<session>/plans/.

editing_plan.json stays as a copy of the current version so older readers keep
working. A version file is never rewritten.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.errors import StudioError


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def plans_dir(work: Path) -> Path:
    folder = work / "plans"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def version_meta(version: dict) -> dict:
    return {key: version.get(key) for key in ("version_id", "number", "created_at", "parent_version_id", "origin", "mode", "validation_ok", "title")}


def save_version(
    work: Path,
    *,
    session: dict,
    engine_plan: dict,
    studio: dict,
    origin: str,
    parent_version_id: str | None,
    source_hashes: dict,
    matching_config: dict,
    title: dict,
    make_current: bool,
    settings: dict | None = None,
) -> dict:
    folder = plans_dir(work)
    number = len(list(folder.glob("*.json"))) + 1
    version_id = f"v{number:03d}-{uuid.uuid4().hex[:8]}"
    validation = studio.get("validation") or {"ok": False, "errors": ["missing validation"]}
    studio = dict(studio)
    studio["plan_version"] = {"version_id": version_id, "number": number, "parent_version_id": parent_version_id, "origin": origin}
    studio["title"] = title
    if settings is not None:
        studio["settings"] = settings
    version = {
        "version_id": version_id,
        "number": number,
        "created_at": _now(),
        "parent_version_id": parent_version_id,
        "origin": origin,
        "mode": session["mode"],
        "session_id": session["session_id"],
        "source_hashes": source_hashes,
        "matching_config": matching_config,
        "title": title,
        "validation_ok": bool(validation.get("ok")),
        "validation": validation,
        "engine_plan": engine_plan,
        "studio": studio,
    }
    if settings is not None:
        version["settings"] = settings
    path = folder / f"{version_id}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(version, handle, indent=2, ensure_ascii=False)
    if make_current:
        (work / "editing_plan.json").write_text(
            json.dumps({"engine_plan": engine_plan, "studio": studio, "version_id": version_id}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return version


def load_version(work: Path, version_id: str) -> dict:
    if not version_id or "/" in version_id or "\\" in version_id or ".." in version_id:
        raise StudioError(404, "plan_version_missing", "That editing-plan version was not found.")
    path = work / "plans" / f"{version_id}.json"
    if not path.is_file():
        raise StudioError(404, "plan_version_missing", "That editing-plan version was not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_versions(work: Path) -> list[dict]:
    folder = work / "plans"
    if not folder.is_dir():
        return []
    rows = [version_meta(json.loads(path.read_text(encoding="utf-8"))) for path in folder.glob("*.json")]
    return sorted(rows, key=lambda row: row.get("number") or 0)
