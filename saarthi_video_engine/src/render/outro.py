"""Append a supplied outro without stretching it or inventing soundtrack.

The lesson stays first. The outro is scaled with its aspect ratio preserved
and padded onto the lesson canvas when the two sizes differ. A silent pad is
used only when the outro file itself has no audio stream.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def fitted_size(src_w: int, src_h: int, canvas_w: int, canvas_h: int) -> tuple[int, int]:
    """Largest even size that fits inside the canvas without changing aspect."""
    if min(src_w, src_h, canvas_w, canvas_h) <= 0:
        raise ValueError("dimensions must be positive")
    scale = min(canvas_w / src_w, canvas_h / src_h)
    width = max(2, int(src_w * scale) // 2 * 2)
    height = max(2, int(src_h * scale) // 2 * 2)
    if width > canvas_w:
        width = canvas_w // 2 * 2
    if height > canvas_h:
        height = canvas_h // 2 * 2
    return width, height


def outro_filter(src_w: int, src_h: int, canvas_w: int, canvas_h: int, fps: int = 30) -> str:
    fitted_size(src_w, src_h, canvas_w, canvas_h)
    return (
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}"
    )


def append_outro(
    lesson: Path,
    outro: Path,
    destination: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
) -> dict:
    """Write destination = lesson picture, then the outro picture."""
    lesson_probe = _probe(ffprobe, lesson)
    outro_probe = _probe(ffprobe, outro)
    lesson_video = _stream(lesson_probe, "video")
    outro_video = _stream(outro_probe, "video")
    lesson_audio = _stream(lesson_probe, "audio")
    outro_audio = _stream(outro_probe, "audio")
    canvas_w = int(lesson_video["width"])
    canvas_h = int(lesson_video["height"])
    src_w = int(outro_video["width"])
    src_h = int(outro_video["height"])
    fit_w, fit_h = fitted_size(src_w, src_h, canvas_w, canvas_h)
    outro_duration = float(outro_probe["format"]["duration"])
    lesson_duration = float(lesson_probe["format"]["duration"])
    expected = lesson_duration + outro_duration
    sample_rate = str(lesson_audio.get("sample_rate") or "48000") if lesson_audio else "48000"
    work = destination.parent / "_outro_work"
    work.mkdir(parents=True, exist_ok=True)
    normalized = work / "outro_normalized.mp4"
    video_filter = outro_filter(src_w, src_h, canvas_w, canvas_h, 30)
    command = [ffmpeg, "-y", "-i", str(outro)]
    if outro_audio:
        command += [
            "-vf", video_filter,
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-ar", sample_rate, "-ac", "2",
            "-t", f"{outro_duration:.3f}",
            str(normalized),
        ]
        audio_policy = "outro audio kept and resampled to the lesson rate"
    else:
        command += [
            "-f", "lavfi", "-t", f"{outro_duration:.3f}",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
            "-filter_complex", f"[0:v]{video_filter}[v]",
            "-map", "[v]", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-shortest",
            str(normalized),
        ]
        audio_policy = (
            "the outro file has no audio stream; silence was padded so the joined "
            "file can play. No voice or music was created for it"
        )
    _run(command)
    listing = work / "concat.txt"
    listing.write_text(
        "file '{}'\nfile '{}'\n".format(_concat_path(lesson), _concat_path(normalized)),
        encoding="utf-8",
    )
    copy_command = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(destination)]
    copied = subprocess.run(copy_command, check=False)
    final = _probe(ffprobe, destination) if copied.returncode == 0 and destination.is_file() else None
    expected = lesson_duration + outro_duration
    if final is None or abs(float(final["format"]["duration"]) - expected) > 0.75:
        _run([
            ffmpeg, "-y", "-i", str(lesson), "-i", str(normalized),
            "-filter_complex",
            "[0:v]setpts=PTS-STARTPTS[v0];[1:v]setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[v];"
            "[0:a][1:a]concat=n=2:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ar", sample_rate, "-ac", "2",
            str(destination),
        ])
        final = _probe(ffprobe, destination)
    final_duration = float(final["format"]["duration"])
    return {
        "outro_path": str(outro),
        "lesson_duration": round(lesson_duration, 3),
        "outro_duration": round(outro_duration, 3),
        "expected_duration": round(expected, 3),
        "final_duration": round(final_duration, 3),
        "duration_delta": round(final_duration - expected, 3),
        "outro_width": src_w,
        "outro_height": src_h,
        "fitted_width": fit_w,
        "fitted_height": fit_h,
        "canvas_width": canvas_w,
        "canvas_height": canvas_h,
        "outro_audio": bool(outro_audio),
        "audio_policy": audio_policy,
        "video_streams": sum(1 for stream in final["streams"] if stream.get("codec_type") == "video"),
        "audio_streams": sum(1 for stream in final["streams"] if stream.get("codec_type") == "audio"),
    }


def _concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _stream(probe: dict, kind: str) -> dict | None:
    return next((stream for stream in probe.get("streams") or [] if stream.get("codec_type") == kind), None)


def _probe(ffprobe: str, path: Path) -> dict:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({completed.returncode}): {' '.join(command[:8])}")


def tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if local.is_file():
        return str(local)
    raise RuntimeError(name)
