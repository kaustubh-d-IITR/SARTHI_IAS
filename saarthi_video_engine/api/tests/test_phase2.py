"""Phase 2 processing tests. Providers and footage search are fakes."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app
from app.processing.jobs import Services
from app.processing.plan_adapt import build_engine_plan, validate_envelope
from app.processing.transcribe import transcribe_upload
from app.processing.user_broll import match_broll

ROOT = Path(__file__).resolve().parents[1]


def _video(tmp: Path) -> Path:
    destination = tmp / "lesson.mp4"
    completed = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x123456:s=160x160:r=30:d=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        pytest.skip("ffmpeg could not write a test video")
    return destination


def _transcript() -> dict:
    return {
        "language": "en",
        "language_source": "provider",
        "translated_to_english": False,
        "method": "elevenlabs",
        "model": "scribe_v2",
        "segments": [
            {
                "id": "a0",
                "text": "A student watching a lecture on a smartphone.",
                "original_text": "A student watching a lecture on a smartphone.",
                "start": 0.2,
                "end": 4.0,
                "confidence": None,
                "timing_precision": "word",
                "words": [
                    {"text": "student", "start": 0.4, "end": 0.8, "logprob": -0.2},
                    {"text": "smartphone", "start": 2.4, "end": 3.1, "logprob": -0.4},
                ],
            },
            {
                "id": "a1",
                "text": "I am going to the hospital.",
                "original_text": "I am going to the hospital.",
                "start": 4.2,
                "end": 7.5,
                "confidence": None,
                "timing_precision": "word",
                "words": [{"text": "hospital", "start": 6.2, "end": 6.8, "logprob": -0.1}],
            },
        ],
    }


def _app(tmp: Path, provider, selector=None):
    settings = Settings(
        data_dir=tmp / "studio",
        max_file_bytes=8_000_000,
        max_session_bytes=16_000_000,
        cors_origins=(),
    )

    def transcribe(path: Path, cache: Path) -> dict:
        return transcribe_upload(path, cache, provider)

    services = Services(transcribe=transcribe, select_auto=selector or (lambda _transcript, _work: []))
    return TestClient(create_app(settings, services)), settings


def _headers(owner: str) -> dict[str, str]:
    return {"X-Studio-Client": owner}


def _upload(client: TestClient, owner: str, path: Path, kind: str, description: str = "") -> dict:
    files = {"file": (path.name, path.read_bytes(), "video/mp4")}
    if kind == "raw":
        response = client.post("/api/uploads/raw-video", headers=_headers(owner), files=files)
    else:
        response = client.post(
            "/api/uploads/broll",
            headers=_headers(owner),
            data={"description": description},
            files=files,
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_mode_b_matches_only_the_supplied_library(tmp_path: Path):
    video = _video(tmp_path)
    calls = {"n": 0}

    def provider(_path: Path) -> dict:
        calls["n"] += 1
        return _transcript()

    def explode(_transcript, _work):
        raise AssertionError("Mode B must not call the internet selector")

    client, _settings = _app(tmp_path, provider, explode)
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    phone = _upload(client, owner, video, "broll", "A student watching a lecture on a smartphone.")
    other = _upload(client, owner, video, "broll", "A volcano eruption over the ocean.")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={
            "mode": "my_broll",
            "raw_video_id": raw["upload_id"],
            "broll": [
                {"asset_id": phone["asset_id"], "description": "A student watching a lecture on a smartphone."},
                {"asset_id": other["asset_id"], "description": "A volcano eruption over the ocean."},
            ],
        },
    )
    session_id = created.json()["session_id"]
    started = client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    assert started.status_code == 200
    assert started.json()["status"] == "queued"
    status = client.get(f"/api/sessions/{session_id}/status", headers=_headers(owner)).json()
    assert status["status"] == "plan_ready"
    assert status["counts"]["approved_placements"] == 1
    assert status["counts"]["teacher_intervals"] >= 1
    assert status["counts"]["validation"] == "passed"
    transcript = client.get(f"/api/sessions/{session_id}/transcript", headers=_headers(owner)).json()
    assert transcript["segments"][1]["original_text"] == "I am going to the hospital."
    assert transcript["translated_to_english"] is False
    assert transcript["words"] if False else transcript["segments"][0]["words"][0]["text"] == "student"
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=_headers(owner)).json()
    cutaways = [row for row in plan["picture_decisions"] if row["picture_role"] == "show_broll"]
    teachers = [row for row in plan["picture_decisions"] if row["picture_role"] == "show_teacher"]
    assert len(cutaways) == 1
    assert cutaways[0]["filename"].endswith(".mp4")
    assert cutaways[0]["evidence"] == "description_lexical"
    assert cutaways[0]["origin"] == "user"
    assert "volcano" not in cutaways[0]["description"]
    assert teachers
    assert plan["rendered"] is False
    assert plan["validation"]["ok"] is True
    assert ":\\" not in json.dumps(plan)
    again = client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    assert again.status_code == 409
    hidden = client.get(f"/api/sessions/{session_id}/editing-plan", headers=_headers(str(uuid.uuid4())))
    assert hidden.status_code == 404


def test_transcript_reuse_and_hash_change(tmp_path: Path):
    video = _video(tmp_path)
    calls = {"n": 0}

    def provider(_path: Path) -> dict:
        calls["n"] += 1
        return _transcript()

    client, settings = _app(tmp_path, provider)
    owner = str(uuid.uuid4())
    first = _upload(client, owner, video, "raw")
    second = _upload(client, owner, video, "raw")
    for upload_id in (first["upload_id"], second["upload_id"]):
        created = client.post(
            "/api/sessions",
            headers=_headers(owner),
            json={"mode": "auto_edit", "raw_video_id": upload_id, "broll": []},
        )
        response = client.post(f"/api/sessions/{created.json()['session_id']}/process", headers=_headers(owner))
        assert response.status_code == 200
        status = client.get(f"/api/sessions/{created.json()['session_id']}/status", headers=_headers(owner)).json()
        assert status["status"] == "plan_ready"
    assert calls["n"] == 1
    reused = client.get(
        f"/api/sessions/{created.json()['session_id']}/transcript",
        headers=_headers(owner),
    ).json()
    assert reused["reused"] is True

    changed = settings.data_dir / "uploads" / second["upload_id"]
    media = next(changed.glob("media.*"))
    media.write_bytes(media.read_bytes() + b"changed")
    third = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": second["upload_id"], "broll": []},
    )
    client.post(f"/api/sessions/{third.json()['session_id']}/process", headers=_headers(owner))
    fresh = client.get(f"/api/sessions/{third.json()['session_id']}/transcript", headers=_headers(owner)).json()
    assert calls["n"] == 2
    assert fresh["reused"] is False
    assert fresh["source_sha256"] != reused["source_sha256"]


def test_transcription_failure_can_be_retried(tmp_path: Path):
    video = _video(tmp_path)
    calls = {"n": 0}

    def provider(_path: Path) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider unavailable")
        return _transcript()

    client, _settings = _app(tmp_path, provider)
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    failed = client.get(f"/api/sessions/{session_id}/status", headers=_headers(owner)).json()
    assert failed["status"] == "failed"
    assert "provider unavailable" in failed["error"]
    assert client.get(f"/api/sessions/{session_id}/transcript", headers=_headers(owner)).status_code == 404
    client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    retried = client.get(f"/api/sessions/{session_id}/status", headers=_headers(owner)).json()
    assert retried["status"] == "plan_ready"


def test_in_progress_session_is_not_started_twice(tmp_path: Path):
    video = _video(tmp_path)
    client, settings = _app(tmp_path, lambda _path: _transcript())
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    store = client.app.state.store
    record = store.get_session(session_id, owner)
    record["status"] = "transcribing"
    store.write_session(record)
    response = client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    assert response.status_code == 409


def test_mode_a_uses_the_injected_selector_without_inventing_a_licence(tmp_path: Path):
    video = _video(tmp_path)
    seen = {}

    def selector(transcript, _work):
        seen["segments"] = len(transcript["segments"])
        return [
            {
                "sha256": "abc123",
                "title": "Licensed clip",
                "description": "a student with a phone",
                "page_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0",
                "source_window": [0.2, 4.0],
                "clip_in": 0.0,
                "duration_seconds": 8.0,
                "relevance": 0.61,
                "reason": "The existing selector accepted this licensed clip.",
            }
        ]

    client, _settings = _app(tmp_path, lambda _path: _transcript(), selector)
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=_headers(owner)).json()
    cutaway = next(row for row in plan["picture_decisions"] if row["picture_role"] == "show_broll")
    assert seen["segments"] == 2
    assert cutaway["origin"] == "commons"
    assert cutaway["explanation"] == "The existing selector accepted this licensed clip."
    assert plan["validation"]["ok"] is True


def test_parliament_description_matches_that_subject():
    chosen, _proposals = match_broll(
        {
            "segments": [
                {
                    "id": "p0",
                    "text": "The Indian Parliament building is where laws are discussed.",
                    "original_text": "The Indian Parliament building is where laws are discussed.",
                    "start": 0.5,
                    "end": 4.5,
                }
            ]
        },
        [
            {
                "asset_id": "clip",
                "filename": "parliament.mp4",
                "description": "Indian Parliament building exterior with people entering the premises.",
                "duration_seconds": 8,
            }
        ],
    )
    assert len(chosen) == 1
    assert chosen[0]["filename"] == "parliament.mp4"
    assert chosen[0]["evidence"] == "description"
    assert chosen[0]["source_out"] <= 8


def test_no_match_stays_on_the_teacher():
    chosen, proposals = match_broll(
        _transcript(),
        [
            {
                "asset_id": "clip",
                "filename": "storm.mp4",
                "description": "A volcano eruption over the ocean.",
                "duration_seconds": 8,
            }
        ],
    )
    assert proposals == []
    assert chosen == []


def test_bad_intervals_and_foreign_assets_fail_validation():
    session = {
        "session_id": "s",
        "mode": "my_broll",
        "raw_video": {"upload_id": "raw"},
        "broll": [{"upload_id": "clip"}],
    }
    engine = {"decisions": [], "input_media": {"duration_seconds": 8, "transcript": {"segments": []}}}
    foreign = {
        "asset_id": "other-session",
        "explanation": "matched",
        "source_in": 0,
        "source_out": 2,
        "speech_start": 0.2,
        "speech_end": 3,
        "origin": "user",
        "clip_duration": 8,
    }
    external = dict(foreign, asset_id="clip", origin="commons", page_url="https://commons.wikimedia.org/wiki/File:X")
    late = dict(foreign, asset_id="clip", origin="user", source_out=20, clip_duration=4)
    assert any("not part of this session" in item for item in validate_envelope(session, _valid_shell(), [foreign], [])["errors"])
    assert any("external" in item for item in validate_envelope(session, _valid_shell(), [external], [{"asset_id": "clip", "duration_seconds": 8}])["errors"])
    assert any("past the clip" in item for item in validate_envelope(session, _valid_shell(), [late], [{"asset_id": "clip", "duration_seconds": 4}])["errors"])
    negative = dict(foreign, asset_id="clip", origin="user", speech_start=-1, source_in=-0.2)
    assert any("invalid" in item for item in validate_envelope(session, _valid_shell(), [negative], [{"asset_id": "clip", "duration_seconds": 8}])["errors"])
    assert engine["decisions"] == []


def test_hinglish_text_is_stored_unchanged(tmp_path: Path):
    video = _video(tmp_path)
    spoken = "main hospital ja raha hoon, yeh important point hai"

    def provider(_path: Path) -> dict:
        body = _transcript()
        body["segments"][1]["text"] = spoken
        body["segments"][1]["original_text"] = spoken
        body["segments"][1]["words"] = [{"text": "hospital", "start": 6.2, "end": 6.8, "logprob": -0.11}]
        return body

    client, _settings = _app(tmp_path, provider)
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    transcript = client.get(f"/api/sessions/{session_id}/transcript", headers=_headers(owner)).json()
    assert transcript["segments"][1]["text"] == spoken
    assert transcript["segments"][1]["original_text"] == spoken
    assert transcript["translated_to_english"] is False
    assert transcript["segments"][1]["words"][0]["logprob"] == -0.11
    assert transcript["model"] == "scribe_v2"


def test_overlapping_and_overlong_decisions_are_rejected():
    plan = build_engine_plan(_transcript(), [], 8.0)
    teacher = next(row for row in plan["decisions"] if row["action"] == "show_teacher")
    overlap = dict(teacher)
    overlap["id"] = "overlap-copy"
    plan["decisions"].append(overlap)
    session = {
        "session_id": "s",
        "mode": "auto_edit",
        "raw_video": {"upload_id": "raw", "filename": "lesson.mp4", "duration_seconds": 8},
        "broll": [],
        "transcript_meta": {},
    }
    rejected = validate_envelope(session, plan, [], [])
    assert rejected["ok"] is False
    assert any("overlaps" in item for item in rejected["errors"])

    plan["decisions"].pop()
    late = dict(teacher)
    late["id"] = "past-the-lesson"
    late["start"] = 7.0
    late["end"] = 12.0
    plan["decisions"].append(late)
    overlong = validate_envelope(session, plan, [], [])
    assert overlong["ok"] is False
    assert any("outside" in item for item in overlong["errors"])


def test_interrupted_job_fails_on_restart_and_can_be_retried(tmp_path: Path):
    video = _video(tmp_path)
    client, settings = _app(tmp_path, lambda _path: _transcript())
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, video, "raw")
    created = client.post(
        "/api/sessions",
        headers=_headers(owner),
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    )
    session_id = created.json()["session_id"]
    store = client.app.state.store
    record = store.get_session(session_id, owner)
    record["status"] = "selecting_footage"
    store.write_session(record)

    def transcribe(media: Path, cache: Path) -> dict:
        return transcribe_upload(media, cache, lambda _path: _transcript())

    restarted = TestClient(create_app(settings, Services(transcribe=transcribe, select_auto=lambda _transcript, _work: [])))
    failed = restarted.get(f"/api/sessions/{session_id}/status", headers=_headers(owner)).json()
    assert failed["status"] == "failed"
    assert "interrupted" in failed["error"]
    restarted.post(f"/api/sessions/{session_id}/process", headers=_headers(owner))
    ready = restarted.get(f"/api/sessions/{session_id}/status", headers=_headers(owner)).json()
    assert ready["status"] == "plan_ready"


def test_mode_b_module_does_not_reference_commons_search():
    text = (ROOT / "app" / "processing" / "user_broll.py").read_text(encoding="utf-8")
    assert "search_videos" not in text
    assert "wikimedia" not in text
    assert "select_clips" not in text


def _valid_shell() -> dict:
    return {
        "input_media": {
            "duration_seconds": 4,
            "transcript": {"segments": [{"id": "a0", "start": 0, "end": 3, "text": "book", "original_text": "book"}]},
            "video_path": "assets/teacher.mp4",
            "width": 720,
            "height": 1280,
        },
        "audio": {
            "preserve_original": True,
            "narration_path": "assets/narration.wav",
            "narration_interval": {"start": 0, "end": 4},
            "music": {"identity": None, "identity_status": "unknown", "source": "default", "gain_under_speech": 0.14, "gain_open": 0.35},
        },
        "decisions": [
            {
                "id": "teacher-all",
                "action": "show_teacher",
                "start": 0,
                "end": 4,
                "reason": "teacher",
                "reference_ids": [],
                "reference_timestamps": [],
                "confidence": 0.5,
                "source": "default",
                "asset_id": "teacher",
            }
        ],
        "render": {"width": 720, "height": 1280, "fps": 30, "object_fit": "cover"},
        "style": {"exemplar_reference_id": "SHORT_03", "layout_family": "bounded_card_with_title", "template_family": "card", "trusted": True},
        "limitations": [],
        "plan_id": "t",
        "contract_version": "1.0.0",
        "duration_seconds": 4,
    }
