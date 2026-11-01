"""Cutaways never ask for footage a clip does not have (production failure 85865c1b, "source interval is past the clip duration").

The director gave a 2-second NCERT clip 2.18 s on screen because it extended the cutaway to
the end of the spoken word without re-checking the clip length. These tests pin the fix at
every layer: interval fitting, the director, processing, review edits and the renderer.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_phase3 import FakeBridge, media  # noqa: F401  (fixture)

from app.config import Settings
from app.factory import create_app
from app.media_probe import probe_video
from app.processing.director import direct
from app.processing.intervals import fit_cutaway, interval_errors
from app.processing.jobs import Services
from app.processing.plan_adapt import fit_placements

FIXTURE = Path(__file__).parent / "fixtures" / "newspaper_transcript.json"
NAMES = {
    "ncert_books_red_cross_2s.mp4": "NCERT books with a red cross",
    "newspaper_red_cross_2s.mp4": "A newspaper with a red cross",
    "youtube_app_opening_2s.mp4": "YouTube app opening on a phone",
}


def _ffmpeg(*args: str) -> None:
    done = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip("ffmpeg could not build a fixture: " + done.stderr[-200:])


@pytest.fixture(scope="module")
def two_second_clips(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("clips_2s")
    out = {}
    for k, name in enumerate(NAMES):
        path = root / name
        _ffmpeg("-f", "lavfi", "-i", f"testsrc=s=640x360:r=30:d=2", "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * k}:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
        out[name] = path
    return out


# --------------------------------------------------------------------------- the interval rules


@pytest.mark.parametrize(
    "start, end, source_in, expected",
    [
        (5.0, 7.0, 0.0, {"speech_end": 7.0, "source_in": 0.0, "source_out": 2.0, "adjusted": ""}),
        (5.0, 7.0, 0.5, {"speech_end": 7.0, "source_in": 0.0, "source_out": 2.0}),
        (5.0, 8.0, 0.0, {"speech_end": 7.0, "source_in": 0.0, "source_out": 2.0}),
        (5.0, 6.2, 1.5, {"speech_end": 6.2, "source_in": 0.8, "source_out": 2.0}),
    ],
)
def test_two_second_clip_requests_are_fitted(start, end, source_in, expected):
    fitted = fit_cutaway(start, end, source_in, 2.0, 1.0)
    assert fitted["ok"] and fitted["speech_start"] == start
    for key, value in expected.items():
        assert fitted[key] == pytest.approx(value) if not isinstance(value, str) else fitted[key] == value
    assert fitted["source_out"] <= 2.0 and fitted["source_out"] - fitted["source_in"] == pytest.approx(fitted["speech_end"] - fitted["speech_start"])
    assert interval_errors("clip", fitted["speech_start"], fitted["speech_end"], fitted["source_in"], fitted["source_out"], 2.0) == []


def test_too_little_footage_is_rejected_with_a_reason():
    late = fit_cutaway(5.0, 5.5, 1.5, 2.0, 1.0)
    assert not late["ok"] and "0.50 s fits" in late["reason"]
    tiny = fit_cutaway(5.0, 7.0, 0.0, 0.6, 1.0)
    assert not tiny["ok"] and "0.60 s of footage" in tiny["reason"]
    assert interval_errors("ncert", 1.26, 3.44, 0.0, 2.18, 2.0) == ["ncert: source interval 0.000–2.180 s is past the clip's 2.000 s of footage"]


def test_a_typed_source_start_is_kept_and_the_end_clamped():
    clamped = fit_cutaway(5.0, 7.0, 0.5, 2.0, 1.0, keep_source=True)
    assert clamped["ok"] and clamped["source_in"] == 0.5 and clamped["source_out"] == 2.0
    assert clamped["speech_end"] == pytest.approx(6.5) and "last frame" in clamped["adjusted"]
    past = fit_cutaway(5.0, 7.0, 2.0, 2.0, 1.0, keep_source=True)
    assert not past["ok"] and "Start the source earlier" in past["reason"]
    short = fit_cutaway(5.0, 7.0, 1.5, 2.0, 1.0, keep_source=True)
    assert not short["ok"] and "leaving 0.50 s" in short["reason"]


def test_frame_rounded_durations_never_overshoot():
    fitted = fit_cutaway(1.26, 3.44, 0.0, 2.0333333, 1.0)
    assert fitted["source_out"] <= 2.0333333 and fitted["speech_end"] == pytest.approx(1.26 + 2.033)


def test_consecutive_clips_keep_their_own_footage():
    placements = [
        {"asset_id": "news", "speech_start": 0.12, "speech_end": 1.26, "source_in": 0.0, "source_out": 1.14},
        {"asset_id": "ncert", "speech_start": 1.26, "speech_end": 3.44, "source_in": 0.0, "source_out": 2.18},
        {"asset_id": "yt", "speech_start": 6.08, "speech_end": 8.08, "source_in": 0.5, "source_out": 2.5},
    ]
    catalogue = [{"asset_id": name, "duration_seconds": 2.0} for name in ("news", "ncert", "yt")]
    kept, rejected = fit_placements(placements, catalogue)
    assert rejected == []
    rows = {row["asset_id"]: row for row in kept}
    assert (rows["ncert"]["speech_end"], rows["ncert"]["source_out"]) == (pytest.approx(3.26), pytest.approx(2.0))
    assert (rows["yt"]["source_in"], rows["yt"]["source_out"]) == (pytest.approx(0.0), pytest.approx(2.0))
    assert rows["news"]["speech_end"] <= rows["ncert"]["speech_start"]
    for row in kept:
        assert row["source_out"] <= 2.0 and row["clip_duration"] == 2.0


def test_picture_shorter_than_container_is_what_counts(tmp_path: Path):
    clip = tmp_path / "short_picture.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:r=30:d=1.5", "-f", "lavfi", "-i", "sine=frequency=400:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip))
    probed = probe_video(clip)
    assert probed["duration_seconds"] >= 2.9 and probed["video_duration_seconds"] == pytest.approx(1.5, abs=0.05)


# --------------------------------------------------------------------------- the director on the real lesson


def test_director_gives_two_second_clips_valid_windows():
    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    catalogue = [{"asset_id": name, "filename": name, "description": text, "duration_seconds": 2.0} for name, text in NAMES.items()]
    placements, _ = direct(transcript, catalogue, None, 41.6)
    rows = {row["asset_id"]: row for row in placements}
    assert set(rows) == set(NAMES)
    assert rows["newspaper_red_cross_2s.mp4"]["speech_start"] == pytest.approx(0.12)
    assert rows["ncert_books_red_cross_2s.mp4"]["speech_start"] == pytest.approx(1.26)
    assert rows["youtube_app_opening_2s.mp4"]["speech_start"] == pytest.approx(6.08)
    for row in placements:
        assert 0 <= row["source_in"] < row["source_out"] <= 2.0
        assert row["source_out"] - row["source_in"] == pytest.approx(row["speech_end"] - row["speech_start"], abs=1e-3)
        assert interval_errors(row["asset_id"], row["speech_start"], row["speech_end"], row["source_in"], row["source_out"], 2.0) == []


# --------------------------------------------------------------------------- end to end: process, edit, render


@pytest.fixture(scope="module")
def lesson_media(tmp_path_factory, media) -> dict:
    root = tmp_path_factory.mktemp("lesson_24s")
    lesson = root / "VIDEO_2.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=24", "-f", "lavfi", "-i", "anoisesrc=d=24:c=pink:a=0.4:seed=7", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(lesson))
    return {**media, "lesson": lesson}


def test_two_second_clips_process_edit_and_render_without_invalid_intervals(tmp_path: Path, lesson_media: dict, two_second_clips: dict):
    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = Settings(data_dir=tmp_path / "studio", max_file_bytes=50_000_000, max_session_bytes=100_000_000, cors_origins=())
    bridge = FakeBridge(lesson_media)
    client = TestClient(create_app(settings, Services(transcribe=lambda _p, _c: transcript, select_auto=lambda *_: []), bridge))
    owner = {"X-Studio-Client": str(uuid.uuid4())}

    def upload(path: Path, kind: str, description: str | None = None) -> dict:
        files = {"file": (path.name, path.read_bytes(), "video/mp4")}
        url = "/api/uploads/raw-video" if kind == "raw" else "/api/uploads/broll"
        response = client.post(url, headers=owner, data={"description": description} if description else {}, files=files)
        assert response.status_code == 200, response.text
        return response.json()

    raw = upload(lesson_media["lesson"], "raw")
    broll = [{"asset_id": upload(path, "broll", NAMES[name])["asset_id"], "description": NAMES[name]} for name, path in two_second_clips.items()]
    session_id = client.post("/api/sessions", headers=owner, json={"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": broll}).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=owner)
    status = client.get(f"/api/sessions/{session_id}/status", headers=owner).json()
    assert status["status"] == "plan_ready", status.get("error")
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=owner).json()
    assert plan["validation"]["ok"] and len(plan["placements"]) == 3
    catalogue = {item["asset_id"]: item for item in plan["catalogue"]}
    for row in plan["placements"]:
        clip = catalogue[row["asset_id"]]["duration_seconds"]
        assert clip == pytest.approx(2.0, abs=0.05)
        assert interval_errors(row["asset_id"], row["speech_start"], row["speech_end"], row["source_in"], row["source_out"], clip) == []

    # A review edit that asks for more footage than exists is fitted, not saved invalid.
    edits = [{key: row[key] for key in ("asset_id", "speech_start", "speech_end", "source_in", "source_out", "approval")} for row in plan["placements"]]
    youtube = next(row for row in edits if row["speech_start"] > 5)
    youtube.update({"speech_end": youtube["speech_start"] + 2.6, "source_in": 0.0, "source_out": 2.6})
    edited = client.post(f"/api/sessions/{session_id}/editing-plan/edits", headers=owner, json={"base_version_id": plan["plan_version"]["version_id"], "placements": edits})
    assert edited.status_code == 200, edited.text
    fixed = next(row for row in edited.json()["placements"] if row["asset_id"] == youtube["asset_id"])
    assert fixed["source_out"] <= catalogue[fixed["asset_id"]]["duration_seconds"] + 1e-3
    assert fixed["speech_end"] - fixed["speech_start"] == pytest.approx(fixed["source_out"] - fixed["source_in"], abs=1e-3)
    assert "shortened" in fixed["adjusted"]
    tiny = [dict(row) for row in edits]
    tiny[-1].update({"speech_end": tiny[-1]["speech_start"] + 0.5, "source_in": 1.5, "source_out": 2.0})
    refused = client.post(f"/api/sessions/{session_id}/editing-plan/edits", headers=owner, json={"base_version_id": edited.json()["plan_version"]["version_id"], "placements": tiny})
    assert refused.status_code == 422 and "a cutaway needs" in refused.json()["error"]["message"]

    # The renderer receives only intervals the clips can supply.
    assert client.post(f"/api/sessions/{session_id}/render", headers=owner).status_code == 200
    render = client.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()
    assert render["status"] == "completed", render.get("error")
    root = client.app.state.store.renders / session_id / render["render_id"]
    rendered = json.loads((root / "render_plan.json").read_text(encoding="utf-8"))
    placed = json.loads((root / "logs" / "steps.json").read_text(encoding="utf-8"))
    cutaways = next(step for step in placed if step["step"] == "cutaways prepared")["placements"]
    assert len(cutaways) == 3
    for item in cutaways:
        start, end = item["source_interval"]
        assert 0 <= start < end <= 2.0 + 1e-3
    assert sum(1 for d in rendered["decisions"] if d["action"] == "replace_with_broll") == 3


def test_renderer_refuses_an_invalid_interval_before_hyperframes(tmp_path: Path, lesson_media: dict, two_second_clips: dict, monkeypatch):
    """Defence in depth: even if a plan file were edited by hand, HyperFrames never sees the bad interval."""
    from app.services.rendering import adapter

    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = Settings(data_dir=tmp_path / "studio", max_file_bytes=50_000_000, max_session_bytes=100_000_000, cors_origins=())
    bridge = FakeBridge(lesson_media)
    client = TestClient(create_app(settings, Services(transcribe=lambda _p, _c: transcript, select_auto=lambda *_: []), bridge))
    owner = {"X-Studio-Client": str(uuid.uuid4())}
    raw = client.post("/api/uploads/raw-video", headers=owner, files={"file": ("VIDEO_2.mp4", lesson_media["lesson"].read_bytes(), "video/mp4")}).json()
    name, path = next(iter(two_second_clips.items()))
    clip = client.post("/api/uploads/broll", headers=owner, data={"description": NAMES[name]}, files={"file": (name, path.read_bytes(), "video/mp4")}).json()
    session_id = client.post("/api/sessions", headers=owner, json={"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": [{"asset_id": clip["asset_id"], "description": NAMES[name]}]}).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers=owner)
    original = adapter.load_approved

    def tampered(*args, **kwargs):
        saved = original(*args, **kwargs)
        for row in saved["studio"]["placements"]:
            row["source_out"] = row["source_in"] + 2.5
        for decision in saved["engine_plan"]["decisions"]:
            if decision["action"] == "replace_with_broll":
                decision["end"] = decision["start"] + 2.5
        return saved

    monkeypatch.setattr(adapter, "load_approved", tampered)
    renders_before = bridge.renders
    client.post(f"/api/sessions/{session_id}/render", headers=owner)
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=owner).json()
    assert status["status"] == "failed"
    assert bridge.renders == renders_before
