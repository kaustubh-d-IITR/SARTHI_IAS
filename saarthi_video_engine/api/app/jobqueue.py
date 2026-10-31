"""Durable job queue on the Studio database.

A job row is the unit of work. Its `active_slot` is set while the job is queued or
running and cleared when it finishes, and (session_id, active_slot) is unique, so
the database itself refuses a second active render or processing job for the same
session, even across API instances. A client may also send an idempotency key; a
repeated request with the same key returns the original job.

A worker claims a job by moving it from queued to running with a lease. It renews
the lease with heartbeats. A job whose lease expires was lost with its worker and is
handed to `reap`, which the worker loop calls.
"""

from __future__ import annotations

import socket
import uuid
from datetime import timedelta

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db import as_utc, jobs, utcnow, workers
from app.errors import StudioError

SLOTS = {"process": "process", "rematch": "process", "render": "render"}
MAX_ATTEMPTS = {"process": 2, "rematch": 2, "render": 1}
OPEN = ("queued", "running")


def _row(row) -> dict:
    data = dict(row._mapping)
    for key in ("lease_expires_at", "heartbeat_at", "created_at", "updated_at", "started_at", "finished_at"):
        data[key] = as_utc(data.get(key))
    return data


def get_job(db, job_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).first()
    return _row(row) if row else None


def find_by_key(db, idempotency_key: str) -> dict | None:
    key = (idempotency_key or "").strip()[:100]
    if not key:
        return None
    with db.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.idempotency_key == key)).first()
    return _row(row) if row else None


def active_job(db, session_id: str, kind: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(select(jobs).where(and_(jobs.c.session_id == session_id, jobs.c.active_slot == SLOTS[kind]))).first()
    return _row(row) if row else None


def session_jobs(db, session_id: str) -> list[dict]:
    with db.connect() as conn:
        return [_row(row) for row in conn.execute(select(jobs).where(jobs.c.session_id == session_id).order_by(jobs.c.created_at))]


def enqueue(db, *, session_id: str, owner: str, kind: str, payload: dict | None = None, idempotency_key: str | None = None) -> tuple[dict, bool]:
    """Insert a queued job. Returns (job, created). A reused idempotency key returns the original job."""
    if kind not in SLOTS:
        raise ValueError(kind)
    key = (idempotency_key or "").strip()[:100] or None
    if key:
        with db.connect() as conn:
            existing = conn.execute(select(jobs).where(jobs.c.idempotency_key == key)).first()
        if existing is not None:
            if existing.session_id != session_id or existing.kind != kind or existing.owner_id != owner:
                raise StudioError(409, "idempotency_conflict", "That request key was already used for something else.")
            return _row(existing), False
    now = utcnow()
    job_id = str(uuid.uuid4())
    try:
        with db.begin() as conn:
            conn.execute(
                insert(jobs).values(
                    id=job_id, session_id=session_id, owner_id=owner, kind=kind, status="queued", active_slot=SLOTS[kind],
                    idempotency_key=key, payload=payload or {}, attempts=0, max_attempts=MAX_ATTEMPTS[kind],
                    last_error="", failure_category="", created_at=now, updated_at=now,
                )
            )
    except IntegrityError as exc:
        if key:
            with db.connect() as conn:
                existing = conn.execute(select(jobs).where(jobs.c.idempotency_key == key)).first()
            if existing is not None and existing.session_id == session_id and existing.kind == kind:
                return _row(existing), False
        what = "A render" if SLOTS[kind] == "render" else "Processing"
        raise StudioError(409, "job_in_progress", f"{what} is already queued or running for this session.") from exc
    return get_job(db, job_id), True


def claim(db, worker_id: str, lease_seconds: int, job_id: str | None = None) -> dict | None:
    """Move one queued job (or the given one) to running with a lease. Returns None when nothing was claimed."""
    with db.connect() as conn:
        query = select(jobs.c.id).where(jobs.c.status == "queued").order_by(jobs.c.created_at).limit(5)
        if job_id:
            query = select(jobs.c.id).where(and_(jobs.c.id == job_id, jobs.c.status == "queued"))
        candidates = [row.id for row in conn.execute(query)]
    for candidate in candidates:
        now = utcnow()
        with db.begin() as conn:
            claimed = conn.execute(
                update(jobs)
                .where(and_(jobs.c.id == candidate, jobs.c.status == "queued"))
                .values(
                    status="running", lease_owner=worker_id, lease_expires_at=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now, attempts=jobs.c.attempts + 1, started_at=now, updated_at=now,
                )
            ).rowcount
        if claimed == 1:
            return get_job(db, candidate)
    return None


def heartbeat(db, job_id: str, worker_id: str, lease_seconds: int) -> bool:
    now = utcnow()
    with db.begin() as conn:
        return bool(
            conn.execute(
                update(jobs)
                .where(and_(jobs.c.id == job_id, jobs.c.status == "running", jobs.c.lease_owner == worker_id))
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds), heartbeat_at=now, updated_at=now)
            ).rowcount
        )


def finish(db, job_id: str, status: str, error: str = "", category: str = "") -> None:
    now = utcnow()
    with db.begin() as conn:
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(status=status, active_slot=None, lease_owner=None, lease_expires_at=None, last_error=error[:2000], failure_category=category, finished_at=now, updated_at=now)
        )


def requeue(db, job_id: str, error: str) -> None:
    now = utcnow()
    with db.begin() as conn:
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(status="queued", lease_owner=None, lease_expires_at=None, last_error=error[:2000], failure_category="worker_lost", updated_at=now)
        )


def expired(db, now=None) -> list[dict]:
    now = now or utcnow()
    with db.connect() as conn:
        rows = conn.execute(select(jobs).where(jobs.c.status == "running")).fetchall()
    return [_row(row) for row in rows if row.lease_expires_at is None or as_utc(row.lease_expires_at) < now]


def open_jobs(db) -> list[dict]:
    with db.connect() as conn:
        return [_row(row) for row in conn.execute(select(jobs).where(jobs.c.status.in_(OPEN)))]


def queue_depth(db) -> dict:
    counts = {"queued": 0, "running": 0}
    for job in open_jobs(db):
        counts[job["status"]] += 1
    return counts


def worker_identity() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def record_worker(db, worker_id: str, version: str, current_job: str | None = None) -> None:
    now = utcnow()
    with db.begin() as conn:
        changed = conn.execute(update(workers).where(workers.c.id == worker_id).values(last_seen_at=now, current_job_id=current_job)).rowcount
        if not changed:
            conn.execute(insert(workers).values(id=worker_id, hostname=socket.gethostname(), version=version, started_at=now, last_seen_at=now, current_job_id=current_job))


def live_workers(db, within_seconds: int = 120) -> list[dict]:
    cutoff = utcnow() - timedelta(seconds=within_seconds)
    with db.connect() as conn:
        rows = conn.execute(select(workers)).fetchall()
    return [dict(row._mapping) for row in rows if as_utc(row.last_seen_at) >= cutoff]
