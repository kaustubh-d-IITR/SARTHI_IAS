"""Production-shaped local rehearsal of the deployed topology. It is not a deployment.

Starts, on this machine: an S3-compatible server (moto), the API with password sign-in,
direct uploads and the queue runner, and a separate worker process running the real
engine (HyperFrames render, outro, audio check). Then it drives one lesson through the
same HTTP calls the browser makes: sign in, upload, process, review, edit, render,
preview, download.

No paid call can happen: ELEVENLABS_API_KEY and GEMINI_API_KEY are set to empty in the
child processes (an existing variable wins over the engine .env), and the transcript is
seeded into the cache from --transcript, keyed by the lesson's SHA-256. Commons footage
for Mode A is free and goes through the existing licence checks.

Run from saarthi_video_engine/api with the API virtualenv (moto comes from requirements-dev.txt):
    .venv/Scripts/python ../deploy/smoke_local.py --mode my_broll --raw L.mp4 --transcript T.json \
        --broll B.mp4 --description "..." --out <empty dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

API_DIR = Path(__file__).resolve().parents[1] / "api"
ENGINE_PYTHON = Path(os.environ.get("STUDIO_ENGINE_PYTHON") or r"C:\Users\shubh\AppData\Local\Programs\Python\Python313\python.exe")
EMAIL = "smoke@example.invalid"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    return {"width": video["width"], "height": video["height"], "fps": round(num / den, 3), "duration": round(float(data["format"]["duration"]), 2), "audio": any(s["codec_type"] == "audio" for s in data["streams"])}


class Smoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = args.out.resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.log: list[dict] = []
        self.procs: list[subprocess.Popen] = []
        self.started = time.time()

    def note(self, step: str, **facts) -> None:
        row = {"t": round(time.time() - self.started, 1), "step": step, **facts}
        self.log.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    # ----------------------------------------------------------------- services

    def start_storage(self) -> None:
        from moto.server import ThreadedMotoServer

        port = free_port()
        self.moto = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
        self.moto.start()
        self.endpoint = f"http://127.0.0.1:{port}"
        self.bucket = f"sarthi-smoke-{uuid.uuid4().hex[:8]}"
        import boto3

        self.s3 = boto3.client("s3", endpoint_url=self.endpoint, aws_access_key_id="smoke", aws_secret_access_key="smoke", region_name="us-east-1")
        self.s3.create_bucket(Bucket=self.bucket)
        self.note("object storage up", endpoint=self.endpoint, bucket=self.bucket)

    def env(self, role: str) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STUDIO_", "OBJECT_STORAGE_"))}
        env.update(
            {
                "STUDIO_ENV": "development",
                "STUDIO_DATA_DIR": str(self.out / f"{role}_data"),
                "STUDIO_SCRATCH_DIR": str(self.out / f"{role}_scratch"),
                "DATABASE_URL": f"sqlite:///{(self.out / 'shared' / 'studio.db').as_posix()}",
                "STUDIO_AUTO_MIGRATE": "false",
                "STUDIO_STORAGE_BACKEND": "s3",
                "OBJECT_STORAGE_ENDPOINT": self.endpoint,
                "OBJECT_STORAGE_BUCKET": self.bucket,
                "OBJECT_STORAGE_ACCESS_KEY": "smoke",
                "OBJECT_STORAGE_SECRET_KEY": "smoke",
                "OBJECT_STORAGE_REGION": "us-east-1",
                "STUDIO_AUTH_MODE": "password",
                "STUDIO_SESSION_SECRET": self.secret,
                "STUDIO_COOKIE_SECURE": "false",
                "STUDIO_JOB_RUNNER": "queue",
                "STUDIO_CORS_ORIGINS": "http://127.0.0.1:5173",
                "STUDIO_VISUAL_PROVIDER": self.args.visual,
                "STUDIO_ENGINE_PYTHON": str(ENGINE_PYTHON),
                "STUDIO_WORKER_POLL_SECONDS": "1",
                "STUDIO_KEEP_SCRATCH": "1" if self.args.keep_scratch else "",
                "ELEVENLABS_API_KEY": "",
                "GEMINI_API_KEY": "",
                "PYTHONIOENCODING": "utf-8",
                "CUDA_VISIBLE_DEVICES": "",
            }
        )
        return env

    def prepare_database(self) -> None:
        self.secret = secrets.token_urlsafe(48)
        self.password = secrets.token_urlsafe(24)
        (self.out / "shared").mkdir(exist_ok=True)
        os.environ.update({k: v for k, v in self.env("api").items() if k.startswith(("STUDIO_", "OBJECT_STORAGE_", "DATABASE_URL"))})
        sys.path.insert(0, str(API_DIR))
        from app.auth import create_user
        from app.config import Settings
        from app.db import get_engine, migrate

        settings = Settings.from_env()
        migrate(settings.resolved_database_url)
        create_user(get_engine(settings.resolved_database_url), EMAIL, self.password, "Smoke test")
        self.note("database migrated, throwaway account created", email=EMAIL)

    def seed_transcript(self) -> None:
        digest = sha256(self.args.raw)
        payload = json.loads(self.args.transcript.read_text(encoding="utf-8"))
        for key in ("source_file", "source_media", "audio_extract", "reused"):
            payload.pop(key, None)
        payload["source_sha256"] = digest
        self.s3.put_object(Bucket=self.bucket, Key=f"transcript_cache/{digest}.json", Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"), ContentType="application/json")
        self.note("transcript cache seeded", sha256=digest[:16], segments=len(payload.get("segments") or []))

    def spawn(self, role: str, command: list[str]) -> None:
        handle = (self.out / f"{role}.log").open("w", encoding="utf-8")
        proc = subprocess.Popen(command, cwd=API_DIR, env=self.env(role), stdout=handle, stderr=subprocess.STDOUT)
        self.procs.append(proc)

    def start_processes(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.spawn("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)])
        self.spawn("worker", [sys.executable, "-m", "app.worker"])
        for _ in range(120):
            try:
                if httpx.get(f"{self.base}/api/live", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit("the API did not start; see api.log")
        self.note("api and worker processes started", api_port=self.port)

    def stop(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if hasattr(self, "moto"):
            self.moto.stop()

    # ----------------------------------------------------------------- browser flow

    def call(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if method != "GET" and getattr(self, "csrf", None):
            headers["X-CSRF-Token"] = self.csrf
        return self.http.request(method, path, headers=headers, **kwargs)

    def sign_in(self) -> None:
        self.http = httpx.Client(base_url=self.base, timeout=120)
        assert self.http.get("/api/auth/me").status_code == 401
        assert self.http.get("/api/sessions").status_code == 401
        bad = self.http.post("/api/auth/login", json={"email": EMAIL, "password": "not-the-password"})
        assert bad.status_code == 401
        good = self.http.post("/api/auth/login", json={"email": EMAIL, "password": self.password})
        assert good.status_code == 200, good.text
        self.csrf = good.json()["csrf_token"]
        no_csrf = self.http.post("/api/sessions", json={})
        assert no_csrf.status_code == 403
        cookie = good.headers.get("set-cookie", "")
        self.note("signed in", httponly="httponly" in cookie.lower(), samesite_lax="samesite=lax" in cookie.lower(), unauthenticated=401, missing_csrf=403)
        deadline = time.time() + 60
        while time.time() < deadline:
            ready = self.http.get("/api/ready")
            if ready.status_code == 200:
                break
            time.sleep(1)
        self.note("readiness", status=ready.status_code, checks={k: v.get("ok") for k, v in ready.json().get("checks", {}).items()})

    def upload(self, path: Path, kind: str, description: str | None = None) -> dict:
        body = {"kind": kind, "filename": path.name, "size_bytes": path.stat().st_size}
        if description:
            body["description"] = description
        started = self.call("POST", "/api/uploads/initiate", json=body)
        assert started.status_code == 200, started.text
        target = started.json()
        assert target["upload"]["url"].startswith(self.endpoint), "upload must go straight to object storage"
        put = httpx.put(target["upload"]["url"], content=path.read_bytes(), headers=target["upload"]["headers"], timeout=300)
        assert put.status_code == 200, put.text
        done = self.call("POST", f"/api/uploads/{target['upload_id']}/complete")
        assert done.status_code == 200, done.text
        meta = done.json()
        self.note("uploaded directly to storage", kind=kind, bytes=path.stat().st_size, duration=meta.get("duration_seconds"), size=f"{meta.get('width')}x{meta.get('height')}")
        return meta

    def wait(self, path: str, done: set[str], minutes: float) -> dict:
        deadline = time.time() + minutes * 60
        last = None
        while time.time() < deadline:
            body = self.call("GET", path).json()
            stage = (body.get("stage") or body.get("status"), body.get("status"))
            if stage != last:
                self.note("status", path=path.rsplit("/", 1)[-1], stage=stage[0], status=stage[1])
                last = stage
            if body.get("status") in done:
                return body
            time.sleep(3)
        raise SystemExit(f"timed out waiting on {path}")

    def run(self) -> dict:
        args = self.args
        raw = self.upload(args.raw, "raw")
        broll = []
        if args.mode == "my_broll":
            for path, description in zip(args.broll, args.description):
                clip = self.upload(path, "broll", description)
                broll.append({"asset_id": clip["upload_id"], "description": description})
        music_upload = self.upload(args.music, "music") if args.music else None
        created = self.call("POST", "/api/sessions", json={"mode": args.mode, "raw_video_id": raw["upload_id"], "broll": broll})
        assert created.status_code == 200, created.text
        sid = created.json()["session_id"]
        self.note("session created", mode=args.mode, session_id=sid)

        first = self.call("POST", f"/api/sessions/{sid}/process", headers={"Idempotency-Key": "smoke-process"})
        replay = self.call("POST", f"/api/sessions/{sid}/process", headers={"Idempotency-Key": "smoke-process"})
        assert first.status_code == 200 and replay.status_code == 200, (first.text, replay.text)
        self.note("processing queued", replay_status=replay.status_code)
        status = self.wait(f"/api/sessions/{sid}/status", {"plan_ready", "failed"}, args.process_minutes)
        if status["status"] != "plan_ready":
            self.note("processing failed", error=status.get("error"))
            return {"session_id": sid, "ok": False}

        transcript_response = self.call("GET", f"/api/sessions/{sid}/transcript")
        plan_response = self.call("GET", f"/api/sessions/{sid}/editing-plan")
        assert transcript_response.status_code == 200 and plan_response.status_code == 200, (transcript_response.text[:200], plan_response.text[:200])
        transcript, plan = transcript_response.json(), plan_response.json()
        footage = [row.get("asset_id") or row.get("source_id") for row in plan.get("placements") or []]
        self.note(
            "review data",
            transcript_reused=transcript.get("reused"),
            transcript_method=transcript.get("method"),
            plan_version=(plan.get("plan_version") or {}).get("number"),
            placements=len(plan.get("placements") or []),
            visual=(plan.get("visual_analysis") or {}).get("provider"),
            visual_performed=(plan.get("visual_analysis") or {}).get("performed"),
        )
        for row in plan.get("placements") or []:
            self.note("placement", asset=row.get("filename"), start=row.get("speech_start"), end=row.get("speech_end"), phrase=row.get("matched_phrase"), rule=row.get("placement_rule"), confidence=row.get("confidence"))
        if args.mode == "my_broll":
            own = {item["asset_id"] for item in broll}
            assert all(item in own for item in footage), "Mode B placed footage the user did not upload"
        for message in args.assistant:
            reply = self.call("POST", f"/api/sessions/{sid}/assistant/propose", json={"message": message}).json()
            assert reply.get("status") == "proposal", reply
            applied = self.call("POST", f"/api/sessions/{sid}/assistant/apply", json={"base_version_id": reply["base_version_id"], "operations": reply["operations"]})
            assert applied.status_code == 200, applied.text
            plan = applied.json()
            self.note("assistant applied", message=message, understood=reply["understood"], plan_version=plan["plan_version"]["number"], origin=plan["plan_version"]["origin"])
            for row in plan.get("placements") or []:
                self.note("placement after assistant", asset=row.get("filename"), start=row.get("speech_start"), end=row.get("speech_end"), phrase=row.get("matched_phrase"))
        raw_settings = Path(args.settings[1:]).read_text(encoding="utf-8") if args.settings and args.settings.startswith("@") else args.settings
        settings = json.loads(raw_settings) if raw_settings else {}
        if music_upload:
            settings.setdefault("music", {}).update({"source": "upload", "upload_id": music_upload["upload_id"]})
        if args.mode == "my_broll" or settings or args.title:
            edits = [{key: row[key] for key in ("asset_id", "speech_start", "speech_end", "source_in", "source_out", "approval")} for row in plan.get("placements") or []]
            body = {"base_version_id": plan["plan_version"]["version_id"], "placements": edits, "title": args.title, "change_title": True}
            if settings:
                body["settings"] = settings
            edited = self.call("POST", f"/api/sessions/{sid}/editing-plan/edits", json=body)
            assert edited.status_code == 200, edited.text
            plan = edited.json()
            self.note("plan edited (title and settings)", plan_version=plan["plan_version"]["number"], settings=sorted(settings))

        render = self.call("POST", f"/api/sessions/{sid}/render", headers={"Idempotency-Key": "smoke-render-1"})
        double = self.call("POST", f"/api/sessions/{sid}/render", headers={"Idempotency-Key": "smoke-render-2"})
        assert render.status_code == 200, render.text
        self.note("render queued", second_click=double.status_code)
        state = self.wait(f"/api/sessions/{sid}/render-status", {"completed", "failed"}, args.render_minutes)
        if state["status"] != "completed":
            self.note("render failed", error=state.get("error"))
            return {"session_id": sid, "ok": False}
        rid = state["render_id"]

        links = self.call("GET", f"/api/sessions/{sid}/video-url").json()
        assert links["url"].startswith(self.endpoint) and "X-Amz-Signature" in links["url"]
        unsigned = httpx.get(f"{self.endpoint}/{self.bucket}/renders/{sid}/{rid}/final.mp4")
        preview = httpx.get(links["url"], timeout=300)
        final = self.out / f"final_{args.mode}.mp4"
        final.write_bytes(preview.content)
        redirect = self.call("GET", f"/api/sessions/{sid}/download", follow_redirects=False)
        downloaded = httpx.get(redirect.headers["location"], timeout=300)
        same = hashlib.sha256(downloaded.content).hexdigest() == hashlib.sha256(preview.content).hexdigest()
        keys = [obj["Key"] for obj in self.s3.list_objects_v2(Bucket=self.bucket, Prefix=f"renders/{sid}/{rid}/").get("Contents", [])]
        logs = [key for key in keys if key.endswith(("render.txt", "lint.txt"))]
        render_log = self.s3.get_object(Bucket=self.bucket, Key=next(key for key in logs if key.endswith("render.txt")))["Body"].read().decode("utf-8", "replace") if logs else ""
        self.note(
            "video served",
            preview_status=preview.status_code,
            unsigned_status=unsigned.status_code,
            download_status=redirect.status_code,
            download_matches_preview=same,
            probe=ffprobe(final),
            title=(state.get("title") or {}).get("text"),
            audio_correlation=(state.get("audio_verification") or {}).get("correlation"),
            hyperframes_logs=[key.split(f"{rid}/", 1)[1] for key in logs],
            hyperframes_log_mentions_render="render" in render_log.lower(),
            objects_in_render_prefix=len(keys),
            quality=(state.get("quality") or {}).get("status"),
            quality_checks={item["name"]: item["status"] for item in (state.get("quality") or {}).get("checks") or []},
        )
        return {"session_id": sid, "render_id": rid, "ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local production-shaped rehearsal (not a deployment).")
    parser.add_argument("--mode", choices=("my_broll", "auto_edit"), required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--broll", type=Path, action="append", default=[])
    parser.add_argument("--description", action="append", default=[])
    parser.add_argument("--music", type=Path, help="Upload this audio file as the custom music track.")
    parser.add_argument("--settings", help="JSON of studio settings to apply before rendering.")
    parser.add_argument("--title", default="Smoke [[Test]]")
    parser.add_argument("--keep-scratch", action="store_true", help="Keep the worker's scratch folder for inspection.")
    parser.add_argument("--assistant", action="append", default=[], help="An editing instruction to propose and apply before rendering.")
    parser.add_argument("--visual", default="auto")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--process-minutes", type=float, default=30)
    parser.add_argument("--render-minutes", type=float, default=40)
    args = parser.parse_args()
    if args.mode == "my_broll" and (not args.broll or len(args.broll) != len(args.description)):
        parser.error("my_broll needs one --description per --broll")
    smoke = Smoke(args)
    result: dict = {"ok": False}
    try:
        smoke.start_storage()
        smoke.prepare_database()
        smoke.seed_transcript()
        smoke.start_processes()
        smoke.sign_in()
        result = smoke.run()
    finally:
        smoke.stop()
        summary = {"result": result, "steps": smoke.log}
        (smoke.out / "smoke_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
