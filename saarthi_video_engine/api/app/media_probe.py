"""Validate a video with ffprobe. The probe stderr is never returned to the client."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.errors import StudioError


def resolve_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def _frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            denominator = float(den)
            if denominator == 0:
                return None
            return round(float(num) / denominator, 3)
        except ValueError:
            return None
    try:
        return round(float(value), 3)
    except ValueError:
        return None


def probe_video(path: Path) -> dict:
    binary = resolve_ffprobe()
    if not binary:
        raise StudioError(
            503,
            "ffprobe_unavailable",
            "ffprobe is not available, so the file cannot be confirmed as a video.",
        )
    try:
        completed = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StudioError(
            422,
            "unreadable_video",
            "This file could not be read as a video.",
        ) from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise StudioError(422, "unreadable_video", "This file could not be read as a video.")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StudioError(422, "unreadable_video", "This file could not be read as a video.") from exc
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not isinstance(video, dict):
        raise StudioError(422, "not_a_video", "The file has no video stream.")
    width = video.get("width")
    height = video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width < 2 or height < 2:
        raise StudioError(422, "not_a_video", "The video dimensions could not be read.")
    raw_duration = (payload.get("format") or {}).get("duration")
    try:
        duration = round(float(raw_duration), 3)
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "not_a_video", "The video duration could not be read.") from exc
    if duration <= 0:
        raise StudioError(422, "not_a_video", "The video duration is empty.")
    result = {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "frame_rate": _frame_rate(video.get("avg_frame_rate")),
    }
    try:
        stream_seconds = round(float(video.get("duration")), 3)
    except (TypeError, ValueError):
        stream_seconds = None
    if stream_seconds and stream_seconds > 0:
        # The picture can end before the container (longer audio); cutaways may only use the picture.
        result["video_duration_seconds"] = stream_seconds
    return result


MUSIC_MIN_SECONDS = 5.0
MUSIC_MAX_SECONDS = 900.0


def probe_audio(path: Path | str) -> dict:
    """A music upload must be real audio between 5 s and 15 min. Cover art streams are allowed."""
    binary = resolve_ffprobe()
    if not binary:
        raise StudioError(503, "ffprobe_unavailable", "ffprobe is not available, so the file cannot be confirmed as audio.")
    try:
        completed = subprocess.run(
            [binary, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,sample_rate,channels", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StudioError(422, "unreadable_audio", "This file could not be read as audio.") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise StudioError(422, "unreadable_audio", "This file could not be read as audio.")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StudioError(422, "unreadable_audio", "This file could not be read as audio.") from exc
    audio = next((stream for stream in payload.get("streams") or [] if stream.get("codec_type") == "audio"), None)
    if not isinstance(audio, dict):
        raise StudioError(422, "not_audio", "The file has no audio stream.")
    try:
        duration = round(float((payload.get("format") or {}).get("duration")), 3)
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "not_audio", "The audio duration could not be read.") from exc
    if duration < MUSIC_MIN_SECONDS or duration > MUSIC_MAX_SECONDS:
        raise StudioError(422, "music_length", f"Music must be between {MUSIC_MIN_SECONDS:.0f} seconds and {MUSIC_MAX_SECONDS / 60:.0f} minutes long.")
    return {
        "duration_seconds": duration,
        "codec": audio.get("codec_name") or "",
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
    }
