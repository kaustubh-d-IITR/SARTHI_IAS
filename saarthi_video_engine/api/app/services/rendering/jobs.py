"""Render lifecycle. One render per session at a time; every attempt gets a new folder."""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.errors import StudioError
from app.processing.jobs import session_lock
from app.services.rendering.adapter import RenderError, load_approved, render_attempt, resolve_inputs
from app.services.rendering.bridge import EngineBridge, EngineFailure
from app.storage import StudioStore

ACTIVE = {"render_queued", "preparing", "composing", "rendering", "verifying", "assembling_outro"}
PUBLIC_KEYS = (
    "render_id",
    "status",
    "stage",
    "message",
    "error",
    "created_at",
    "completed_at",
    "failed_at",
    "metadata",
    "lesson_duration_seconds",
    "audio_verification",
    "outro",
    "approved_broll_placements",
    "plan_version_id",
    "title",
    "quality",
    "studio_settings",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def public_render(state: dict | None) -> dict | None:
    if not state:
        return None
    return {key: state[key] for key in PUBLIC_KEYS if key in state}


def queue_render(store: StudioStore, session_id: str, owner: str, enqueue=None) -> dict:
    """Check the plan and inputs, reserve the job (enqueue), then record the queued render."""
    with session_lock(session_id):
        record = store.get_session(session_id, owner)
        if record.get("status") != "plan_ready":
            raise StudioError(409, "plan_not_ready", "Complete and validate the editing plan before rendering.")
        current = record.get("render") or {}
        if current.get("status") in ACTIVE:
            raise StudioError(409, "render_in_progress", "A render is already running for this session.")
        store.pull_work(session_id)
        try:
            saved = load_approved(store, record)
            resolve_inputs(store, record, saved, materialize=False)
        except RenderError as exc:
            raise StudioError(409, "render_inputs_missing", str(exc)) from exc
        plan_version_id = saved.get("version_id") or "legacy"
        render_id = str(uuid.uuid4())
        while (store.renders / session_id / render_id).exists() or store.objects.list(f"renders/{session_id}/{render_id}/"):
            render_id = str(uuid.uuid4())
        job_id = enqueue(render_id, plan_version_id) if enqueue is not None else None
        record["render"] = {
            "render_id": render_id,
            "status": "render_queued",
            "stage": "render_queued",
            "created_at": _now(),
            "error": "",
            "plan_version_id": plan_version_id,
            "job_id": job_id,
            "message": "Render queued. The approved plan will be sent to the existing renderer.",
        }
        history = record.setdefault("render_history", [])
        history.append({"render_id": render_id, "status": "render_queued", "created_at": record["render"]["created_at"], "plan_version_id": plan_version_id})
        store.write_session(record)
        return record


def _stage(store: StudioStore, session_id: str, owner: str, stage: str, message: str) -> None:
    with session_lock(session_id):
        record = store.get_session(session_id, owner)
        state = record.get("render") or {}
        state.update({"status": stage, "stage": stage, "message": message})
        record["render"] = state
        store.write_session(record)


def _history(record: dict, render_id: str, status: str) -> None:
    for row in record.get("render_history") or []:
        if row.get("render_id") == render_id:
            row["status"] = status
            row["finished_at"] = _now()


def run_render(store: StudioStore, session_id: str, owner: str, bridge: EngineBridge | None = None, render_id: str | None = None, publish=None) -> None:
    """publish, when given, copies the render's files to object storage before it is marked completed."""
    with session_lock(session_id):
        record = store.get_session(session_id, owner)
        state = record.get("render") or {}
        if state.get("status") != "render_queued":
            return
        if render_id is not None and state.get("render_id") != render_id:
            return
        render_id = state["render_id"]
        plan_version_id = state.get("plan_version_id")
    _stage(store, session_id, owner, "preparing", "Checking the approved plan and preparing its media.")
    detail = ""
    try:
        record = store.get_session(session_id, owner)
        result = render_attempt(
            store,
            record,
            render_id,
            bridge or EngineBridge(),
            lambda name, message: _stage(store, session_id, owner, name, message),
            None if plan_version_id in (None, "legacy") else plan_version_id,
        )
        if publish is not None:
            publish()
    except RenderError as exc:
        message = str(exc)
        detail = traceback.format_exc()
    except EngineFailure as exc:
        message = f"The {exc.step} step failed. The approved plan is unchanged; see the render diagnostics."
        detail = exc.detail + "\n" + traceback.format_exc()
    except (Exception, SystemExit) as exc:
        message = "Rendering failed. The approved plan is unchanged; see the render diagnostics."
        detail = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc()
    else:
        with session_lock(session_id):
            record = store.get_session(session_id, owner)
            state = {**(record.get("render") or {}), **result, "stage": "completed", "error": "", "message": "Video rendered and verified."}
            record["render"] = state
            record["last_completed_render"] = dict(state)
            _history(record, render_id, "completed")
            store.write_session(record)
        return
    logging.getLogger("studio.render").warning("render failed", extra={"render_id": render_id, "error": message[:400], "detail": detail[-3000:]})
    with session_lock(session_id):
        record = store.get_session(session_id, owner)
        state = record.get("render") or {}
        state.update({"status": "failed", "stage": "failed", "failed_at": _now(), "error": message[:400], "message": "Rendering failed. The approved plan is unchanged."})
        record["render"] = state
        _history(record, render_id, "failed")
        store.write_session(record)
    attempt = store.renders / session_id / render_id
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "failure.json").write_text(
        json.dumps({"render_id": render_id, "failed_at": _now(), "error": message[:400], "detail": detail[-6000:]}, indent=2),
        encoding="utf-8",
    )


def render_status(store: StudioStore, session_id: str, owner: str) -> dict:
    record = store.get_session(session_id, owner)
    state = public_render(record.get("render"))
    previous = public_render(record.get("last_completed_render"))
    body = state or {"status": "not_started", "stage": "not_started", "message": "Render the validated editing plan when you are ready."}
    body = {"session_id": session_id, **body}
    if previous and previous.get("render_id") != body.get("render_id"):
        body["last_completed"] = previous
    return body


def completed_video(store: StudioStore, session_id: str, owner: str) -> tuple[str, dict]:
    """Object key of the newest completed render that belongs to this owner."""
    record = store.get_session(session_id, owner)
    state = record.get("render") or {}
    if state.get("status") != "completed":
        state = record.get("last_completed_render") or {}
    if state.get("status") != "completed" or not state.get("render_id"):
        raise StudioError(404, "video_not_ready", "There is no completed video for this session yet.")
    try:
        render_id = str(uuid.UUID(str(state["render_id"])))
    except ValueError as exc:
        raise StudioError(404, "video_missing", "The rendered video is not available.") from exc
    key = f"renders/{session_id}/{render_id}/final.mp4"
    info = store.objects.head(key)
    if info is None or info.size == 0:
        raise StudioError(404, "video_missing", "The rendered video is not available.")
    return key, state


def mark_render_failed(store: StudioStore, session_id: str, owner: str, render_id: str | None, error: str) -> None:
    """Record a render that stopped without a result. Earlier completed renders are left alone."""
    with session_lock(session_id):
        record = store.get_session(session_id, owner)
        state = record.get("render") or {}
        if render_id is not None and state.get("render_id") != render_id:
            _history(record, render_id, "failed")
            store.write_session(record)
            return
        if state.get("status") not in ACTIVE:
            return
        state.update({"status": "failed", "stage": "failed", "failed_at": _now(), "error": error, "message": "Rendering failed. Retry to start a fresh attempt."})
        record["render"] = state
        _history(record, state.get("render_id"), "failed")
        store.write_session(record)


def recover_render_jobs(store: StudioStore) -> None:
    """Inline runner only: a restart lost renders that ran in the API process."""
    for record in store.list_session_records():
        state = record.get("render") or {}
        if state.get("status") not in ACTIVE:
            continue
        mark_render_failed(store, record["session_id"], record["client_id"], state.get("render_id"), "Rendering was interrupted before a video was saved.")
