"""Studio look and sound: settings, composition HTML, outro fill, focus, music, and the music API."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import ENGINE_ROOT, Settings
from app.errors import StudioError
from app.factory import create_app
from app.processing.jobs import Services
from app.processing.settings import default_settings, merge
from app.services.music import library_track, public_tracks
from app.services.rendering.studio import estimate_focus, music_tail, studio_block

if str(ENGINE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "src"))
from hyperframes_adapter.studio_layout import cover_position, filter_css, render_studio_html, teacher_box  # noqa: E402
from render.outro import append_outro, outro_filter  # noqa: E402


def _ffmpeg(*args: str) -> None:
    done = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip("ffmpeg could not build a fixture: " + done.stderr[-200:])


def _mean_volume(path: Path, start: float = 0.0) -> float:
    done = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(start), "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"mean_volume:\s*(-?[\d.]+) dB", done.stderr).group(1))


# --------------------------------------------------------------------------- settings


def test_settings_defaults_are_restrained_and_validated():
    settings = default_settings()
    assert not any(effect["enabled"] for effect in settings["effects"].values())
    assert settings["transitions"]["style"] == "cut" and settings["outro"]["fill"] == "blur"
    changed = merge(settings, {"effects": {"glitter": {"enabled": True, "scope": "intro"}}, "captions": {"size": 40}})
    assert changed["effects"]["glitter"] == {"enabled": True, "intensity": 0.4, "scope": "intro"} and changed["captions"]["size"] == 40
    for bad in (
        {"captions": {"size": 200}},
        {"captions": {"highlight": "yellow"}},
        {"effects": {"laser": {"enabled": True}}},
        {"effects": {"zoom": {"scope": "intro"}}},
        {"framing": {"mode": "stretch"}},
        {"music": {"source": "library"}},
        {"nonsense": {}},
        {"captions": {"font": "Comic Sans"}},
    ):
        with pytest.raises(StudioError):
            merge(settings, bad)


# --------------------------------------------------------------------------- composition


def _plan(decisions: list[dict], duration: float = 12.0) -> dict:
    return {
        "plan_id": "t",
        "style": {"layout_family": "bounded_card_with_title"},
        "input_media": {"video_path": "assets/teacher.mp4", "duration_seconds": duration, "width": 1280, "height": 720, "transcript": {"segments": [{"start": 0.5, "end": 5.0}, {"start": 6.0, "end": 11.0}]}},
        "render": {"width": 720, "height": 1280, "fps": 30, "corner_logo": "assets/logo.png"},
        "audio": {"narration_path": "assets/narration.wav", "music": {"path": "assets/music.wav", "gain_under_speech": 0.09, "gain_open": 0.21}},
        "decisions": decisions,
    }


DEFAULTS = {"audio": {"music_gain_under_speech": 0.12, "music_gain_open": 0.28, "duck_attack_seconds": 0.15, "duck_release_seconds": 0.4}}
DECISIONS = [
    {"id": "teacher-0", "action": "show_teacher", "start": 0.0, "end": 2.0},
    {"id": "broll-0", "action": "replace_with_broll", "start": 2.0, "end": 4.5, "asset_path": "assets/broll-000.mp4", "asset_id": "news"},
    {"id": "teacher-end", "action": "show_teacher", "start": 4.5, "end": 12.0},
    {"id": "caption-0", "action": "caption", "start": 0.5, "end": 1.6, "text": "na hi maine"},
    {"id": "caption-1", "action": "caption", "start": 1.6, "end": 2.6, "text": "newspaper padha"},
]


def _studio(**changes) -> dict:
    settings = merge(default_settings(), changes or None)
    return studio_block(settings, src_width=1280, src_height=720, focus={"focus_x": 0.7, "focus_y": 0.4, "method": "motion"}, backdrop="assets/teacher_backdrop.jpg", broll_sizes={"assets/broll-000.mp4": (1280, 720)}) | {"music": {"fade_in": 0.5, "fade_out": 1.5}}


def test_landscape_teacher_fills_most_of_the_canvas_centred_on_the_teacher():
    box = teacher_box(_studio(), 720, 1280)
    assert box["height"] == round(1280 * 0.62) and not box["full"]
    # The teacher at 70 % of a 16:9 frame ends up in the middle of the crop.
    scale = box["height"] / 720
    shown_left = (720 - 1280 * scale) * box["pos_x"] / 100
    assert abs(shown_left + 0.7 * 1280 * scale - 360) < 2
    assert cover_position(1080, 1920, 720, 1280, 0.5, 0.4) == (50.0, 50.0)
    fill = teacher_box(_studio(framing={"mode": "fill"}), 720, 1280)
    assert fill["height"] == 1280 and fill["full"]


def test_studio_composition_cuts_cleanly_and_keeps_audio_and_branding():
    html = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(), "<div id='title-card'></div>")
    videos = re.findall(r"<video\b[^>]*>", html)
    assert videos and all(" muted" in tag for tag in videos)
    assert 'id="teacher-backdrop"' in html and "blur(" in html
    assert re.search(r'id="broll-0"[^>]*width:720px;height:1280px;object-fit:cover', html)
    assert 'tl.fromTo("#broll-0", { opacity: 0' not in html
    assert len(re.findall(r'<audio\s+id="narration"', html)) == 1 and 'id="music-bed"' in html and 'data-volume="0.09"' in html
    assert 'data-fade-out="1.5"' in html and 'id="corner-logo"' in html
    assert 'data-text="NA HI MAINE"' in html and 'color: "#f5c400"' in html
    assert 'class="fx-particle"' not in html and 'class="clip fx-vignette"' not in html
    faded = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(transitions={"style": "fade", "seconds": 0.3}), "")
    assert 'tl.fromTo("#broll-0", { opacity: 0 }, { opacity: 1, duration: 0.3' in faded


def test_effects_are_real_css_and_respect_their_scope():
    warm = _studio(effects={"warm": {"enabled": True, "intensity": 0.5, "scope": "all"}})
    assert filter_css(warm["effects"], "all") == "sepia(0.150) saturate(1.075) hue-rotate(-4.0deg)"
    html = render_studio_html(_plan(DECISIONS), DEFAULTS, warm, "")
    assert "#stage { position: absolute; inset: 0; z-index: 1; filter:sepia(0.150)" in html
    bw = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(effects={"black_and_white": {"enabled": True, "scope": "broll"}}), "")
    assert re.search(r'id="broll-0"[^>]*filter:grayscale\(1\.000\)', bw) and not re.search(r'id="teacher-0"[^>]*grayscale', bw)
    sparkly = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(effects={"glitter": {"enabled": True, "scope": "intro"}, "vignette": {"enabled": True, "scope": "intro"}}), "")
    particles = re.findall(r'id="fx-glitter-\d+"', sparkly)
    assert 10 <= len(particles) <= 40
    times = [float(t) for t in re.findall(r'tl\.fromTo\("#fx-glitter-\d+", \{opacity:0, scale:0\.3\}, \{[^}]*\}, ([\d.]+)\);', sparkly)]
    assert times and max(times) < 3.0
    assert re.search(r'id="fx-vignette-0"[^>]*data-start="0.0" data-duration="3.0"', sparkly)
    zoom = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(effects={"zoom": {"enabled": True, "intensity": 0.5}}), "")
    assert 'tl.fromTo("#teacher-0", { scale: 1 }, { scale: 1.040' in zoom


def test_captions_follow_settings_and_stay_inside_the_frame():
    html = render_studio_html(_plan(DECISIONS), DEFAULTS, _studio(captions={"size": 56, "position": 0.9, "style": "outline", "uppercase": False}), "")
    top = float(re.search(r"\.caption \{\s*position: absolute; left: 40px; width: 640px; top: ([\d.]+)px", html).group(1))
    assert top <= 1280 - 56 - 56 * 1.6
    assert "-webkit-text-stroke" in html and 'data-text="na hi maine"' in html and "text-transform: none" in html


# --------------------------------------------------------------------------- outro, focus, music


def test_outro_is_framed_over_a_blurred_copy_with_music_under_it(tmp_path: Path):
    lesson = tmp_path / "lesson.mp4"
    outro = tmp_path / "outro.mp4"
    music = tmp_path / "music.wav"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=3", "-f", "lavfi", "-i", "sine=frequency=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(lesson))
    _ffmpeg("-f", "lavfi", "-i", "color=c=0xd0d0d0:s=848x478:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(outro))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:d=20", str(music))
    assert outro_filter(848, 478, 720, 1280, 30, "blur").startswith("split=2")
    assert outro_filter(848, 478, 720, 1280, 30).startswith("scale=")
    tail = music_tail(music, tmp_path / "tail.wav", 3.0, 2.0, 0.28, 1.0, "ffmpeg")
    assert _mean_volume(tail) > -40
    final = tmp_path / "final.mp4"
    report = append_outro(lesson, outro, final, ffmpeg="ffmpeg", ffprobe="ffprobe", fill="blur", audio=tail)
    assert report["fill"] == "blur" and "continues" in report["audio_policy"]
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "4.0", "-i", str(final), "-frames:v", "1", "-vf", "scale=72:128,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    pixels = np.frombuffer(frame, dtype=np.uint8).reshape(128, 72)
    assert pixels[:10].mean() > 40 and pixels[-10:].mean() > 40
    assert _mean_volume(final, 3.2) > -50


def test_focus_follows_the_moving_teacher(tmp_path: Path):
    clip = tmp_path / "lecture.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=0x404040:s=1280x720:r=30:d=4", "-f", "lavfi", "-i", "color=c=white:s=180x320:r=30:d=4",
        "-filter_complex", "[0][1]overlay=x='900+40*sin(t*6)':y=200", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    )
    focus = estimate_focus(clip, 1280, 720, "ffmpeg")
    assert focus["method"] == "motion" and 0.65 < focus["focus_x"] < 0.85


# --------------------------------------------------------------------------- music API


def test_music_library_and_custom_music_upload(tmp_path: Path):
    tracks = public_tracks()
    assert tracks and tracks[0]["default"] and all(track["license"] for track in tracks)
    assert {"inspirational", "cinematic", "calm_educational"} <= {track["category"] for track in tracks}
    with pytest.raises(StudioError):
        library_track("not-a-track")
    settings = Settings(data_dir=tmp_path / "studio", max_file_bytes=20_000_000, max_session_bytes=60_000_000, cors_origins=())
    client = TestClient(create_app(settings, Services()))
    owner = {"X-Studio-Client": str(uuid.uuid4())}
    listing = client.get("/api/music/library", headers=owner).json()
    assert listing["tracks"][0]["track_id"] == tracks[0]["track_id"] and "file" not in listing["tracks"][0]
    preview = client.get(f"/api/music/library/{tracks[1]['track_id']}/preview", headers=owner)
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("audio/")
    song = tmp_path / "theme.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:d=8", str(song))
    started = client.post("/api/uploads/initiate", headers=owner, json={"kind": "music", "filename": "theme.wav", "size_bytes": song.stat().st_size}).json()
    client.put(started["upload"]["url"], content=song.read_bytes())
    done = client.post(f"/api/uploads/{started['upload_id']}/complete", headers=owner)
    assert done.status_code == 200, done.text
    assert client.get(f"/api/uploads/{started['upload_id']}/preview-url", headers=owner).json()["url"]
    video_as_music = client.post("/api/uploads/initiate", headers=owner, json={"kind": "music", "filename": "lesson.mp4", "size_bytes": 1000})
    assert video_as_music.status_code == 415
    short = tmp_path / "blip.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:d=1", str(short))
    blip = client.post("/api/uploads/initiate", headers=owner, json={"kind": "music", "filename": "blip.wav", "size_bytes": short.stat().st_size}).json()
    client.put(blip["upload"]["url"], content=short.read_bytes())
    rejected = client.post(f"/api/uploads/{blip['upload_id']}/complete", headers=owner)
    assert rejected.status_code == 422 and "between" in rejected.json()["error"]["message"]
