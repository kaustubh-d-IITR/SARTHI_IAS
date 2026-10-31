"""Background worker for processing, re-matching, and rendering.

    python -m app.worker

The API only enqueues. This process claims one job at a time from the database,
pulls the session's objects into its scratch directory, runs the existing Phase 2-4
code, pushes the results back, and records plan versions and renders. A heartbeat
renews the job's lease. If a worker dies, its lease expires and the next worker's
reaper handles the job: processing is retried once; a render is marked failed with
its logs kept and waits for an explicit retry under a new render id.

With STUDIO_JOB_RUNNER=inline (local development and tests) the same claim and
execute path runs inside the API process.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import threading
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from app import jobqueue
from app.config import Settings
from app.db import database_label, plan_versions, renders, schema_state, utcnow
from app.errors import StudioError
from app.logs import configure_logging, log_context
from app.processing.jobs import Services, _fail, queue_processing, run_processing
from app.processing.versions import list_versions
from app.services.rendering.bridge import EngineBridge
from app.services.rendering.jobs import mark_render_failed, queue_render, run_render
from app.storage import StudioStore

log = logging.getLogger("studio.worker")
VERSION = "phase5-1"


# --------------------------------------------------------------------------- submit (API side)


def _replay(store: StudioStore, session_id: str, owner: str, kind: str, idempotency_key: str | None) -> tuple[dict, str] | None:
    """A request repeated with the same idempotency key gets the original job back."""
    if not idempotency_key:
        return None
    job = jobqueue.find_by_key(store.db, idempotency_key)
    if job is None:
        return None
    if job["session_id"] != session_id or job["owner_id"] != owner or jobqueue.SLOTS[job["kind"]] != jobqueue.SLOTS[kind]:
        raise StudioError(409, "idempotency_conflict", "That request key was already used for something else.")
    return store.get_session(session_id, owner), job["id"]


def submit_processing(store: StudioStore, session_id: str, owner: str, services: Services, rematch: bool = False, idempotency_key: str | None = None) -> tuple[dict, str]:
    kind = "rematch" if rematch else "process"
    replay = _replay(store, session_id, owner, kind, idempotency_key)
    if replay is not None:
        return replay
    created: dict = {}

    def enqueue() -> str:
        job, fresh = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind=kind, idempotency_key=idempotency_key)
        created["job"], created["fresh"] = job, fresh
        if not fresh:
            raise _Duplicate(job)
        return job["id"]

    try:
        record = queue_processing(store, session_id, owner, services, rematch=rematch, enqueue=enqueue)
    except _Duplicate as dup:
        return store.get_session(session_id, owner), dup.job["id"]
    return record, created["job"]["id"]


def submit_render(store: StudioStore, session_id: str, owner: str, idempotency_key: str | None = None) -> tuple[dict, str]:
    replay = _replay(store, session_id, owner, "render", idempotency_key)
    if replay is not None:
        return replay
    created: dict = {}

    def enqueue(render_id: str, plan_version_id: str) -> str:
        job, fresh = jobqueue.enqueue(
            store.db,
            session_id=session_id,
            owner=owner,
            kind="render",
            payload={"render_id": render_id, "plan_version_id": plan_version_id},
            idempotency_key=idempotency_key,
        )
        created["job"] = job
        if not fresh:
            raise _Duplicate(job)
        return job["id"]

    try:
        record = queue_render(store, session_id, owner, enqueue=enqueue)
    except _Duplicate as dup:
        return store.get_session(session_id, owner), dup.job["id"]
    _record_render(store, session_id, record.get("render") or {}, created["job"]["id"])
    return record, created["job"]["id"]


class _Duplicate(Exception):
    def __init__(self, job: dict) -> None:
        super().__init__("duplicate")
        self.job = job


# --------------------------------------------------------------------------- index rows


def _record_render(store: StudioStore, session_id: str, state: dict, job_id: str | None = None) -> None:
    render_id = state.get("render_id")
    if not render_id:
        return
    now = utcnow()
    details = {key: state.get(key) for key in ("metadata", "audio_verification", "outro", "title", "message", "approved_broll_placements", "lesson_duration_seconds", "quality") if key in state}
    values = {
        "status": state.get("status") or "unknown",
        "plan_version_id": state.get("plan_version_id"),
        "details": details,
        "error": state.get("error") or "",
        "updated_at": now,
    }
    if state.get("status") == "completed":
        values["completed_at"] = now
    with store.db.begin() as conn:
        exists = conn.execute(select(renders.c.id).where(renders.c.id == render_id)).first()
        if exists:
            conn.execute(update(renders).where(renders.c.id == render_id).values(**values))
        else:
            conn.execute(
                insert(renders).values(id=render_id, session_id=session_id, job_id=job_id or state.get("job_id"), object_prefix=f"renders/{session_id}/{render_id}/", created_at=now, **values)
            )


def sync_index(store: StudioStore, session_id: str, owner: str) -> None:
    """Make sure every plan version file and every render of the session has its database row."""
    work = store.work_dir(session_id)
    with store.db.connect() as conn:
        known = {row.id for row in conn.execute(select(plan_versions.c.id).where(plan_versions.c.session_id == session_id))}
    for version in list_versions(work):
        if version["version_id"] in known:
            continue
        created = version.get("created_at")
        with store.db.begin() as conn:
            conn.execute(
                insert(plan_versions).values(
                    id=version["version_id"],
                    session_id=session_id,
                    number=version["number"],
                    parent_version_id=version.get("parent_version_id"),
                    origin=version.get("origin") or "generated",
                    validation_ok=bool(version.get("validation_ok")),
                    title=version.get("title"),
                    object_key=f"work/{session_id}/plans/{version['version_id']}.json",
                    created_at=datetime.fromisoformat(created) if created else utcnow(),
                )
            )
    record = store.get_session(session_id, owner)
    seen = set()
    for state in (record.get("render"), record.get("last_completed_render")):
        if state and state.get("render_id") and state["render_id"] not in seen:
            seen.add(state["render_id"])
            _record_render(store, session_id, state)
    for row in record.get("render_history") or []:
        if row.get("render_id") and row["render_id"] not in seen:
            seen.add(row["render_id"])
            _record_render(store, session_id, {"render_id": row["render_id"], "status": row.get("status"), "plan_version_id": row.get("plan_version_id")})


# --------------------------------------------------------------------------- execute


def _broll_hashes(store: StudioStore, record: dict) -> list[str]:
    hashes = []
    for item in record.get("broll") or []:
        try:
            meta = store.get_upload(item["upload_id"], record["client_id"])
        except StudioError:
            continue
        if meta.get("sha256"):
            hashes.append(meta["sha256"])
    return hashes


def _cleanup_scratch(store: StudioStore, record: dict, render_id: str | None = None) -> None:
    """Free the worker's scratch disk once results are in storage. Storage itself is untouched."""
    if store.workspace.passthrough or os.environ.get("STUDIO_KEEP_SCRATCH") == "1":
        return
    sid = record["session_id"]
    targets = [store.root / "work" / sid, store.root / "renders" / sid]
    targets += [store.root / "uploads" / record["raw_video"]["upload_id"]]
    targets += [store.root / "uploads" / item["upload_id"] for item in record.get("broll") or []]
    for path in targets:
        shutil.rmtree(path, ignore_errors=True)


def execute(store: StudioStore, job: dict, services: Services, bridge: EngineBridge, worker_id: str, lease_seconds: int) -> str:
    """Run one claimed job. Returns the job's final status."""
    session_id, owner, kind = job["session_id"], job["owner_id"], job["kind"]
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(max(5, lease_seconds // 3)):
            if not jobqueue.heartbeat(store.db, job["id"], worker_id, lease_seconds):
                log.warning("lease lost", extra={"job_id": job["id"]})
                return

    thread = threading.Thread(target=beat, name=f"heartbeat-{job['id'][:8]}", daemon=True)
    thread.start()
    started = time.monotonic()
    status, error, category = "failed", "", ""
    record = store.get_session(session_id, owner)
    with log_context(job_id=job["id"], session_id=session_id, user_id=owner):
        log.info("job started", extra={"kind": kind, "attempt": job["attempts"]})
        try:
            if kind in ("process", "rematch"):
                store.pull_work(session_id, include_footage=True)
                store.workspace.pull_prefix("transcript_cache/")
                for sha in _broll_hashes(store, record):
                    store.workspace.pull_prefix(f"visual/{sha}/")

                def publish_processing() -> None:
                    store.push_work(session_id)
                    store.workspace.push_prefix("transcript_cache/")
                    store.workspace.push_prefix("visual/")

                run_processing(store, session_id, owner, services, publish=publish_processing)
                publish_processing()
                after = store.get_session(session_id, owner)
                failed = after["status"] == "failed" or (kind == "rematch" and after.get("rematch_error"))
                status = "failed" if failed else "succeeded"
                error = after.get("error") or after.get("rematch_error") or ""
                category = "processing" if failed else ""
            elif kind == "render":
                render_id = (job.get("payload") or {}).get("render_id")
                store.pull_work(session_id, include_footage=True)

                def publish_render() -> None:
                    store.workspace.push_prefix(f"renders/{session_id}/{render_id}/")

                run_render(store, session_id, owner, bridge, render_id=render_id, publish=publish_render)
                publish_render()
                state = store.get_session(session_id, owner).get("render") or {}
                status = "succeeded" if state.get("render_id") == render_id and state.get("status") == "completed" else "failed"
                error = state.get("error") or ""
                category = "render" if status == "failed" else ""
            sync_index(store, session_id, owner)
        except BaseException as exc:  # noqa: BLE001 - engine helpers raise SystemExit
            status, category = "failed", "worker_error"
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.error("job crashed", extra={"error": error, "trace": traceback.format_exc()[-2000:]})
            _settle_failure(store, job, "The job stopped unexpectedly. The previous plan and earlier videos are unchanged; retry to try again.")
            if isinstance(exc, KeyboardInterrupt):
                raise
        finally:
            stop.set()
            jobqueue.finish(store.db, job["id"], status, error, category)
            log.info("job finished", extra={"kind": kind, "status": status, "duration_seconds": round(time.monotonic() - started, 1), "failure_category": category})
            try:
                _cleanup_scratch(store, record)
            except Exception:  # noqa: BLE001
                pass
    return status


def _settle_failure(store: StudioStore, job: dict, message: str) -> None:
    session_id, owner = job["session_id"], job["owner_id"]
    try:
        if job["kind"] == "render":
            render_id = (job.get("payload") or {}).get("render_id")
            mark_render_failed(store, session_id, owner, render_id, message)
            store.objects.put_bytes(
                f"renders/{session_id}/{render_id}/failure.json",
                json.dumps({"render_id": render_id, "failed_at": datetime.now(timezone.utc).isoformat(), "error": message}).encode(),
                "application/json",
            )
            _record_render(store, session_id, store.get_session(session_id, owner).get("render") or {"render_id": render_id, "status": "failed"})
        else:
            record = store.get_session(session_id, owner)
            if record.get("status") not in {"plan_ready", "failed", "prepared"} or record.get("rematch_of"):
                _fail(store, session_id, owner, message)
    except Exception as exc:  # noqa: BLE001
        log.error("could not record the failure", extra={"error": f"{type(exc).__name__}"})


def recover_expired(store: StudioStore) -> list[str]:
    """Handle jobs whose worker stopped renewing the lease."""
    handled = []
    for job in jobqueue.expired(store.db):
        with log_context(job_id=job["id"], session_id=job["session_id"]):
            if job["kind"] in ("process", "rematch") and job["attempts"] < job["max_attempts"]:
                jobqueue.requeue(store.db, job["id"], "The worker stopped; retrying once.")
                try:
                    record = store.get_session(job["session_id"], job["owner_id"])
                    record.update({"status": "queued", "stage": "queued", "note": "The worker restarted. Processing will start again."})
                    store.write_session(record)
                except StudioError:
                    pass
                log.warning("job requeued after lost worker")
            else:
                jobqueue.finish(store.db, job["id"], "failed", "The worker stopped before the job finished.", "worker_lost")
                text = (
                    "Rendering stopped because the worker shut down. Earlier videos are unchanged. Retry to start a new render."
                    if job["kind"] == "render"
                    else "Processing stopped because the worker shut down. Retry to start again."
                )
                _settle_failure(store, job, text)
                log.warning("job failed after lost worker")
        handled.append(job["id"])
    return handled


def run_inline(store: StudioStore, job_id: str, services: Services, bridge: EngineBridge, lease_seconds: int = 180) -> None:
    """Local development and tests: claim the given job and run it in this process."""
    job = jobqueue.claim(store.db, "inline", lease_seconds, job_id=job_id)
    if job is not None:
        execute(store, job, services, bridge, "inline", lease_seconds)


def recover_inline(store: StudioStore) -> None:
    """Inline jobs died with the previous API process."""
    for job in jobqueue.open_jobs(store.db):
        jobqueue.finish(store.db, job["id"], "failed", "The API restarted before the job finished.", "api_restart")


# --------------------------------------------------------------------------- main loop


def wait_for_schema(store: StudioStore, timeout: float, interval: float = 10, sleep=time.sleep) -> dict:
    """Block until the database is at the code's migration head; the API's pre-deploy migrate gets it there.

    The worker never migrates in production (one migrator only). If the schema does not
    arrive within timeout, exit with a message naming the database instead of crashing
    later on a missing table.
    """
    label = database_label(store.settings.resolved_database_url)
    deadline = time.monotonic() + timeout
    while True:
        try:
            state = schema_state(store.db)
        except Exception as exc:  # noqa: BLE001 - an unreachable database is reported the same way
            state = {"ok": False, "error": type(exc).__name__}
        if state["ok"]:
            log.info("database schema ready", extra={"database": label, "revision": state["revision"]})
            return state
        if time.monotonic() >= deadline:
            log.error("database schema not ready", extra={"database": label, **state})
            raise SystemExit(
                f"The database {label} is not at migration {state.get('head')} (found {state.get('revision')}, "
                f"missing tables {state.get('missing_tables')}). Run `python -m app.cli migrate` on the API service "
                "against this same DATABASE_URL, then restart the worker."
            )
        log.warning("waiting for database migrations", extra={"database": label, **state})
        sleep(interval)


def main() -> int:
    configure_logging()
    settings = Settings.from_env()
    store = StudioStore(settings)
    if settings.auto_migrate:
        from app.db import migrate

        migrate(settings.resolved_database_url)
    wait_for_schema(store, float(os.environ.get("STUDIO_SCHEMA_WAIT_SECONDS") or 600))
    store.ensure()
    services = Services()
    bridge = EngineBridge()
    worker_id = os.environ.get("STUDIO_WORKER_ID") or jobqueue.worker_identity()
    running = {"value": True}

    def stop(_signum, _frame) -> None:
        running["value"] = False
        log.info("stop requested; finishing the current job first")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    poll = float(os.environ.get("STUDIO_WORKER_POLL_SECONDS") or 3)
    log.info("worker started", extra={"worker_id": worker_id, "version": VERSION})
    while running["value"]:
        jobqueue.record_worker(store.db, worker_id, VERSION)
        recover_expired(store)
        job = jobqueue.claim(store.db, worker_id, settings.job_lease_seconds)
        if job is None:
            time.sleep(poll)
            continue
        jobqueue.record_worker(store.db, worker_id, VERSION, job["id"])
        execute(store, job, services, bridge, worker_id, settings.job_lease_seconds)
    jobqueue.record_worker(store.db, worker_id, VERSION)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
