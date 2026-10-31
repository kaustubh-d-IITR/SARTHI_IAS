"""Phase 5: storage providers, migrations, durable queue and worker, authentication, direct uploads.

External services are local stand-ins: a moto S3 server over real HTTP, SQLite, and
the Phase 3 FakeBridge in place of the HyperFrames CLI. No ElevenLabs, Gemini, or
Commons call is made.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update
from test_phase3 import DESCRIPTION, FakeBridge, _transcript, media  # noqa: F401  (media is a fixture)

from app import jobqueue
from app.config import Settings
from app.db import get_engine, jobs, migrate, plan_versions, renders, utcnow
from app.factory import create_app
from app.logs import mask
from app.objects import LocalStorageProvider, S3StorageProvider, StorageError, verify_token
from app.processing.jobs import Services
from app.storage import StudioStore
from app.worker import execute, recover_expired, run_inline


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    try:
        yield endpoint
    finally:
        server.stop()


def _s3(endpoint: str, bucket: str) -> S3StorageProvider:
    provider = S3StorageProvider(bucket, endpoint, "test-access", "test-secret", "us-east-1")
    provider.client.create_bucket(Bucket=bucket)
    return provider


def _settings(tmp: Path, **extra) -> Settings:
    base = {
        "data_dir": tmp / "studio",
        "max_file_bytes": 20_000_000,
        "max_session_bytes": 60_000_000,
        "cors_origins": (),
    }
    base.update(extra)
    return Settings(**base)


# --------------------------------------------------------------------------- storage


def test_local_storage_provider_keys_signing_and_integrity(tmp_path: Path):
    provider = LocalStorageProvider(tmp_path / "objects", "x" * 40)
    provider.put_bytes("work/abc/transcript.json", b'{"ok": true}')
    assert provider.exists("work/abc/transcript.json")
    assert provider.size("work/abc/transcript.json") == 12
    assert provider.sha256("work/abc/transcript.json") == hashlib.sha256(b'{"ok": true}').hexdigest()
    assert [item.key for item in provider.list("work/abc/")] == ["work/abc/transcript.json"]
    for bad in ("../secret", "work/../../etc/passwd", "C:/Windows/x", "outputs/x.mp4", "work//x", "/work/x"):
        with pytest.raises(StorageError):
            provider.put_bytes(bad, b"x")
    url = provider.presign_get("work/abc/transcript.json", 60)
    payload = verify_token("x" * 40, url.rsplit("/", 1)[-1])
    assert payload["key"] == "work/abc/transcript.json" and payload["op"] == "get"
    with pytest.raises(StorageError):
        verify_token("y" * 40, url.rsplit("/", 1)[-1])
    expired = provider.presign_get("work/abc/transcript.json", -1)
    with pytest.raises(StorageError, match="expired"):
        verify_token("x" * 40, expired.rsplit("/", 1)[-1])
    provider.delete("work/abc/transcript.json")
    assert provider.head("work/abc/transcript.json") is None


def test_s3_provider_is_private_and_uses_signed_urls(s3_server: str, tmp_path: Path):
    provider = _s3(s3_server, "studio-private")
    provider.put_bytes("renders/s1/r1/final.mp4", b"video-bytes", "video/mp4")
    info = provider.head("renders/s1/r1/final.mp4")
    assert info is not None and info.size == 11
    assert provider.sha256("renders/s1/r1/final.mp4") == hashlib.sha256(b"video-bytes").hexdigest()
    assert provider.head("renders/s1/r1/missing.mp4") is None
    plain = httpx.get(f"{s3_server}/studio-private/renders/s1/r1/final.mp4")
    assert plain.status_code == 403
    signed = provider.presign_get("renders/s1/r1/final.mp4", 60, "video.mp4", inline=False)
    assert "X-Amz-Signature" in signed and "X-Amz-Expires=60" in signed
    fetched = httpx.get(signed)
    assert fetched.status_code == 200 and fetched.content == b"video-bytes"
    assert "response-content-disposition=attachment" in signed
    target = provider.presign_put("uploads/u1/media.mp4", 60, "video/mp4")
    put = httpx.put(target["url"], content=b"abc", headers=target["headers"])
    assert put.status_code == 200 and provider.size("uploads/u1/media.mp4") == 3
    provider.download("uploads/u1/media.mp4", tmp_path / "copy.mp4")
    assert (tmp_path / "copy.mp4").read_bytes() == b"abc"
    provider.delete("uploads/u1/media.mp4")
    assert not provider.exists("uploads/u1/media.mp4")
    ok, _ = provider.ping()
    assert ok


def test_readiness_fails_when_the_bucket_serves_objects_without_a_signature(s3_server: str):
    provider = _s3(s3_server, f"studio-{uuid.uuid4().hex[:8]}")
    assert provider.check_private() == (True, "")
    leaky = _s3(s3_server, f"studio-{uuid.uuid4().hex[:8]}")
    leaky.put_bytes = lambda key, data, content_type="": leaky.client.put_object(Bucket=leaky.bucket, Key=key, Body=data, ACL="public-read")
    ok, detail = leaky.ping()
    assert not ok and "anonymous reads" in detail


# --------------------------------------------------------------------------- migrations


def test_migrations_create_upgrade_and_downgrade(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'm' / 'studio.db').as_posix()}"
    migrate(url)
    engine = get_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"users", "auth_sessions", "login_attempts", "uploads", "studio_sessions", "jobs", "plan_versions", "renders", "audit_events", "workers", "alembic_version"} <= tables
    migrate(url)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    assert "jobs" not in set(inspect(get_engine(url)).get_table_names())
    command.upgrade(config, "head")
    assert "jobs" in set(inspect(get_engine(url)).get_table_names())


def test_production_configuration_refuses_unsafe_settings(tmp_path: Path):
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        _settings(tmp_path, environment="production")
    good = _settings(
        tmp_path,
        environment="production",
        auth_mode="password",
        storage_backend="s3",
        storage_bucket="b",
        storage_access_key="a",
        storage_secret_key="s",
        database_url="postgresql+psycopg://user:pass@db/studio",
        job_runner="queue",
        session_secret="s" * 40,
        cookie_secure=True,
    )
    assert good.environment == "production"
    hosted = _settings(tmp_path, database_url="postgres://user:pass@db:5432/studio")
    assert hosted.resolved_database_url == "postgresql+psycopg://user:pass@db:5432/studio"
    assert _settings(tmp_path, database_url="postgresql://u:p@h/d").resolved_database_url == "postgresql+psycopg://u:p@h/d"
    assert _settings(tmp_path).resolved_database_url.startswith("sqlite:///")
    with pytest.raises(RuntimeError, match="requires secure cookies"):
        _settings(tmp_path, cookie_samesite="none")
    with pytest.raises(RuntimeError, match="STUDIO_COOKIE_SAMESITE"):
        _settings(tmp_path, cookie_samesite="sometimes")
    assert _settings(tmp_path, cookie_samesite="none", cookie_secure=True).cookie_samesite == "none"


# --------------------------------------------------------------------------- queue


def _session_row(store: StudioStore, owner: str) -> str:
    session_id = str(uuid.uuid4())
    store.write_session({"session_id": session_id, "client_id": owner, "mode": "my_broll", "status": "prepared", "raw_video": {}, "broll": [], "total_bytes": 0, "note": "", "created_at": "now"})
    return session_id


def test_queue_blocks_duplicates_replays_keys_and_claims_once(tmp_path: Path):
    store = StudioStore(_settings(tmp_path))
    migrate(store.settings.resolved_database_url)
    owner = str(uuid.uuid4())
    session_id = _session_row(store, owner)
    first, created = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="render", payload={"render_id": "r1"}, idempotency_key="click-1")
    assert created
    again, created = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="render", idempotency_key="click-1")
    assert not created and again["id"] == first["id"]
    with pytest.raises(Exception, match="already queued or running"):
        jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="render", idempotency_key="click-2")
    processing, created = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="process")
    assert created
    claimed_a = jobqueue.claim(store.db, "worker-a", 60, job_id=first["id"])
    claimed_b = jobqueue.claim(store.db, "worker-b", 60, job_id=first["id"])
    assert claimed_a is not None and claimed_b is None
    assert jobqueue.heartbeat(store.db, first["id"], "worker-a", 60)
    assert not jobqueue.heartbeat(store.db, first["id"], "worker-b", 60)
    jobqueue.finish(store.db, first["id"], "succeeded")
    next_render, created = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="render", idempotency_key="click-3")
    assert created and next_render["id"] != first["id"]
    assert processing["status"] == "queued"


def test_lost_worker_retries_processing_once_then_fails(tmp_path: Path):
    store = StudioStore(_settings(tmp_path))
    migrate(store.settings.resolved_database_url)
    owner = str(uuid.uuid4())
    session_id = _session_row(store, owner)
    record = store.get_session(session_id, owner)
    record.update({"status": "transcribing", "stage": "transcribing"})
    store.write_session(record)
    job, _ = jobqueue.enqueue(store.db, session_id=session_id, owner=owner, kind="process")
    for attempt in (1, 2):
        claimed = jobqueue.claim(store.db, f"worker-{attempt}", 60)
        assert claimed["attempts"] == attempt
        with store.db.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == job["id"]).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        recover_expired(store)
        state = jobqueue.get_job(store.db, job["id"])
        if attempt == 1:
            assert state["status"] == "queued" and state["failure_category"] == "worker_lost"
            assert store.get_session(session_id, owner)["status"] == "queued"
            record = store.get_session(session_id, owner)
            record.update({"status": "transcribing"})
            store.write_session(record)
    final = jobqueue.get_job(store.db, job["id"])
    assert final["status"] == "failed" and final["active_slot"] is None
    after = store.get_session(session_id, owner)
    assert after["status"] == "failed" and "worker shut down" in after["error"]


# --------------------------------------------------------------------------- authentication


def _password_app(tmp_path: Path, media: dict):
    settings = _settings(tmp_path, auth_mode="password", session_secret="k" * 40)
    services = Services(transcribe=lambda _p, _c: _transcript(), select_auto=lambda *_: [])
    app = create_app(settings, services, FakeBridge(media))
    from app.auth import create_user

    create_user(get_engine(settings.resolved_database_url), "manager@example.org", "correct horse battery staple", "Manager")
    create_user(get_engine(settings.resolved_database_url), "other@example.org", "another long password here", "Other")
    return TestClient(app), app


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_password_login_cookie_csrf_lockout_and_ownership(tmp_path: Path, media: dict):
    client, app = _password_app(tmp_path, media)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"X-Studio-Client": str(uuid.uuid4())}).status_code == 401
    wrong = client.post("/api/auth/login", json={"email": "manager@example.org", "password": "nope"})
    assert wrong.status_code == 401
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.org", "password": "nope"})
    assert unknown.status_code == 401 and unknown.json() == wrong.json()

    csrf = _login(client, "manager@example.org", "correct horse battery staple")
    cookie = client.cookies.get("sarthi_session")
    assert cookie
    raw_cookie = next(value for key, value in client.post("/api/auth/login", json={"email": "manager@example.org", "password": "correct horse battery staple"}).headers.multi_items() if key == "set-cookie")
    assert "HttpOnly" in raw_cookie and "SameSite=lax" in raw_cookie
    csrf = client.get("/api/auth/me").json()["csrf_token"]
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "manager@example.org" and "password_hash" not in json.dumps(me) and "scrypt$" not in json.dumps(me)

    target = client.post("/api/uploads/initiate", json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": media["lesson"].stat().st_size})
    assert target.status_code == 403 and target.json()["error"]["code"] == "csrf_failed"
    target = client.post("/api/uploads/initiate", headers={"X-CSRF-Token": csrf}, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": media["lesson"].stat().st_size}).json()
    put = client.put(target["upload"]["url"], content=media["lesson"].read_bytes(), headers=target["upload"]["headers"])
    assert put.status_code == 200
    done = client.post(f"/api/uploads/{target['upload_id']}/complete", headers={"X-CSRF-Token": csrf})
    assert done.status_code == 200 and done.json()["validation_status"] == "valid"
    created = client.post("/api/sessions", headers={"X-CSRF-Token": csrf}, json={"mode": "auto_edit", "raw_video_id": target["upload_id"], "broll": []})
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert [row["session_id"] for row in client.get("/api/sessions").json()["sessions"]] == [session_id]

    other = TestClient(app)
    _login(other, "other@example.org", "another long password here")
    assert other.get(f"/api/sessions/{session_id}").status_code == 404
    assert other.get(f"/api/sessions/{session_id}/video-url").status_code == 404
    assert other.get("/api/sessions").json()["sessions"] == []

    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/sessions").status_code == 401

    for _ in range(5):
        client.post("/api/auth/login", json={"email": "other@example.org", "password": "wrong password!!"})
    locked = client.post("/api/auth/login", json={"email": "other@example.org", "password": "another long password here"})
    assert locked.status_code == 429
    events = [row["action"] for row in app.state.store.db.connect().execute(select(__import__("app.db", fromlist=["audit_events"]).audit_events)).mappings()]
    assert "login" in events and "login_failed" in events and "session_created" in events


def test_direct_upload_rejects_mismatch_bad_media_and_forged_links(tmp_path: Path, media: dict):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, Services(transcribe=lambda *_: _transcript(), select_auto=lambda *_: []), FakeBridge(media)))
    owner = {"X-Studio-Client": str(uuid.uuid4())}
    size = media["lesson"].stat().st_size
    too_big = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": 999_999_999})
    assert too_big.status_code == 413
    bad_type = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "notes.exe", "size_bytes": 10})
    assert bad_type.status_code == 415
    target = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "../../lesson.mp4", "size_bytes": size}).json()
    assert target["upload"]["url"].startswith("/api/files/") and "lesson" not in target["upload"]["url"]
    forged = target["upload"]["url"][:-4] + "abcd"
    assert client.put(forged, content=b"x").status_code == 403
    too_long = client.put(target["upload"]["url"], content=b"x" * (size + 10))
    assert too_long.status_code == 413
    assert client.post(f"/api/uploads/{target['upload_id']}/complete", headers=owner).status_code == 409
    client.put(target["upload"]["url"], content=media["lesson"].read_bytes()[:-100])
    mismatch = client.post(f"/api/uploads/{target['upload_id']}/complete", headers=owner)
    assert mismatch.status_code == 422 and mismatch.json()["error"]["code"] == "size_mismatch"
    junk = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "fake.mp4", "size_bytes": 64}).json()
    client.put(junk["upload"]["url"], content=b"z" * 64)
    rejected = client.post(f"/api/uploads/{junk['upload_id']}/complete", headers=owner)
    assert rejected.status_code == 422
    assert not (settings.data_dir / "uploads" / junk["upload_id"] / "media.mp4").exists()
    stranger = {"X-Studio-Client": str(uuid.uuid4())}
    good = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": size}).json()
    client.put(good["upload"]["url"], content=media["lesson"].read_bytes())
    assert client.post(f"/api/uploads/{good['upload_id']}/complete", headers=stranger).status_code == 404
    valid = client.post(f"/api/uploads/{good['upload_id']}/complete", headers=owner).json()
    assert valid["validation_status"] == "valid" and valid["width"] == 360 and valid["duration_seconds"] > 7


def test_readiness_reports_each_dependency(tmp_path: Path, media: dict):
    client = TestClient(create_app(_settings(tmp_path, job_runner="queue"), Services(), FakeBridge(media)))
    body = client.get("/api/ready")
    assert body.status_code == 503
    checks = body.json()["checks"]
    assert checks["database"]["ok"] and checks["object_storage"]["ok"] and checks["queue"]["ok"] and checks["ffmpeg"]["ok"]
    assert checks["worker"]["ok"] is False
    jobqueue.record_worker(client.app.state.store.db, "worker-test", "t")
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/live").json()["status"] == "alive"
    assert mask("url?X-Amz-Signature=abc123&password=hunter2") == "url?X-Amz-Signature=[redacted]&password=[redacted]"


# --------------------------------------------------------------------------- production-shaped flow


def test_s3_queue_worker_flow_with_restart_retry_and_version_history(tmp_path: Path, media: dict, s3_server: str):
    """API and worker share only the database and the bucket; each has its own scratch disk."""
    bucket = f"studio-{uuid.uuid4().hex[:8]}"
    _s3(s3_server, bucket)
    database = f"sqlite:///{(tmp_path / 'shared' / 'studio.db').as_posix()}"
    common = {
        "storage_backend": "s3",
        "storage_endpoint": s3_server,
        "storage_bucket": bucket,
        "storage_access_key": "test-access",
        "storage_secret_key": "test-secret",
        "storage_region": "us-east-1",
        "database_url": database,
        "job_runner": "queue",
    }
    api_settings = _settings(tmp_path / "api", scratch_dir=tmp_path / "api_scratch", **common)
    worker_settings = _settings(tmp_path / "worker", scratch_dir=tmp_path / "worker_scratch", **common)
    services = Services(transcribe=lambda _p, _c: _transcript(), select_auto=lambda *_: [])
    app = create_app(api_settings, services, FakeBridge(media))
    client = TestClient(app)
    worker_store = StudioStore(worker_settings)
    db = worker_store.db
    bridge = FakeBridge(media)
    owner = {"X-Studio-Client": str(uuid.uuid4())}

    def upload(path: Path, kind: str, description: str | None = None) -> dict:
        body = {"kind": kind, "filename": path.name, "size_bytes": path.stat().st_size}
        if description:
            body["description"] = description
        target = client.post("/api/uploads/initiate", headers=owner, json=body).json()
        assert target["upload"]["url"].startswith(s3_server)
        assert httpx.put(target["upload"]["url"], content=path.read_bytes(), headers=target["upload"]["headers"]).status_code == 200
        done = client.post(f"/api/uploads/{target['upload_id']}/complete", headers=owner)
        assert done.status_code == 200, done.text
        return done.json()

    raw = upload(media["lesson"], "raw")
    clip = upload(media["broll"], "broll", DESCRIPTION)
    assert not (tmp_path / "api_scratch" / "uploads").exists()
    created = client.post(
        "/api/sessions",
        headers=owner,
        json={"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": [{"asset_id": clip["asset_id"], "description": DESCRIPTION}], "title": "From [[Notes]] to UPSC"},
    ).json()
    session_id = created["session_id"]
    queued = client.post(f"/api/sessions/{session_id}/process", headers={**owner, "Idempotency-Key": "process-1"})
    assert queued.status_code == 200 and queued.json()["status"] == "queued"
    replay = client.post(f"/api/sessions/{session_id}/process", headers={**owner, "Idempotency-Key": "process-1"})
    assert replay.status_code == 200
    assert len(jobqueue.session_jobs(db, session_id)) == 1

    job = jobqueue.claim(db, "worker-1", 60)
    assert job["kind"] == "process"
    assert execute(worker_store, job, services, bridge, "worker-1", 60) == "succeeded"
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=owner).json()
    assert plan["plan_version"]["number"] == 1 and plan["title"]["text"] == "From [[Notes]] to UPSC"
    assert len(plan["placements"]) == 1
    v1_key = f"work/{session_id}/plans/{plan['plan_version']['version_id']}.json"
    v1_bytes = worker_store.objects.get_bytes(v1_key)

    first = client.post(f"/api/sessions/{session_id}/render", headers={**owner, "Idempotency-Key": "render-a"})
    double = client.post(f"/api/sessions/{session_id}/render", headers={**owner, "Idempotency-Key": "render-b"})
    assert first.status_code == 200 and double.status_code == 409
    replayed = client.post(f"/api/sessions/{session_id}/render", headers={**owner, "Idempotency-Key": "render-a"})
    assert replayed.status_code == 200 and replayed.json()["render"]["render_id"] == first.json()["render"]["render_id"]
    lost_render_id = first.json()["render"]["render_id"]
    lost = jobqueue.claim(db, "worker-crashed", 60)
    assert lost["kind"] == "render"
    with db.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == lost["id"]).values(lease_expires_at=utcnow() - timedelta(seconds=5)))
    recover_expired(worker_store)
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()
    assert status["status"] == "failed" and "worker shut down" in status["error"]
    assert worker_store.objects.exists(f"renders/{session_id}/{lost_render_id}/failure.json")

    retry = client.post(f"/api/sessions/{session_id}/render", headers={**owner, "Idempotency-Key": "render-c"})
    assert retry.status_code == 200
    render_id = retry.json()["render"]["render_id"]
    assert render_id != lost_render_id
    job = jobqueue.claim(db, "worker-2", 60)
    assert execute(worker_store, job, services, bridge, "worker-2", 60) == "succeeded"
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()
    assert status["status"] == "completed" and status["plan_version_id"] == plan["plan_version"]["version_id"]
    links = client.get(f"/api/sessions/{session_id}/video-url", headers=owner).json()
    assert links["url"].startswith(s3_server) and "X-Amz-Signature" in links["url"]
    video = httpx.get(links["url"])
    assert video.status_code == 200 and video.content[4:8] == b"ftyp"
    first_hash = hashlib.sha256(video.content).hexdigest()
    assert client.get(f"/api/sessions/{session_id}/video-url", headers={"X-Studio-Client": str(uuid.uuid4())}).status_code == 404
    redirect = client.get(f"/api/sessions/{session_id}/download", headers=owner, follow_redirects=False)
    assert redirect.status_code == 307 and "X-Amz-Signature" in redirect.headers["location"]
    assert not (tmp_path / "worker_scratch" / "uploads" / raw["upload_id"]).exists()

    edited = client.post(
        f"/api/sessions/{session_id}/editing-plan/edits",
        headers=owner,
        json={"base_version_id": plan["plan_version"]["version_id"], "placements": [], "title": "Second cut", "change_title": True},
    )
    assert edited.status_code == 200, edited.text
    v2 = edited.json()["plan_version"]
    assert v2["parent_version_id"] == plan["plan_version"]["version_id"]
    assert worker_store.objects.get_bytes(v1_key) == v1_bytes
    assert worker_store.objects.exists(f"work/{session_id}/plans/{v2['version_id']}.json")
    client.post(f"/api/sessions/{session_id}/render", headers=owner)
    job = jobqueue.claim(db, "worker-3", 60)
    assert execute(worker_store, job, services, bridge, "worker-3", 60) == "succeeded"
    latest = client.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()
    assert latest["plan_version_id"] == v2["version_id"] and latest["title"]["text"] == "Second cut"
    old = httpx.get(worker_store.objects.presign_get(f"renders/{session_id}/{render_id}/final.mp4", 60))
    assert hashlib.sha256(old.content).hexdigest() == first_hash

    with db.connect() as conn:
        versions = sorted((row.number, row.object_key) for row in conn.execute(select(plan_versions).where(plan_versions.c.session_id == session_id)))
        render_rows = {row.id: (row.status, row.plan_version_id) for row in conn.execute(select(renders).where(renders.c.session_id == session_id))}
    assert [number for number, _ in versions] == [1, 2]
    assert render_rows[lost_render_id][0] == "failed"
    assert render_rows[render_id] == ("completed", plan["plan_version"]["version_id"])
    assert len(render_rows) == 3
    restarted = TestClient(create_app(api_settings, services, FakeBridge(media)))
    assert restarted.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()["status"] == "completed"
    assert [row["session_id"] for row in restarted.get("/api/sessions", headers=owner).json()["sessions"]] == [session_id]


def test_worker_publishes_files_before_announcing_results(tmp_path: Path, media: dict, s3_server: str):
    """The API must never see plan_ready or completed while the files are still on the worker's disk."""
    bucket = f"studio-{uuid.uuid4().hex[:8]}"
    _s3(s3_server, bucket)
    common = {
        "storage_backend": "s3", "storage_endpoint": s3_server, "storage_bucket": bucket, "storage_access_key": "test-access",
        "storage_secret_key": "test-secret", "storage_region": "us-east-1", "job_runner": "queue",
        "database_url": f"sqlite:///{(tmp_path / 'shared' / 'studio.db').as_posix()}",
    }
    services = Services(transcribe=lambda _p, _c: _transcript(), select_auto=lambda *_: [])
    client = TestClient(create_app(_settings(tmp_path / "api", scratch_dir=tmp_path / "api_scratch", **common), services, FakeBridge(media)))
    worker_store = StudioStore(_settings(tmp_path / "worker", scratch_dir=tmp_path / "worker_scratch", **common))
    seen: list[tuple[str, bool]] = []
    original = worker_store.write_session

    def guarded(record: dict) -> None:
        sid = record["session_id"]
        if record.get("status") == "plan_ready":
            keys = (f"work/{sid}/transcript.json", f"work/{sid}/plans/{record['plan_version']}.json")
            seen.append(("plan_ready", all(worker_store.objects.exists(key) for key in keys)))
        render = record.get("render") or {}
        if render.get("status") == "completed":
            seen.append(("completed", worker_store.objects.exists(f"renders/{sid}/{render['render_id']}/final.mp4")))
        original(record)

    worker_store.write_session = guarded
    owner = {"X-Studio-Client": str(uuid.uuid4())}
    size = media["lesson"].stat().st_size
    target = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": size}).json()
    httpx.put(target["upload"]["url"], content=media["lesson"].read_bytes(), headers=target["upload"]["headers"])
    client.post(f"/api/uploads/{target['upload_id']}/complete", headers=owner)
    session_id = client.post("/api/sessions", headers=owner, json={"mode": "auto_edit", "raw_video_id": target["upload_id"], "broll": []}).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=owner)
    assert execute(worker_store, jobqueue.claim(worker_store.db, "w", 60), services, FakeBridge(media), "w", 60) == "succeeded"
    client.post(f"/api/sessions/{session_id}/render", headers=owner)
    assert execute(worker_store, jobqueue.claim(worker_store.db, "w", 60), services, FakeBridge(media), "w", 60) == "succeeded"
    assert {kind for kind, _ in seen} == {"plan_ready", "completed"}
    assert all(present for _, present in seen), seen


def test_inline_runner_survives_api_restart_without_losing_sessions(tmp_path: Path, media: dict):
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings, Services(transcribe=lambda *_: _transcript(), select_auto=lambda *_: []), FakeBridge(media)))
    owner = {"X-Studio-Client": str(uuid.uuid4())}
    size = media["lesson"].stat().st_size
    target = client.post("/api/uploads/initiate", headers=owner, json={"kind": "raw", "filename": "lesson.mp4", "size_bytes": size}).json()
    client.put(target["upload"]["url"], content=media["lesson"].read_bytes())
    client.post(f"/api/uploads/{target['upload_id']}/complete", headers=owner)
    session_id = client.post("/api/sessions", headers=owner, json={"mode": "auto_edit", "raw_video_id": target["upload_id"], "broll": []}).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=owner)
    assert client.get(f"/api/sessions/{session_id}/status", headers=owner).json()["status"] == "plan_ready"
    again = TestClient(create_app(settings, Services(), FakeBridge(media)))
    assert again.get(f"/api/sessions/{session_id}/status", headers=owner).json()["status"] == "plan_ready"
    assert again.get(f"/api/sessions/{session_id}/editing-plan", headers=owner).status_code == 200
    _ = run_inline, time


# --------------------------------------------------------------------------- optional import of older local data


def _legacy_tree(root: Path, client: str) -> dict:
    """The pre-database layout: uploads/<id>/meta.json, sessions/<id>.json, work/, renders/."""
    upload_id, done_id, busy_id, render_id = (str(uuid.uuid4()) for _ in range(4))
    folder = root / "uploads" / upload_id
    folder.mkdir(parents=True)
    (folder / "media.mp4").write_bytes(b"lesson-bytes")
    meta = {"upload_id": upload_id, "kind": "raw", "client_id": client, "filename": "lesson.mp4", "size_bytes": 12, "validation_status": "valid", "stored_name": "media.mp4", "duration_seconds": 8.0}
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    raw = {"upload_id": upload_id, "filename": "lesson.mp4", "size_bytes": 12, "validation_status": "valid"}
    done = {
        "session_id": done_id, "client_id": client, "mode": "auto_edit", "status": "plan_ready", "raw_video": raw, "broll": [], "total_bytes": 12, "note": "", "created_at": "2026-10-29T10:00:00+00:00",
        "render": {"render_id": render_id, "status": "completed", "plan_version_id": None},
    }
    busy = {**done, "session_id": busy_id, "status": "transcribing", "render": None}
    (root / "sessions").mkdir()
    for record in (done, busy):
        (root / "sessions" / f"{record['session_id']}.json").write_text(json.dumps(record), encoding="utf-8")
    (root / "work" / done_id).mkdir(parents=True)
    (root / "work" / done_id / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
    (root / "renders" / done_id / render_id).mkdir(parents=True)
    (root / "renders" / done_id / render_id / "final.mp4").write_bytes(b"final-video")
    return {"upload": upload_id, "done": done_id, "busy": busy_id, "render": render_id}


def test_import_tool_copies_legacy_sessions_into_storage_and_database(tmp_path: Path, s3_server: str):
    from app.auth import create_user
    from app.db import studio_sessions, uploads
    from app.tools.import_local import Importer, _owner_id

    legacy = tmp_path / "legacy"
    ids = _legacy_tree(legacy, str(uuid.uuid4()))
    before = {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    bucket = f"studio-{uuid.uuid4().hex[:8]}"
    _s3(s3_server, bucket)
    settings = _settings(
        tmp_path / "target", storage_backend="s3", storage_endpoint=s3_server, storage_bucket=bucket,
        storage_access_key="test-access", storage_secret_key="test-secret", storage_region="us-east-1", scratch_dir=tmp_path / "scratch",
    )
    migrate(settings.resolved_database_url)
    store = StudioStore(settings)
    user_id = create_user(store.db, "manager@example.org", "correct horse battery staple", "Manager")
    live = _session_row(store, user_id)
    store.write_session({**store.get_session(live, user_id), "status": "transcribing"})

    dry = Importer(store, legacy, _owner_id(store, "manager@example.org"), apply=False)
    dry.uploads()
    dry.sessions()
    assert dry.counts["uploads"] == 1 and dry.counts["sessions"] == 2
    assert not store.objects.exists(f"uploads/{ids['upload']}/media.mp4")
    with store.db.connect() as conn:
        assert conn.execute(select(uploads)).first() is None

    real = Importer(store, legacy, user_id, apply=True)
    real.uploads()
    real.sessions()
    real.caches()
    assert store.objects.get_bytes(f"renders/{ids['done']}/{ids['render']}/final.mp4") == b"final-video"
    assert store.objects.exists(f"work/{ids['done']}/transcript.json")
    meta = store.get_upload(ids["upload"], user_id)
    assert meta["sha256"] == hashlib.sha256(b"lesson-bytes").hexdigest() and meta["duration_seconds"] == 8.0
    assert store.get_session(ids["done"], user_id)["status"] == "plan_ready"
    assert store.get_session(ids["busy"], user_id)["status"] == "failed"
    assert store.get_session(live, user_id)["status"] == "transcribing"
    with store.db.connect() as conn:
        assert conn.execute(select(renders.c.status).where(renders.c.id == ids["render"])).scalar_one() == "completed"
        assert {row.id for row in conn.execute(select(studio_sessions.c.id))} == {ids["done"], ids["busy"], live}

    again = Importer(store, legacy, user_id, apply=True)
    again.uploads()
    again.sessions()
    assert again.counts["skipped"] == 3 and again.counts["files"] == 0
    assert {path: path.read_bytes() for path in legacy.rglob("*") if path.is_file()} == before
