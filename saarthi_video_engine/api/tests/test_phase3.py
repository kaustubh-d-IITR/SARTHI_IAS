"""Phase 3 render tests on synthetic clips.

HyperFrames is replaced by a stand-in that writes a 720x1280 lesson from the
composition's own narration and music files. Every other step is the existing
engine code: lesson cuts, logo trim, B-roll placement, the composer, the outro
join, and the audio comparison. No ElevenLabs, Commons, or real lesson is used.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app
from app.processing.jobs import Services
from app.services.rendering.adapter import RenderError, load_approved, resolve_inputs, timeline_changes
from app.services.rendering.bridge import EngineBridge

DESCRIPTION = "A student writing notes in a notebook."
HINGLISH = "main yeh concept samjha raha hoon"


def _ffmpeg(*args: str) -> None:
    completed = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        pytest.skip("ffmpeg could not write a fixture: " + completed.stderr[-200:])


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("phase3_media")
    lesson = root / "lesson.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=8",
        "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.4:seed=7",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(lesson),
    )
    broll = root / "broll.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=s=320x240:r=30:d=6",
        "-f", "lavfi", "-i", "sine=frequency=1000:d=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(broll),
    )
    outro = root / "outro.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=0x6b2430:s=720x1280:r=30:d=1",
        "-f", "lavfi", "-i", "sine=frequency=330:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(outro),
    )
    music = root / "music.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:d=20", "-ac", "2", "-ar", "48000", str(music))
    from PIL import Image, ImageDraw

    logo = root / "logo.png"
    image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((100, 100, 500, 500), fill=(200, 30, 30, 255))
    image.save(logo)
    return {"lesson": lesson, "broll": broll, "outro": outro, "music": music, "logo": logo}


class FakeBridge(EngineBridge):
    """Real engine helpers, synthetic brand files, and a stand-in for the HyperFrames CLI."""

    def __init__(self, media: dict, mode: str = "good") -> None:
        super().__init__()
        self.media = media
        self.mode = mode
        self.renders = 0

    def logo_path(self) -> Path:
        return self.media["logo"]

    def outro_path(self) -> Path:
        return self.media["outro"]

    def music(self) -> tuple[Path, dict]:
        return self.media["music"], {
            "title": "File:Synthetic test tone.wav",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "page_url": "https://commons.wikimedia.org/wiki/File:Synthetic_test_tone.wav",
            "artist": "Fixture",
        }

    def place_broll(self, asset: dict, asset_dir: Path, video_filter: str | None) -> None:
        if self.mode == "keep_broll_audio":
            shutil.copyfile(asset["local_path"], asset_dir / asset["file_name"])
            return
        super().place_broll(asset, asset_dir, video_filter)

    def hyperframes(self, composition: Path, output: Path) -> None:
        self.renders += 1
        if self.mode == "corrupt":
            output.write_bytes(b"this is not an mp4")
            return
        plan = json.loads((composition.parent / "render_plan.json").read_text(encoding="utf-8"))
        duration = plan["input_media"]["duration_seconds"]
        music = plan["audio"]["music"]["gain_under_speech"]
        assets = composition / "assets"
        _ffmpeg(
            "-f", "lavfi", "-i", f"color=c=0x202020:s=720x1280:r=30:d={duration}",
            "-i", str(assets / "narration.wav"),
            "-i", str(assets / "music.wav"),
            "-filter_complex", f"[2:a]volume={music}[m];[1:a][m]amix=inputs=2:normalize=0:duration=first[a]",
            "-map", "0:v", "-map", "[a]", "-t", str(duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(output),
        )


def _transcript() -> dict:
    return {
        "language": "en",
        "language_source": "provider",
        "translated_to_english": False,
        "method": "elevenlabs",
        "model": "scribe_v2",
        "source_sha256": "fixture",
        "reused": False,
        "segments": [
            {
                "id": "s0",
                "text": "A student is writing notes in a notebook at the desk.",
                "original_text": "A student is writing notes in a notebook at the desk.",
                "start": 0.3,
                "end": 3.6,
                "confidence": None,
                "timing_precision": "word",
                "words": [
                    {"text": "A", "start": 0.3, "end": 0.4},
                    {"text": "student", "start": 0.4, "end": 0.9},
                    {"text": "is", "start": 0.9, "end": 1.0},
                    {"text": "writing", "start": 1.0, "end": 1.5},
                    {"text": "notes", "start": 1.5, "end": 1.9},
                    {"text": "in", "start": 1.9, "end": 2.0},
                    {"text": "a", "start": 2.0, "end": 2.1},
                    {"text": "notebook", "start": 2.1, "end": 2.8},
                    {"text": "at", "start": 2.8, "end": 2.9},
                    {"text": "the", "start": 2.9, "end": 3.0},
                    {"text": "desk.", "start": 3.0, "end": 3.6},
                ],
            },
            {
                "id": "s1",
                "text": HINGLISH,
                "original_text": HINGLISH,
                "start": 3.8,
                "end": 7.6,
                "confidence": None,
                "timing_precision": "segment",
                "words": [],
            },
        ],
    }


def _studio(tmp_path: Path, media: dict, *, mode: str = "good", selector=None):
    settings = Settings(data_dir=tmp_path / "studio", max_file_bytes=20_000_000, max_session_bytes=40_000_000, cors_origins=())
    calls = {"select": 0}

    def select(transcript: dict, work: Path) -> list[dict]:
        calls["select"] += 1
        return selector(transcript, work) if selector else []

    bridge = FakeBridge(media, mode)
    app = create_app(settings, Services(transcribe=lambda _path, _cache: _transcript(), select_auto=select), bridge)
    return TestClient(app), app, bridge, calls


def _upload(client: TestClient, owner: str, path: Path, kind: str, description: str = "") -> dict:
    files = {"file": (path.name, path.read_bytes(), "video/mp4")}
    if kind == "raw":
        response = client.post("/api/uploads/raw-video", headers={"X-Studio-Client": owner}, files=files)
    else:
        response = client.post("/api/uploads/broll", headers={"X-Studio-Client": owner}, data={"description": description}, files=files)
    assert response.status_code == 200, response.text
    return response.json()


def _ready_session(client: TestClient, media: dict, mode: str = "my_broll") -> tuple[str, str]:
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, media["lesson"], "raw")
    broll = []
    if mode == "my_broll":
        clip = _upload(client, owner, media["broll"], "broll", DESCRIPTION)
        broll = [{"asset_id": clip["asset_id"], "description": DESCRIPTION}]
    created = client.post(
        "/api/sessions",
        headers={"X-Studio-Client": owner},
        json={"mode": mode, "raw_video_id": raw["upload_id"], "broll": broll},
    )
    session_id = created.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers={"X-Studio-Client": owner})
    status = client.get(f"/api/sessions/{session_id}/status", headers={"X-Studio-Client": owner}).json()
    assert status["status"] == "plan_ready", status
    return session_id, owner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_mode_b_render_keeps_narration_captions_music_logo_and_outro(tmp_path: Path, media: dict):
    client, app, bridge, _calls = _studio(tmp_path, media)
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    before = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert before["status"] == "not_started"

    started = client.post(f"/api/sessions/{session_id}/render", headers=headers)
    assert started.status_code == 200, started.text
    assert started.json()["render"]["status"] == "render_queued"
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert status["status"] == "completed", status
    assert status["metadata"]["width"] == 720 and status["metadata"]["height"] == 1280
    assert status["metadata"]["frame_rate"] == 30
    assert status["audio_verification"]["status"] == "passed"
    assert status["audio_verification"]["correlation"] >= 0.9
    assert status["outro"]["status"] == "assembled"
    assert abs(status["metadata"]["duration_seconds"] - 9.0) < 0.5
    assert ":\\" not in json.dumps(status)

    store = app.state.store
    root = store.renders / session_id / status["render_id"]
    approved = json.loads((store.work_dir(session_id) / "editing_plan.json").read_text(encoding="utf-8"))["engine_plan"]
    rendered = json.loads((root / "render_plan.json").read_text(encoding="utf-8"))
    assert timeline_changes(approved, rendered) == []
    cutaways = [row for row in rendered["decisions"] if row["action"] == "replace_with_broll"]
    assert len(cutaways) == 1
    assert cutaways[0]["asset_path"] == "assets/broll-000.mp4"
    assert "page_url" not in cutaways[0] and "license" not in cutaways[0]
    assert rendered["audio"]["music"]["gain_under_speech"] == 0.12
    assert rendered["audio"]["music"]["gain_open"] == 0.28
    assert rendered["render"]["corner_logo"] == "assets/logo.png"

    html = (root / "composition" / "index.html").read_text(encoding="utf-8")
    videos = re.findall(r"<video\b[^>]*>", html)
    assert videos and all(" muted" in tag for tag in videos)
    assert 'src="assets/broll-000.mp4"' in html
    narration = re.findall(r'<audio\s+id="narration"[^>]*>', html, flags=re.S)
    assert len(narration) == 1
    assert 'data-start="0"' in narration[0] and 'src="assets/narration.wav"' in narration[0]
    assert 'id="music-bed"' in html and 'data-volume="0.12"' in html
    assert 'id="corner-logo"' in html
    approved_captions = {row["id"]: row for row in approved["decisions"] if row["action"] == "caption"}
    assert approved_captions
    for caption_id, row in approved_captions.items():
        assert f'id="{caption_id}"' in html
        assert f'data-start="{row["start"]}"' in html
    hinglish = [row["text"] for row in approved_captions.values() if "samjha" in row["text"].lower()]
    assert hinglish and all(text.upper() in html for text in hinglish)
    assert "explain" not in html.lower()

    from PIL import Image

    assert max(Image.open(root / "composition" / "assets" / "logo.png").size) <= 192
    outro = json.loads((root / "outro.json").read_text(encoding="utf-8"))
    assert outro["outro_filename"] == media["outro"].name
    assert "outro_path" not in outro
    manifest = json.loads((root / "input_manifest.json").read_text(encoding="utf-8"))
    assert [row["origin"] for row in manifest["broll"]] == ["user"]
    steps = json.loads((root / "logs" / "steps.json").read_text(encoding="utf-8"))
    placed = next(row for row in steps if row["step"] == "cutaways prepared")["placements"][0]
    assert placed["audio"] == "removed"
    assert placed["source_interval"] == [0.0, round(cutaways[0]["end"] - cutaways[0]["start"], 3)]
    assert (root / "editing_plan_snapshot.json").is_file()
    assert (root / "audio_verification.json").is_file()
    assert not (root / "lesson.mp4").exists()

    first_hash = _sha(root / "final.mp4")
    video = client.get(f"/api/sessions/{session_id}/video", headers=headers)
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.content == (root / "final.mp4").read_bytes()
    download = client.get(f"/api/sessions/{session_id}/download", headers=headers)
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    stranger = {"X-Studio-Client": str(uuid.uuid4())}
    assert client.get(f"/api/sessions/{session_id}/download", headers=stranger).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/video", headers=stranger).status_code == 404
    assert client.post(f"/api/sessions/{session_id}/render", headers=stranger).status_code == 404

    again = client.post(f"/api/sessions/{session_id}/render", headers=headers)
    assert again.status_code == 200
    second = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert second["status"] == "completed"
    assert second["render_id"] != status["render_id"]
    assert _sha(root / "final.mp4") == first_hash
    assert (store.renders / session_id / second["render_id"] / "final.mp4").is_file()
    assert bridge.renders == 2


def test_mode_a_render_uses_only_the_approved_selector_file(tmp_path: Path, media: dict):
    def selector(_transcript: dict, work: Path) -> list[dict]:
        folder = work / "footage"
        folder.mkdir(parents=True, exist_ok=True)
        clip = folder / "commons-clip.mp4"
        shutil.copyfile(media["broll"], clip)
        return [
            {
                "sha256": _sha(clip),
                "title": "Student notebook",
                "description": "A student writing.",
                "page_url": "https://commons.wikimedia.org/wiki/File:Fixture_notebook.webm",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0",
                "artist": "Fixture author",
                "local_path": str(clip),
                "source_window": [0.3, 3.6],
                "clip_in": 0.5,
                "duration_seconds": 6.0,
                "relevance": 0.6,
                "reason": "Returned by the fixture selector.",
            }
        ]

    client, app, _bridge, calls = _studio(tmp_path, media, selector=selector)
    session_id, owner = _ready_session(client, media, mode="auto_edit")
    headers = {"X-Studio-Client": owner}
    assert calls["select"] == 1
    assert client.post(f"/api/sessions/{session_id}/render", headers=headers).status_code == 200
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert status["status"] == "completed", status
    assert calls["select"] == 1
    root = app.state.store.renders / session_id / status["render_id"]
    rendered = json.loads((root / "render_plan.json").read_text(encoding="utf-8"))
    cutaway = next(row for row in rendered["decisions"] if row["action"] == "replace_with_broll")
    assert cutaway["license"] == "CC BY 4.0"
    assert cutaway["page_url"].startswith("https://commons.wikimedia.org/")
    assert cutaway["creator"] == "Fixture author"
    steps = json.loads((root / "logs" / "steps.json").read_text(encoding="utf-8"))
    placed = next(row for row in steps if row["step"] == "cutaways prepared")["placements"][0]
    assert placed["origin"] == "commons"
    assert placed["source_interval"][0] == 0.5


def test_corrupted_render_output_fails_and_retry_gets_a_new_folder(tmp_path: Path, media: dict):
    client, app, bridge, _calls = _studio(tmp_path, media, mode="corrupt")
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    failed = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert failed["status"] == "failed"
    assert "unreadable or corrupted" in failed["error"]
    assert client.get(f"/api/sessions/{session_id}/video", headers=headers).status_code == 404
    failure = json.loads((app.state.store.renders / session_id / failed["render_id"] / "failure.json").read_text(encoding="utf-8"))
    assert failure["error"] == failed["error"]
    bridge.mode = "good"
    assert client.post(f"/api/sessions/{session_id}/render", headers=headers).status_code == 200
    retried = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert retried["status"] == "completed"
    assert retried["render_id"] != failed["render_id"]


def test_failed_retry_keeps_the_previous_video_available(tmp_path: Path, media: dict):
    client, _app, bridge, _calls = _studio(tmp_path, media)
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    first = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    bridge.mode = "corrupt"
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    second = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert second["status"] == "failed"
    assert second["last_completed"]["render_id"] == first["render_id"]
    video = client.get(f"/api/sessions/{session_id}/video", headers=headers)
    assert video.status_code == 200
    assert video.headers["x-render-id"] == first["render_id"]


def test_broll_audio_left_in_a_cutaway_is_rejected(tmp_path: Path, media: dict):
    client, _app, _bridge, _calls = _studio(tmp_path, media, mode="keep_broll_audio")
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert status["status"] == "failed"
    assert "audio track" in status["error"]


def test_engine_broll_placement_strips_audio(tmp_path: Path, media: dict):
    bridge = FakeBridge(media)
    asset = {"local_path": str(media["broll"]), "file_name": "broll-000.mp4", "kind": "video", "clip_in": 0.5, "source_window": [0.3, 3.6], "duration_seconds": 6.0}
    bridge.place_broll(asset, tmp_path, None)
    probe = bridge.probe(tmp_path / "broll-000.mp4")
    assert [stream["codec_type"] for stream in probe["streams"]] == ["video"]
    assert float(probe["format"]["duration"]) < 4.0


def test_missing_raw_lesson_and_missing_broll_block_the_render(tmp_path: Path, media: dict):
    client, app, _bridge, _calls = _studio(tmp_path, media)
    store = app.state.store
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    record = store.get_session(session_id, owner)
    clip = store.media_file(record["broll"][0]["upload_id"])
    clip.unlink()
    blocked = client.post(f"/api/sessions/{session_id}/render", headers=headers)
    assert blocked.status_code == 409
    assert "B-roll file is missing" in blocked.json()["error"]["message"]

    other_id, other_owner = _ready_session(client, media)
    raw = store.media_file(store.get_session(other_id, other_owner)["raw_video"]["upload_id"])
    raw.unlink()
    blocked = client.post(f"/api/sessions/{other_id}/render", headers={"X-Studio-Client": other_owner})
    assert blocked.status_code == 409
    assert "raw lesson file is missing" in blocked.json()["error"]["message"]


def test_changed_or_corrupted_broll_is_rejected_during_render(tmp_path: Path, media: dict):
    client, app, _bridge, _calls = _studio(tmp_path, media)
    store = app.state.store
    session_id, owner = _ready_session(client, media)
    headers = {"X-Studio-Client": owner}
    clip = store.media_file(store.get_session(session_id, owner)["broll"][0]["upload_id"])
    clip.write_bytes(clip.read_bytes() + b"\x00tampered")
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert status["status"] == "failed"
    assert "changed after the plan was approved" in status["error"]

    clip.write_bytes(b"not a video at all")
    client.post(f"/api/sessions/{session_id}/render", headers=headers)
    status = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert status["status"] == "failed"
    assert "unreadable or corrupted" in status["error"]


def test_asset_ownership_external_footage_and_paths_outside_the_session(tmp_path: Path, media: dict):
    client, app, _bridge, _calls = _studio(tmp_path, media)
    store = app.state.store
    session_id, owner = _ready_session(client, media)
    record = store.get_session(session_id, owner)
    saved = load_approved(store, record)
    assert len(resolve_inputs(store, record, saved)["clips"]) == 1

    foreign_id, foreign_owner = _ready_session(client, media)
    foreign = store.get_session(foreign_id, foreign_owner)["broll"][0]["upload_id"]
    stolen = json.loads(json.dumps(saved))
    for decision in stolen["engine_plan"]["decisions"]:
        if decision["action"] == "replace_with_broll":
            decision["asset_id"] = foreign
    with pytest.raises(RenderError, match="outside this session"):
        resolve_inputs(store, record, stolen)

    external = json.loads(json.dumps(saved))
    external["studio"]["placements"][0].update({"origin": "commons", "page_url": "https://commons.wikimedia.org/wiki/File:X.webm"})
    with pytest.raises(RenderError, match="external footage"):
        resolve_inputs(store, record, external)

    auto = dict(record, mode="auto_edit")
    auto_saved = json.loads(json.dumps(saved))
    auto_saved["studio"]["mode"] = "auto_edit"
    work = store.work_dir(session_id)
    asset_id = auto_saved["studio"]["placements"][0]["asset_id"]
    (work / "footage_index.json").write_text(json.dumps({"assets": [{"asset_id": asset_id, "relative_path": "../../uploads/escape.mp4"}]}), encoding="utf-8")
    with pytest.raises(RenderError, match="outside this session"):
        resolve_inputs(store, auto, auto_saved)
    (work / "footage_index.json").write_text(json.dumps({"assets": []}), encoding="utf-8")
    with pytest.raises(RenderError, match="missing from the session catalogue"):
        resolve_inputs(store, auto, auto_saved)


def test_render_requires_a_ready_plan_and_blocks_duplicates(tmp_path: Path, media: dict):
    client, app, _bridge, _calls = _studio(tmp_path, media)
    store = app.state.store
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, media["lesson"], "raw")
    created = client.post(
        "/api/sessions",
        headers={"X-Studio-Client": owner},
        json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []},
    ).json()
    early = client.post(f"/api/sessions/{created['session_id']}/render", headers={"X-Studio-Client": owner})
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "plan_not_ready"

    session_id, owner = _ready_session(client, media)
    record = store.get_session(session_id, owner)
    record["render"] = {"render_id": str(uuid.uuid4()), "status": "rendering", "stage": "rendering"}
    store.write_session(record)
    duplicate = client.post(f"/api/sessions/{session_id}/render", headers={"X-Studio-Client": owner})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "render_in_progress"

    restarted = create_app(app.state.settings, app.state.services, app.state.renderer)
    state = restarted.state.store.get_session(session_id, owner)["render"]
    assert state["status"] == "failed"
    assert "interrupted" in state["error"]


def test_timeline_changes_flags_moved_captions():
    plan = {
        "input_media": {"duration_seconds": 8.0},
        "decisions": [
            {"id": "teacher-all", "action": "show_teacher", "start": 0.0, "end": 8.0},
            {"id": "cap-s0", "action": "caption", "start": 0.3, "end": 1.0, "text": "A"},
        ],
    }
    moved = json.loads(json.dumps(plan))
    moved["decisions"][1]["start"] = 0.6
    assert timeline_changes(plan, moved) == ["cap-s0 changed"]
    trimmed = json.loads(json.dumps(plan))
    trimmed["input_media"]["duration_seconds"] = 7.9
    trimmed["decisions"][0]["end"] = 7.9
    assert timeline_changes(plan, trimmed) == []
