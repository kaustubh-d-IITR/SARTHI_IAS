"""Phase 1 API tests. They use a generated tiny video and never call the lesson renderer."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ENGINE_ROOT, Settings
from app.factory import create_app

RAW = ENGINE_ROOT.parent / "DR_SHIVIN_RAW"
OUTPUTS = ENGINE_ROOT / "outputs"


@pytest.fixture(scope="session")
def tiny_mp4(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("fixture") / "tiny.mp4"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x123456:s=160x160:r=30:d=0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        pytest.skip("ffmpeg could not write a tiny test video")
    return destination


@pytest.fixture()
def studio(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "studio",
        max_file_bytes=8_000_000,
        max_session_bytes=16_000_000,
        cors_origins=("http://localhost:5173",),
    )
    client = TestClient(create_app(settings))
    owner = str(uuid.uuid4())
    return client, owner, settings


def _headers(owner: str) -> dict[str, str]:
    return {"X-Studio-Client": owner}


def _video(path: Path, name: str = "lesson.mp4"):
    return {"file": (name, path.read_bytes(), "video/mp4")}


def test_health(studio):
    client, _owner, settings = studio
    response = client.get("/api/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["upload_directory_writable"] is True
    assert body["configuration"]["ffprobe_available"] is True
    assert body["configuration"]["credentials_loaded"] is False
    assert body["configuration"]["max_file_bytes"] == settings.max_file_bytes
    assert "mp4" in body["configuration"]["allowed_extensions"]
    assert "ELEVEN" not in response.text
    assert "GEMINI" not in response.text


def test_valid_raw_upload_and_safe_name(studio, tiny_mp4: Path):
    client, owner, settings = studio
    response = client.post(
        "/api/uploads/raw-video",
        headers=_headers(owner),
        files=_video(tiny_mp4, r"..\..\DR_SHIVIN_RAW\VIDEO_1.mp4"),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["validation_status"] == "valid"
    assert body["width"] == 160
    assert body["height"] == 160
    assert body["duration_seconds"] > 0
    assert "\\" not in body["filename"]
    assert "/" not in body["filename"]
    assert "stored_name" not in body
    assert "client_id" not in body
    folder = settings.data_dir / "uploads" / body["upload_id"]
    assert (folder / "media.mp4").is_file()
    stored = client.app.state.store.get_upload(body["upload_id"], owner)
    assert stored["object_key"] == f"uploads/{body['upload_id']}/media.mp4"
    assert stored["sha256"] and len(stored["sha256"]) == 64
    assert not (folder / "media.partial").exists()
    assert settings.data_dir.resolve() in folder.resolve().parents
    assert OUTPUTS.resolve() not in folder.resolve().parents
    assert RAW.resolve() not in folder.resolve().parents


def test_invalid_extension(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    response = client.post(
        "/api/uploads/raw-video",
        headers=_headers(owner),
        files=_video(tiny_mp4, "notes.exe"),
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_type"


def test_invalid_video_bytes_are_removed(studio):
    client, owner, settings = studio
    response = client.post(
        "/api/uploads/raw-video",
        headers=_headers(owner),
        files={"file": ("broken.mp4", b"this is not a video", "video/mp4")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unreadable_video"
    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_empty_upload(studio):
    client, owner, settings = studio
    response = client.post(
        "/api/uploads/raw-video",
        headers=_headers(owner),
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_upload"
    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_file_size_limit(tiny_mp4: Path, tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "studio",
        max_file_bytes=100,
        max_session_bytes=100,
        cors_origins=(),
    )
    client = TestClient(create_app(settings))
    owner = str(uuid.uuid4())
    response = client.post(
        "/api/uploads/raw-video",
        headers=_headers(owner),
        files=_video(tiny_mp4),
    )
    assert response.status_code == 413
    uploads = settings.data_dir / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_broll_requires_description(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    missing = client.post("/api/uploads/broll", headers=_headers(owner), files=_video(tiny_mp4, "clip.mp4"))
    blank = client.post(
        "/api/uploads/broll",
        headers=_headers(owner),
        data={"description": "   "},
        files=_video(tiny_mp4, "clip.mp4"),
    )
    assert missing.status_code == 422
    assert blank.status_code == 422
    assert missing.json()["error"]["code"] == "description_required"


def test_broll_upload_keeps_description(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    response = client.post(
        "/api/uploads/broll",
        headers=_headers(owner),
        data={"description": "A student watching a lecture on a smartphone."},
        files=_video(tiny_mp4, "phone.mp4"),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["asset_id"] == body["upload_id"]
    assert body["description"] == "A student watching a lecture on a smartphone."
    assert body["validation_status"] == "valid"


def test_replace_broll_before_session(studio, tiny_mp4: Path):
    client, owner, settings = studio
    first = _broll(client, owner, tiny_mp4, "A person writing notes in a notebook.")
    deleted = client.delete(f"/api/uploads/{first['asset_id']}", headers=_headers(owner))
    assert deleted.status_code == 204
    assert not (settings.data_dir / "uploads" / first["asset_id"]).exists()
    second = _broll(client, owner, tiny_mp4, "An aerial view of the Indian Parliament.")
    raw = _raw(client, owner, tiny_mp4)
    rejected = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": [{"asset_id": first["asset_id"], "description": "gone"}]},
    )
    assert rejected.status_code == 404
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={
            "mode": "my_broll",
            "raw_video_id": raw["upload_id"],
            "broll": [{"asset_id": second["asset_id"], "description": "An aerial view of the Indian Parliament."}],
        },
    )
    assert created.status_code == 200
    assert created.json()["broll"][0]["asset_id"] == second["asset_id"]


def test_multiple_broll_and_session_roundtrip(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    raw = _raw(client, owner, tiny_mp4)
    first = _broll(client, owner, tiny_mp4, "A student watching a lecture on a smartphone.")
    second = _broll(client, owner, tiny_mp4, "संसद का हवाई दृश्य")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={
            "mode": "my_broll",
            "raw_video_id": raw["upload_id"],
            "broll": [
                {"asset_id": first["asset_id"], "description": "A student watching a lecture on a smartphone."},
                {"asset_id": second["asset_id"], "description": "संसद का हवाई दृश्य"},
            ],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "prepared"
    assert body["mode"] == "my_broll"
    assert body["broll_count"] == 2
    assert "not started" in body["note"]
    fetched = client.get(f"/api/sessions/{body['session_id']}", headers=_headers(owner))
    assert fetched.status_code == 200
    assert fetched.json()["broll"][1]["description"] == "संसद का हवाई दृश्य"
    assert "DR_SHIVIN_RAW" not in fetched.text
    assert "outputs" not in fetched.text


def test_auto_edit_session_rejects_broll(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    raw = _raw(client, owner, tiny_mp4)
    clip = _broll(client, owner, tiny_mp4, "A person writing notes in a notebook.")
    alone = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    assert alone.status_code == 200
    assert alone.json()["broll"] == []
    assert alone.json()["status"] == "prepared"
    mixed = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={
            "mode": "auto_edit",
            "raw_video_id": raw["upload_id"],
            "broll": [{"asset_id": clip["asset_id"], "description": "A person writing notes in a notebook."}],
        },
    )
    assert mixed.status_code == 422
    assert mixed.json()["error"]["code"] == "mode_does_not_accept_broll"


def test_my_broll_requires_a_clip(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    raw = _raw(client, owner, tiny_mp4)
    response = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "broll_required"


def test_unknown_asset_and_other_client_are_hidden(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    raw = _raw(client, owner, tiny_mp4)
    other = str(uuid.uuid4())
    missing = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": str(uuid.uuid4()), "broll": []},
    )
    foreign = client.post(
        "/api/sessions",
        headers=_headers(other),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    assert missing.status_code == 404
    assert foreign.status_code == 404
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    hidden = client.get(f"/api/sessions/{session_id}", headers=_headers(other))
    assert hidden.status_code == 404


def test_missing_client_header(studio, tiny_mp4: Path):
    client, _owner, _settings = studio
    response = client.post("/api/uploads/raw-video", files=_video(tiny_mp4))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "client_required"


def test_data_dir_cannot_point_at_outputs_or_raw_video():
    with pytest.raises(RuntimeError):
        Settings(data_dir=OUTPUTS, max_file_bytes=10, max_session_bytes=10, cors_origins=())
    with pytest.raises(RuntimeError):
        Settings(data_dir=RAW, max_file_bytes=10, max_session_bytes=10, cors_origins=())


def test_session_total_limit(tiny_mp4: Path, tmp_path: Path):
    size = tiny_mp4.stat().st_size
    settings = Settings(
        data_dir=tmp_path / "studio",
        max_file_bytes=size,
        max_session_bytes=size,
        cors_origins=(),
    )
    client = TestClient(create_app(settings))
    owner = str(uuid.uuid4())
    raw = _raw(client, owner, tiny_mp4)
    clip = _broll(client, owner, tiny_mp4, "A student watching a lecture on a smartphone.")
    response = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={
            "mode": "my_broll",
            "raw_video_id": raw["upload_id"],
            "broll": [{"asset_id": clip["asset_id"], "description": "A student watching a lecture on a smartphone."}],
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "session_too_large"


def test_api_source_does_not_call_the_engine():
    root = Path(__file__).resolve().parents[1] / "app"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "run_phase" not in text
    assert "broll_bot" not in text
    assert "rewind" not in text.lower()
    assert "search_videos" not in text


def test_prepared_upload_cannot_be_deleted(studio, tiny_mp4: Path):
    client, owner, _settings = studio
    raw = _raw(client, owner, tiny_mp4)
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    assert created.status_code == 200
    removed = client.delete(f"/api/uploads/{raw['upload_id']}", headers=_headers(owner))
    assert removed.status_code == 409


def _raw(client: TestClient, owner: str, path: Path) -> dict:
    response = client.post("/api/uploads/raw-video", headers=_headers(owner), files=_video(path))
    assert response.status_code == 200, response.text
    return response.json()


def _broll(client: TestClient, owner: str, path: Path, description: str) -> dict:
    response = client.post(
        "/api/uploads/broll",
        headers=_headers(owner),
        data={"description": description},
        files=_video(path, "clip.mp4"),
    )
    assert response.status_code == 200, response.text
    return response.json()
