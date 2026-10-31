"""Session job. One thread owns one session. The request that queues it returns immediately."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from app.errors import StudioError
from app.processing.matching import CONFIG as MATCHING_CONFIG
from app.processing.matching import match_assets
from app.processing.plan_adapt import (
    assets_from_engine,
    assets_from_user,
    build_engine_plan,
    lesson_duration,
    public_plan,
    reconcile,
    validate_envelope,
    write_footage_index,
)
from app.processing.titles import suggest_title, title_record
from app.processing.transcribe import elevenlabs_provider, transcribe_upload
from app.processing.user_broll import catalogue_asset
from app.processing.versions import list_versions, load_version, save_version
from app.services.visual.analysis import AssetInput, choose_provider
from app.storage import StudioStore

ACTIVE = {
    "queued",
    "transcribing",
    "analysing",
    "analysing_footage",
    "selecting_footage",
    "building_plan",
    "validating_plan",
}
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(session_id: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(session_id, threading.Lock())


def session_lock(session_id: str) -> threading.Lock:
    return _lock(session_id)


class Services:
    def __init__(self, transcribe=None, select_auto=None, visual=None) -> None:
        self.transcribe = transcribe or _default_transcribe
        self.select_auto = select_auto or _default_select
        self.visual = visual or choose_provider


def _default_transcribe(path: Path, cache_dir: Path) -> dict:
    return transcribe_upload(path, cache_dir, elevenlabs_provider)


def _default_select(transcript: dict, work: Path) -> list[dict]:
    """Existing Commons selector. Ranker is omitted so CLIP weights are not downloaded."""
    import sys

    from app.config import ENGINE_ROOT

    src = str(ENGINE_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from assets.footage import select_clips
    from assets.library import Catalogue

    speech = " ".join(
        (segment.get("original_text") or segment.get("text") or "") for segment in transcript.get("segments") or []
    )
    cache = work / "footage"
    catalogue = Catalogue(work / "library.sqlite")
    try:
        chosen, _log = select_clips("lesson", transcript, speech, cache, catalogue, ranker=None, sleep=lambda _seconds: None)
    finally:
        catalogue.close()
    return chosen


def recover_interrupted(store: StudioStore, only: set[str] | None = None) -> None:
    """Inline runner only: jobs ran in the API process, so a restart lost them. Mark them failed for retry."""
    for record in store.list_session_records():
        if record.get("status") not in ACTIVE or (only is not None and record["session_id"] not in only):
            continue
        if record.get("rematch_of"):
            record["status"] = "plan_ready"
            record["stage"] = "plan_ready"
            record["rematch_error"] = "Matching again was interrupted. The previous plan version is unchanged."
            record.pop("rematch_of", None)
        else:
            record["status"] = "failed"
            record["stage"] = "failed"
            record["error"] = "Processing was interrupted before the plan was saved."
            record["note"] = "Processing failed. No video was rendered."
        store.write_session(record)


RENDER_ACTIVE = {"render_queued", "preparing", "composing", "rendering", "verifying", "assembling_outro"}


def queue_processing(store: StudioStore, session_id: str, client_id: str, services: Services, rematch: bool = False, enqueue=None) -> dict:
    """Check the session can be processed, reserve the job (enqueue), then mark the session queued."""
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        if rematch:
            if record["status"] != "plan_ready":
                raise StudioError(409, "plan_not_ready", "Footage can be matched again only after a plan is ready.")
            if (record.get("render") or {}).get("status") in RENDER_ACTIVE:
                raise StudioError(409, "render_in_progress", "Wait for the current render to finish.")
            record["rematch_of"] = record.get("plan_version")
        else:
            if record["status"] in ACTIVE or record["status"] == "plan_ready":
                raise StudioError(409, "already_processing", "This session is already processing or already has a plan.")
            if record["status"] not in {"prepared", "failed"}:
                raise StudioError(409, "not_ready", "This session cannot be processed.")
            record.pop("rematch_of", None)
        if enqueue is not None:
            record["job_id"] = enqueue()
        record["status"] = "queued"
        record["stage"] = "queued"
        record["error"] = ""
        record["note"] = "Processing is queued. No video is being rendered."
        store.write_session(record)
    return record


def run_processing(store: StudioStore, session_id: str, client_id: str, services: Services, publish: Callable[[], None] | None = None) -> None:
    """publish, when given, copies the session's files to object storage before plan_ready is written."""
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        if record["status"] != "queued":
            return
    try:
        _run(store, session_id, client_id, services, publish)
    except Exception as exc:
        message = str(exc) or "Processing failed."
        if ":\\" in message or "://" in message:
            message = "Processing failed."
        _fail(store, session_id, client_id, message[:300])


def _advance(store: StudioStore, session_id: str, client_id: str, status: str, note: str) -> dict:
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        record["status"] = status
        record["stage"] = status
        record["note"] = note
        store.write_session(record)
        return record


def _fail(store: StudioStore, session_id: str, client_id: str, message: str) -> None:
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        if record.get("rematch_of"):
            record["status"] = "plan_ready"
            record["stage"] = "plan_ready"
            record["error"] = ""
            record["rematch_error"] = message
            record["note"] = "Matching again failed. The previous plan version is unchanged."
            record.pop("rematch_of", None)
        else:
            record["status"] = "failed"
            record["stage"] = "failed"
            record["error"] = message
            record["note"] = "Processing failed. No video was rendered."
        store.write_session(record)
        path = store.work_dir(session_id) / "diagnostics.json"
        previous = []
        if path.is_file():
            previous = json.loads(path.read_text(encoding="utf-8")).get("events") or []
        previous.append({"status": "failed", "message": message})
        path.write_text(json.dumps({"events": previous}, indent=2), encoding="utf-8")


def _run(store: StudioStore, session_id: str, client_id: str, services: Services, publish: Callable[[], None] | None = None) -> None:
    record = _advance(store, session_id, client_id, "transcribing", "Transcribing the lesson.")
    source = store.media_file(record["raw_video"]["upload_id"])
    cache = store.root / "transcript_cache"
    transcript = services.transcribe(source, cache)
    work = store.work_dir(session_id)
    (work / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        record["transcript_meta"] = {
            "source_sha256": transcript.get("source_sha256"),
            "model": transcript.get("model"),
            "method": transcript.get("method"),
            "reused": bool(transcript.get("reused")),
        }
        store.write_session(record)

    _advance(store, session_id, client_id, "analysing", "Reading the transcript.")
    record = store.get_session(session_id, client_id)
    duration = lesson_duration(transcript, record["raw_video"].get("duration_seconds"))
    if duration <= 0:
        raise RuntimeError("The lesson duration could not be read.")
    catalogue: list[dict] = []
    evaluations: list[dict] = []
    analysis_info: dict = {}
    if record["mode"] == "auto_edit":
        _advance(store, session_id, client_id, "selecting_footage", "Choosing licensed footage with the existing pipeline.")
        rows = services.select_auto(transcript, work)
        assets, placements = assets_from_engine(rows or [])
        write_footage_index(work, rows or [])
    else:
        _advance(store, session_id, client_id, "analysing_footage", "Sampling and analysing frames from your clips.")
        for item in record.get("broll") or []:
            meta = store.get_upload(item["upload_id"], client_id)
            catalogue.append(catalogue_asset(meta, store.media_file(meta["upload_id"])))
            if catalogue[-1]["sha256"] != meta.get("sha256"):
                store.set_upload_sha(meta["upload_id"], catalogue[-1]["sha256"])
        batch, analysis_info = _analyse_footage(store, transcript, catalogue, services)
        _advance(store, session_id, client_id, "selecting_footage", "Matching clips to the speech.")
        placements, evaluations = match_assets(transcript, catalogue, batch, duration)
        assets = assets_from_user(placements)
    (work / "catalogue.json").write_text(
        json.dumps({"assets": [_private_catalogue(item) for item in catalogue], "evaluations": evaluations, "analysis": analysis_info}, indent=2),
        encoding="utf-8",
    )
    _advance(store, session_id, client_id, "building_plan", "Building the editing plan.")
    record = store.get_session(session_id, client_id)
    title = _title_for(record, transcript)
    engine_plan = build_engine_plan(transcript, assets, duration, title["text"])
    placements, dropped = reconcile(placements, engine_plan)
    _advance(store, session_id, client_id, "validating_plan", "Checking timestamps and asset references.")
    validation = validate_envelope(record, engine_plan, placements, catalogue)
    envelope = public_plan(record, engine_plan, placements, validation, catalogue, evaluations, dropped)
    envelope["visual_analysis"] = analysis_info
    origin = "rematch" if record.get("rematch_of") else "generated"
    version = save_version(
        work,
        session=record,
        engine_plan=engine_plan,
        studio=envelope,
        origin=origin,
        parent_version_id=record.get("rematch_of"),
        source_hashes=_source_hashes(record, transcript, catalogue, work),
        matching_config=MATCHING_CONFIG if record["mode"] == "my_broll" else {"matcher": "existing select_clips", "ranker": "none"},
        title=title,
        make_current=bool(validation["ok"]),
    )
    if not validation["ok"]:
        _fail(store, session_id, client_id, "; ".join(validation["errors"][:8]) or "The editing plan failed validation.")
        return
    if publish is not None:
        publish()
    with _lock(session_id):
        record = store.get_session(session_id, client_id)
        _make_current(record, version, transcript)
        record.pop("rematch_of", None)
        record.pop("rematch_error", None)
        record["status"] = "plan_ready"
        record["stage"] = "plan_ready"
        record["error"] = ""
        record["note"] = "The editing plan is ready for review. No video has been rendered."
        store.write_session(record)


def _make_current(record: dict, version: dict, transcript: dict) -> None:
    studio = version["studio"]
    pictures = [row for row in studio["picture_decisions"] if row["picture_role"] in {"show_teacher", "show_broll"}]
    record["plan_version"] = version["version_id"]
    record.setdefault("plan_versions", []).append(
        {key: version.get(key) for key in ("version_id", "number", "created_at", "parent_version_id", "origin", "validation_ok")}
    )
    record["counts"] = {
        "segments": len(transcript.get("segments") or []),
        "broll_supplied": len(record.get("broll") or []),
        "candidate_matches": sum(1 for row in studio.get("evaluations") or [] if row.get("status") == "selected") if record["mode"] == "my_broll" else len(studio.get("placements") or []),
        "approved_placements": sum(1 for row in pictures if row["picture_role"] == "show_broll"),
        "teacher_intervals": sum(1 for row in pictures if row["picture_role"] == "show_teacher"),
        "validation": "passed",
    }


def _title_for(record: dict, transcript: dict) -> dict:
    current = record.get("title") or {}
    text = current.get("text") or ""
    title = title_record(text, current.get("source") or "user")
    title["suggestion"] = None if text else suggest_title(transcript)
    return title


def _private_catalogue(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "visual_analysis"} | {
        "visual_analysis": {key: value for key, value in (item.get("visual_analysis") or {}).items() if key != "image_vectors"}
        if isinstance(item.get("visual_analysis"), dict)
        else item.get("visual_analysis")
    }


def _source_hashes(record: dict, transcript: dict, catalogue: list[dict], work: Path) -> dict:
    hashes = {"raw_lesson": transcript.get("source_sha256"), "broll": {item["asset_id"]: item.get("sha256") for item in catalogue}}
    index = work / "footage_index.json"
    if record["mode"] == "auto_edit" and index.is_file():
        hashes["footage"] = {row["asset_id"]: row.get("sha256") for row in json.loads(index.read_text(encoding="utf-8")).get("assets") or []}
    return hashes


def _analyse_footage(store: StudioStore, transcript: dict, catalogue: list[dict], services: Services):
    """Sample and analyse each clip of this session, sequentially, reusing results by file hash."""
    from app.processing.matching import build_windows

    provider = services.visual()
    available, reason = provider.availability()
    windows = build_windows(transcript)
    descriptions = [item["description"] for item in catalogue if item.get("description")]
    texts = list(dict.fromkeys([window.text for window in windows] + descriptions))
    inputs = [
        AssetInput(item["asset_id"], item["sha256"], store.media_file(item["asset_id"]), float(item.get("duration_seconds") or 0.0), item.get("description") or "", item.get("filename") or "")
        for item in catalogue
    ]
    root = store.root / "visual"
    root.mkdir(parents=True, exist_ok=True)
    batch = provider.analyse(root, inputs, texts, texts)
    for item in catalogue:
        record = batch.analyses.get(item["asset_id"]) or {}
        item["visual_analysis"] = record
    info = {
        "provider": provider.name,
        "model": provider.model,
        "available": available,
        "unavailable_reason": "" if available else reason,
        "text_model": "Snowflake/snowflake-arctic-embed-xs" if batch.embed_text else None,
        "errors": batch.errors,
        "performed": sum(1 for item in catalogue if (item.get("visual_analysis") or {}).get("performed")),
        "assets": len(catalogue),
    }
    return batch, info


def public_transcript(payload: dict) -> dict:
    segments = []
    for segment in payload.get("segments") or []:
        segments.append(
            {
                "id": segment.get("id"),
                "text": segment.get("text"),
                "original_text": segment.get("original_text"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "timing_precision": segment.get("timing_precision"),
                "confidence": segment.get("confidence"),
                "words": segment.get("words") or [],
            }
        )
    return {
        "language": payload.get("language"),
        "method": payload.get("method"),
        "model": payload.get("model"),
        "processed_at": payload.get("processed_at"),
        "source_sha256": payload.get("source_sha256"),
        "reused": bool(payload.get("reused")),
        "translated_to_english": payload.get("translated_to_english"),
        "segments": segments,
    }


def read_transcript(store: StudioStore, session_id: str, client_id: str) -> dict:
    store.get_session(session_id, client_id)
    path = store.pull_work(session_id) / "transcript.json"
    if not path.is_file():
        from app.errors import StudioError

        raise StudioError(404, "transcript_missing", "This session has no transcript yet.")
    return public_transcript(json.loads(path.read_text(encoding="utf-8")))


def read_plan(store: StudioStore, session_id: str, client_id: str) -> dict:
    record = store.get_session(session_id, client_id)
    if record["status"] != "plan_ready":
        raise StudioError(404, "plan_missing", "This session has no ready editing plan.")
    work = store.pull_work(session_id)
    if record.get("plan_version"):
        return load_version(work, record["plan_version"])["studio"]
    path = work / "editing_plan.json"
    if not path.is_file():
        raise StudioError(404, "plan_missing", "This session has no ready editing plan.")
    return json.loads(path.read_text(encoding="utf-8"))["studio"]


def read_versions(store: StudioStore, session_id: str, client_id: str) -> dict:
    record = store.get_session(session_id, client_id)
    rows = list_versions(store.pull_work(session_id))
    renders = {}
    for item in record.get("render_history") or []:
        if item.get("plan_version_id"):
            renders.setdefault(item["plan_version_id"], []).append({"render_id": item["render_id"], "status": item.get("status")})
    for row in rows:
        row["renders"] = renders.get(row["version_id"], [])
    return {"current_version_id": record.get("plan_version"), "versions": rows}


def read_version(store: StudioStore, session_id: str, client_id: str, version_id: str) -> dict:
    store.get_session(session_id, client_id)
    return load_version(store.pull_work(session_id), version_id)["studio"]


def thumbnail_for(store: StudioStore, session_id: str, client_id: str, asset_id: str) -> Path:
    """The cached thumbnail of a clip that belongs to this session."""
    from app.services.visual.analysis import thumbnail_path

    record = store.get_session(session_id, client_id)
    if asset_id not in {item["upload_id"] for item in record.get("broll") or []}:
        raise StudioError(404, "not_found", "That clip is not part of this session.")
    path = store.pull_work(session_id) / "catalogue.json"
    assets = json.loads(path.read_text(encoding="utf-8")).get("assets") or [] if path.is_file() else []
    sha = next((item.get("sha256") for item in assets if item.get("asset_id") == asset_id), None)
    if sha:
        store.workspace.pull(f"visual/{sha}/thumb.jpg")
    thumb = thumbnail_path(store.root / "visual", sha) if sha else None
    if thumb is None:
        raise StudioError(404, "thumbnail_missing", "No thumbnail was made for this clip.")
    return thumb
