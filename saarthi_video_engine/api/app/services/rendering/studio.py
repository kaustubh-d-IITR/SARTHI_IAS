"""Render preparation for plan versions that carry Studio settings.

Everything here produces inputs for the existing HyperFrames composition: where the
teacher is in the frame, a blurred backdrop made from the lesson itself, the music bed
(looped to length) and its continuation under the silent outro.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SAMPLE_WIDTH = 160


def _run(command: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({completed.returncode}): {' '.join(command[:6])}")
    return completed


def estimate_focus(video: Path, width: int, height: int, ffmpeg: str, seconds: float = 120.0) -> dict:
    """Where the teacher is: the centroid of motion across sampled frames.

    A lecture's board and background are still; the teacher moves. Column and row
    motion energy above the median gives the horizontal and vertical centre. Too little
    motion falls back to the centre.
    """
    sample_h = max(2, int(round(SAMPLE_WIDTH * height / max(width, 1) / 2)) * 2)
    completed = _run(
        [ffmpeg, "-v", "error", "-t", f"{seconds:.1f}", "-i", str(video), "-vf", f"fps=2,scale={SAMPLE_WIDTH}:{sample_h}:flags=area,format=gray", "-f", "rawvideo", "-"],
        timeout=300,
    )
    data = np.frombuffer(completed.stdout, dtype=np.uint8)
    count = data.size // (SAMPLE_WIDTH * sample_h)
    if count < 3:
        return {"focus_x": 0.5, "focus_y": 0.4, "method": "default", "confidence": 0.0}
    frames = data[: count * SAMPLE_WIDTH * sample_h].reshape(count, sample_h, SAMPLE_WIDTH).astype(np.float32)
    motion = np.abs(np.diff(frames, axis=0)).mean(axis=0)
    columns = motion.mean(axis=0)
    rows = motion.mean(axis=1)
    kernel = np.ones(9) / 9
    columns = np.convolve(columns, kernel, mode="same")
    rows = np.convolve(rows, kernel, mode="same")
    col_weight = np.clip(columns - np.median(columns), 0, None) ** 2
    row_weight = np.clip(rows - np.median(rows), 0, None) ** 2
    if col_weight.sum() <= 1e-6 or float(columns.max()) < 0.6:
        return {"focus_x": 0.5, "focus_y": 0.4, "method": "default", "confidence": 0.0}
    focus_x = float((col_weight * np.arange(SAMPLE_WIDTH)).sum() / col_weight.sum() / (SAMPLE_WIDTH - 1))
    focus_y = float((row_weight * np.arange(sample_h)).sum() / row_weight.sum() / (sample_h - 1)) if row_weight.sum() > 1e-6 else 0.4
    confidence = float(min(1.0, (columns.max() - np.median(columns)) / (columns.max() + 1e-6)))
    return {"focus_x": round(min(0.9, max(0.1, focus_x)), 3), "focus_y": round(min(0.6, max(0.2, focus_y - 0.08)), 3), "method": "motion", "confidence": round(confidence, 3)}


def make_backdrop(video: Path, dest: Path, canvas_w: int, canvas_h: int, at_seconds: float, ffmpeg: str) -> Path:
    """One blurred, slightly darkened frame of the lesson, cropped to the canvas."""
    _run([
        ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(video), "-frames:v", "1",
        "-vf", f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,crop={canvas_w}:{canvas_h},gblur=sigma=28,eq=brightness=-0.10:saturation=0.85",
        "-q:v", "3", str(dest),
    ])
    return dest


def prepare_music(source: Path, dest: Path, duration: float, ffmpeg: str) -> Path:
    """The music bed for the lesson, looped if the track is shorter than the lesson."""
    _run([ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dest)])
    return dest


def music_tail(source: Path, dest: Path, offset: float, seconds: float, level: float, fade_out: float, ffmpeg: str) -> Path:
    """The same track continuing under the silent outro, at the pause level, fading out at the end."""
    fade = max(0.1, min(fade_out, seconds))
    _run([
        ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(source), "-vn",
        "-af", (
            f"atrim=start={offset:.3f}:duration={seconds:.3f},asetpts=PTS-STARTPTS,"
            f"volume={level:.4f},afade=t=out:st={max(0.0, seconds - fade):.3f}:d={fade:.3f}"
        ),
        "-t", f"{seconds:.3f}", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dest),
    ])
    return dest


def studio_block(settings: dict, *, src_width: int, src_height: int, focus: dict, backdrop: str, broll_sizes: dict[str, tuple[int, int]]) -> dict:
    """What the composer needs from the settings, with the measured focus filled in."""
    framing = settings["framing"]
    focus_x = framing["focus_x"] if framing.get("focus_x") is not None else focus["focus_x"]
    portrait = src_height > src_width
    if framing["mode"] == "fill" or portrait:
        box_height = 1.0
    elif framing["mode"] == "fit":
        box_height = None
    else:
        box_height = framing["teacher_height"]
    return {
        "teacher": {
            "src_width": src_width,
            "src_height": src_height,
            "box_height": box_height,
            "focus_x": round(float(focus_x), 3),
            "focus_y": framing["focus_y"],
            "focus_method": "user" if framing.get("focus_x") is not None else focus["method"],
            "backdrop": backdrop,
        },
        "broll_fit": settings["broll"]["fit"],
        "broll_sizes": {key: list(value) for key, value in broll_sizes.items()},
        "transition": dict(settings["transitions"]),
        "captions": dict(settings["captions"]),
        "effects": {name: dict(value) for name, value in settings["effects"].items() if value.get("enabled")},
        "intro_seconds": settings["intro_seconds"],
    }
