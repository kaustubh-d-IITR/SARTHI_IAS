"""FastAPI application factory. Importing this module does not start a render."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from collections import defaultdict, deque

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app import auth, jobqueue
from app.config import ALLOWED_EXTENSIONS, Settings
from app.db import get_engine, migrate
from app.errors import StudioError
from app.logs import bind, configure_logging, log_context
from app.media_probe import resolve_ffprobe
from app.objects import StorageError, verify_token
from app.processing.editing import apply_edits
from app.processing.jobs import (
    Services,
    read_plan,
    read_transcript,
    read_version,
    read_versions,
    recover_interrupted,
    session_lock,
    thumbnail_for,
)
from app.schemas import (
    BrollUploadResponse,
    HealthConfiguration,
    HealthResponse,
    PlanEdit,
    RawUploadResponse,
    SessionCreate,
    SessionResponse,
)
from app.services.rendering.bridge import EngineBridge
from app.services.rendering.jobs import completed_video, public_render, recover_render_jobs, render_status
from app.storage import StudioStore
from app.worker import recover_inline, run_inline, submit_processing, submit_render, sync_index

CLIENT_HEADER = "x-studio-client"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT = ("/api/auth/login", "/api/files/")
log = logging.getLogger("studio.api")


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=500)


class InitiateUpload(BaseModel):
    kind: str = Field(pattern="^(raw|broll)$")
    filename: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=500)


class RateLimiter:
    """Per-instance sliding window. The database login throttle covers multiple instances."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def allow(self, ip: str, bucket: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        queue = self.hits[(ip, bucket)]
        while queue and now - queue[0] > window:
            queue.popleft()
        if len(queue) >= limit:
            return False
        queue.append(now)
        return True


def create_app(
    settings: Settings | None = None,
    services: Services | None = None,
    renderer: EngineBridge | None = None,
) -> FastAPI:
    configure_logging()
    active = settings or Settings.from_env()
    if active.auto_migrate:
        migrate(active.resolved_database_url)
    store = StudioStore(active)
    store.ensure()
    if active.job_runner == "inline":
        recover_inline(store)
        recover_interrupted(store)
        recover_render_jobs(store)
    jobs = services or Services()
    bridge = renderer or EngineBridge()
    db = get_engine(active.resolved_database_url)
    limiter = RateLimiter()
    app = FastAPI(title="Sarthi IAS Video Studio", version=active.version, docs_url=None if active.environment == "production" else "/docs", redoc_url=None)
    app.state.settings = active
    app.state.store = store
    app.state.services = jobs
    app.state.renderer = bridge
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active.cors_origins),
        allow_credentials=active.auth_mode == "password",
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Studio-Client", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Render-ID"],
    )

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return (request.client.host if request.client else "unknown")[:64]

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = (request.headers.get("x-request-id") or "")[:64] or uuid.uuid4().hex
        started = time.monotonic()
        ip = client_ip(request)
        with log_context(request_id=request_id):
            path = request.url.path
            if path.startswith("/api/auth/login") and not limiter.allow(ip, "login", 20):
                return _error(429, "rate_limited", "Too many requests. Wait a minute and try again.", request_id)
            if not limiter.allow(ip, "all", active.rate_limit_per_minute):
                return _error(429, "rate_limited", "Too many requests. Wait a minute and try again.", request_id)
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > 2_000_000 and not (path.startswith("/api/files/") or path.startswith("/api/uploads/")):
                return _error(413, "request_too_large", "The request body is too large.", request_id)
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"path": path, "method": request.method})
                return _error(500, "internal_error", "Something went wrong. The request id can be used to find the log entry.", request_id)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if path.startswith("/api/") and "cache-control" not in response.headers:
                response.headers["Cache-Control"] = "no-store"
            if path not in {"/api/live", "/api/health"}:
                log.info("request", extra={"method": request.method, "path": path, "status": response.status_code, "duration_ms": round((time.monotonic() - started) * 1000, 1)})
            return response

    @app.exception_handler(StudioError)
    async def studio_error(_request: Request, exc: StudioError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    def owner(request: Request) -> str:
        """The authenticated owner: the signed-in user, or in development the X-Studio-Client id."""
        if active.auth_mode == "password":
            user = auth.session_user(db, request.cookies.get(auth.COOKIE))
            if user is None:
                raise StudioError(401, "not_signed_in", "Sign in to continue.")
            if request.method in UNSAFE and not request.url.path.startswith(CSRF_EXEMPT):
                sent = request.headers.get(auth.CSRF_HEADER) or ""
                if not sent or sent != user["csrf_token"]:
                    raise StudioError(403, "csrf_failed", "The page is out of date. Reload it and try again.")
            bind(user_id=user["id"])
            return user["id"]
        raw = (request.headers.get(CLIENT_HEADER) or "").strip()
        try:
            client = str(uuid.UUID(raw))
        except ValueError as exc:
            raise StudioError(400, "client_required", "Send a UUID in the X-Studio-Client header.") from exc
        bind(user_id=client)
        return client

    def current_store() -> StudioStore:
        return store

    def dispatch(job_id: str, background: BackgroundTasks) -> None:
        if active.job_runner == "inline":
            background.add_task(run_inline, store, job_id, jobs, bridge, active.job_lease_seconds)

    # ------------------------------------------------------------------ health

    @app.get("/api/live")
    def live() -> dict:
        return {"status": "alive", "version": active.version}

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        writable = store.ensure()
        return HealthResponse(
            status="ok" if writable else "degraded",
            version=active.version,
            upload_directory_writable=writable,
            configuration=HealthConfiguration(
                max_file_bytes=active.max_file_bytes,
                max_session_bytes=active.max_session_bytes,
                allowed_extensions=[item.lstrip(".") for item in ALLOWED_EXTENSIONS],
                ffprobe_available=resolve_ffprobe() is not None,
                credentials_loaded=False,
                auth_mode=active.auth_mode,
                upload_mode="direct",
                environment=active.environment,
            ),
        )

    @app.get("/api/ready")
    def ready() -> JSONResponse:
        checks: dict[str, dict] = {}
        try:
            with db.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            checks["database"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            checks["database"] = {"ok": False, "detail": type(exc).__name__}
        ok, detail = store.objects.ping()
        checks["object_storage"] = {"ok": ok, "backend": store.objects.backend, **({"detail": detail} if detail else {})}
        try:
            depth = jobqueue.queue_depth(db)
            checks["queue"] = {"ok": True, **depth}
        except Exception as exc:  # noqa: BLE001
            checks["queue"] = {"ok": False, "detail": type(exc).__name__}
        if active.job_runner == "queue":
            try:
                alive = jobqueue.live_workers(db, within_seconds=max(60, active.job_lease_seconds))
                checks["worker"] = {"ok": bool(alive), "live_workers": len(alive)}
            except Exception as exc:  # noqa: BLE001
                checks["worker"] = {"ok": False, "detail": type(exc).__name__}
        else:
            checks["worker"] = {"ok": True, "runner": "inline"}
        checks["ffmpeg"] = {"ok": bool(shutil.which("ffmpeg")) and resolve_ffprobe() is not None}
        status = 200 if all(item["ok"] for item in checks.values()) else 503
        return JSONResponse(status_code=status, content={"status": "ready" if status == 200 else "not_ready", "checks": checks})

    # ------------------------------------------------------------------ auth

    @app.get("/api/auth/me")
    def me(request: Request) -> dict:
        if active.auth_mode != "password":
            return {"mode": "client_header", "user": None, "csrf_token": None}
        user = auth.session_user(db, request.cookies.get(auth.COOKIE))
        if user is None:
            raise StudioError(401, "not_signed_in", "Sign in to continue.")
        return {"mode": "password", "user": {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}, "csrf_token": user["csrf_token"]}

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request) -> JSONResponse:
        if active.auth_mode != "password":
            raise StudioError(404, "not_found", "Sign-in is not enabled in this environment.")
        user, token = auth.login(db, body.email, body.password, client_ip(request), request.headers.get("user-agent", ""), active.session_ttl_hours, active.login_attempts_per_window)
        response = JSONResponse({"user": {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}, "csrf_token": user["csrf_token"]})
        response.set_cookie(auth.COOKIE, token, max_age=active.session_ttl_hours * 3600, httponly=True, secure=active.cookie_secure, samesite=active.cookie_samesite, path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request, _owner: str = Depends(owner)) -> JSONResponse:
        auth.logout(db, request.cookies.get(auth.COOKIE))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(auth.COOKIE, path="/", httponly=True, secure=active.cookie_secure, samesite=active.cookie_samesite)
        return response

    # ------------------------------------------------------------------ uploads

    def _api_upload_allowed() -> None:
        if store.objects.backend != "local":
            raise StudioError(410, "use_direct_upload", "Upload files with /api/uploads/initiate.")

    @app.post("/api/uploads/raw-video", response_model=RawUploadResponse)
    async def upload_raw(file: UploadFile = File(...), user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        _api_upload_allowed()
        if not uploads.ensure():
            raise StudioError(503, "storage_unavailable", "The upload directory is not writable.")
        record = await uploads.save_upload(file, client_id=user, kind="raw")
        return uploads.public_upload(record)

    @app.post("/api/uploads/broll", response_model=BrollUploadResponse)
    async def upload_broll(file: UploadFile = File(...), description: str = Form(""), user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        _api_upload_allowed()
        if not uploads.ensure():
            raise StudioError(503, "storage_unavailable", "The upload directory is not writable.")
        record = await uploads.save_upload(file, client_id=user, kind="broll", description=description)
        return uploads.public_asset(record)

    @app.post("/api/uploads/initiate")
    def initiate_upload(body: InitiateUpload, user: str = Depends(owner)) -> dict:
        return store.initiate_upload(user, body.kind, body.filename, body.size_bytes, body.description)

    @app.post("/api/uploads/{upload_id}/complete")
    def complete_upload(upload_id: str, user: str = Depends(owner)) -> dict:
        meta = store.complete_upload(upload_id, user)
        return store.public_asset(meta) if meta["kind"] == "broll" else store.public_upload(meta)

    @app.delete("/api/uploads/{upload_id}")
    def delete_upload(upload_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> Response:
        uploads.delete_upload(upload_id, user)
        return Response(status_code=204)

    @app.put("/api/files/{token}")
    async def local_put(token: str, request: Request) -> dict:
        if store.objects.backend != "local":
            raise StudioError(404, "not_found", "Not found.")
        try:
            payload = verify_token(active.signing_secret, token)
        except StorageError as exc:
            raise StudioError(403, "invalid_link", str(exc)) from exc
        if payload.get("op") != "put":
            raise StudioError(403, "invalid_link", "Invalid link.")
        tmp, final, limit = store.local_put_target(payload["key"])
        total = 0
        try:
            with tmp.open("wb") as handle:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise StudioError(413, "file_too_large", "That file is larger than the configured limit.")
                    handle.write(chunk)
            tmp.replace(final)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return {"stored_bytes": total}

    @app.get("/api/files/{token}")
    def local_get(token: str) -> FileResponse:
        if store.objects.backend != "local":
            raise StudioError(404, "not_found", "Not found.")
        try:
            payload = verify_token(active.signing_secret, token)
        except StorageError as exc:
            raise StudioError(403, "invalid_link", str(exc)) from exc
        if payload.get("op") != "get":
            raise StudioError(403, "invalid_link", "Invalid link.")
        path = store.objects.path(payload["key"])
        if not path.is_file():
            raise StudioError(404, "not_found", "Not found.")
        name = payload.get("name") or path.name
        disposition = "inline" if payload.get("inline", True) else "attachment"
        media = "video/mp4" if path.suffix == ".mp4" else ("image/jpeg" if path.suffix == ".jpg" else "application/octet-stream")
        return FileResponse(path, media_type=media, headers={"Content-Disposition": f'{disposition}; filename="{name}"', "Cache-Control": "private, max-age=300"})

    # ------------------------------------------------------------------ sessions

    @app.get("/api/sessions")
    def list_sessions(user: str = Depends(owner)) -> dict:
        rows = []
        for record in store.list_session_records(user)[:100]:
            render = public_render(record.get("render")) or {}
            rows.append(
                {
                    "session_id": record["session_id"],
                    "mode": record["mode"],
                    "status": record["status"],
                    "title": (record.get("title") or {}).get("text") or "",
                    "filename": record["raw_video"]["filename"],
                    "created_at": record["created_at"],
                    "render_status": render.get("status") or "not_started",
                    "has_video": bool(record.get("last_completed_render")),
                }
            )
        return {"sessions": rows}

    @app.post("/api/sessions", response_model=SessionResponse)
    def create_session(body: SessionCreate, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        record = uploads.create_session(user, body.mode, body.raw_video_id, [item.model_dump() for item in body.broll], body.title)
        with log_context(session_id=record["session_id"]):
            auth.audit(db, "session_created", user_id=user, session_id=record["session_id"], detail={"mode": body.mode})
        return _session_body(record)

    @app.get("/api/sessions/{session_id}", response_model=SessionResponse)
    def read_session(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return _session_body(uploads.get_session(session_id, user))

    @app.post("/api/sessions/{session_id}/process", response_model=SessionResponse)
    def process_session(session_id: str, request: Request, background: BackgroundTasks, user: str = Depends(owner)) -> dict:
        with log_context(session_id=session_id):
            record, job_id = submit_processing(store, session_id, user, jobs, idempotency_key=request.headers.get("idempotency-key"))
            log.info("processing queued", extra={"job_id": job_id})
        dispatch(job_id, background)
        return _session_body(record)

    @app.get("/api/sessions/{session_id}/status")
    def session_status(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        record = uploads.get_session(session_id, user)
        return {
            "session_id": record["session_id"],
            "mode": record["mode"],
            "status": record["status"],
            "stage": record.get("stage") or record["status"],
            "error": record.get("error") or "",
            "message": record.get("note") or "",
            "filename": record["raw_video"]["filename"],
            "broll_count": len(record.get("broll") or []),
            "counts": record.get("counts"),
            "title": record.get("title"),
            "plan_version": record.get("plan_version"),
            "rematch_error": record.get("rematch_error") or "",
        }

    @app.get("/api/sessions/{session_id}/transcript")
    def session_transcript(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return read_transcript(uploads, session_id, user)

    @app.get("/api/sessions/{session_id}/editing-plan")
    def session_plan(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return read_plan(uploads, session_id, user)

    @app.post("/api/sessions/{session_id}/editing-plan/edits")
    def edit_plan(session_id: str, body: PlanEdit, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        payload: dict = {"base_version_id": body.base_version_id, "placements": body.placements}
        if body.change_title:
            payload["title"] = body.title or ""
        with log_context(session_id=session_id):
            uploads.get_session(session_id, user)
            uploads.pull_work(session_id)
            result = apply_edits(uploads, session_id, user, payload, session_lock(session_id))
            uploads.push_work(session_id)
            sync_index(uploads, session_id, user)
            auth.audit(db, "plan_edited", user_id=user, session_id=session_id, detail={"version_id": result["plan_version"]["version_id"]})
        return result

    @app.get("/api/sessions/{session_id}/editing-plan/versions")
    def plan_versions(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return read_versions(uploads, session_id, user)

    @app.get("/api/sessions/{session_id}/editing-plan/versions/{version_id}")
    def plan_version(session_id: str, version_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return read_version(uploads, session_id, user, version_id)

    @app.post("/api/sessions/{session_id}/rematch", response_model=SessionResponse)
    def rematch_session(session_id: str, request: Request, background: BackgroundTasks, user: str = Depends(owner)) -> dict:
        with log_context(session_id=session_id):
            record, job_id = submit_processing(store, session_id, user, jobs, rematch=True, idempotency_key=request.headers.get("idempotency-key"))
        dispatch(job_id, background)
        return _session_body(record)

    @app.get("/api/sessions/{session_id}/assets/{asset_id}/thumbnail")
    def asset_thumbnail(session_id: str, asset_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> FileResponse:
        return FileResponse(thumbnail_for(uploads, session_id, user, asset_id), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

    @app.post("/api/sessions/{session_id}/render")
    def render_session(session_id: str, request: Request, background: BackgroundTasks, user: str = Depends(owner)) -> dict:
        with log_context(session_id=session_id):
            record, job_id = submit_render(store, session_id, user, idempotency_key=request.headers.get("idempotency-key"))
            render = record.get("render") or {}
            log.info("render queued", extra={"job_id": job_id, "render_id": render.get("render_id"), "plan_version_id": render.get("plan_version_id")})
            auth.audit(db, "render_requested", user_id=user, session_id=session_id, detail={"render_id": render.get("render_id"), "plan_version_id": render.get("plan_version_id")})
        dispatch(job_id, background)
        return _session_body(record)

    @app.get("/api/sessions/{session_id}/render-status")
    def session_render_status(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        return render_status(uploads, session_id, user)

    @app.get("/api/sessions/{session_id}/video-url")
    def video_url(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)) -> dict:
        key, state = completed_video(uploads, session_id, user)
        name = f"saarthi-video-{state['render_id'][:8]}.mp4"
        seconds = active.signed_url_seconds
        return {
            "render_id": state["render_id"],
            "url": uploads.objects.presign_get(key, seconds, name, inline=True),
            "download_url": uploads.objects.presign_get(key, seconds, name, inline=False),
            "expires_in": seconds,
        }

    @app.get("/api/sessions/{session_id}/video")
    def session_video(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)):
        key, state = completed_video(uploads, session_id, user)
        if not uploads.workspace.passthrough:
            return RedirectResponse(uploads.objects.presign_get(key, active.signed_url_seconds, "saarthi-video.mp4", inline=True), status_code=307)
        return FileResponse(uploads.workspace.path(key), media_type="video/mp4", headers={"Content-Disposition": 'inline; filename="saarthi-video.mp4"', "X-Render-ID": state["render_id"]})

    @app.get("/api/sessions/{session_id}/download")
    def download_video(session_id: str, user: str = Depends(owner), uploads: StudioStore = Depends(current_store)):
        key, state = completed_video(uploads, session_id, user)
        name = f"saarthi-video-{state['render_id'][:8]}.mp4"
        if not uploads.workspace.passthrough:
            return RedirectResponse(uploads.objects.presign_get(key, active.signed_url_seconds, name, inline=False), status_code=307)
        return FileResponse(uploads.workspace.path(key), media_type="video/mp4", filename=name, headers={"X-Render-ID": state["render_id"]})

    return app


def _error(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers={"X-Request-ID": request_id})


def _session_body(record: dict) -> dict:
    return {
        "session_id": record["session_id"],
        "mode": record["mode"],
        "status": record["status"],
        "stage": record.get("stage") or record["status"],
        "error": record.get("error") or "",
        "raw_video": record["raw_video"],
        "broll": record["broll"],
        "total_bytes": record["total_bytes"],
        "broll_count": len(record["broll"]),
        "note": record["note"],
        "created_at": record["created_at"],
        "render": public_render(record.get("render")),
        "title": record.get("title"),
        "plan_version": record.get("plan_version"),
    }
