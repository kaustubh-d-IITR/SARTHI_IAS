"""Post-render inspection of the finished MP4 with ffmpeg and sampled frames.

Hard failures stop the render from being marked completed. Warnings are recorded in
quality_report.json next to the video and shown in the Studio.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np

GRID_W, GRID_H = 36, 64


def _run(command: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _frame(ffmpeg: str, video: Path, seconds: float, width: int = GRID_W, height: int = GRID_H, cover: bool = True) -> np.ndarray | None:
    scale = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if cover else f"scale={width}:{height}"
    completed = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"{scale},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60, check=False,
    )
    if completed.returncode != 0 or len(completed.stdout) < width * height:
        return None
    return np.frombuffer(completed.stdout[: width * height], dtype=np.uint8).reshape(height, width).astype(np.float32)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return 0.0 if denom < 1e-6 else float((a * b).sum() / denom)


def _band(frame: np.ndarray, top: float, bottom: float) -> np.ndarray:
    h = frame.shape[0]
    return frame[int(h * top): int(h * bottom)]


def inspect(
    *,
    ffmpeg: str,
    final: Path,
    final_meta: dict,
    lesson_seconds: float,
    plan: dict,
    assets: Path,
    settings: dict | None,
    outro_report: dict,
) -> dict:
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, **data) -> None:
        checks.append({"name": name, "status": status, "detail": detail, **data})

    width, height = int(final_meta.get("width") or 0), int(final_meta.get("height") or 0)
    add("canvas", "passed" if (width, height) == (720, 1280) else "failed", f"{width}×{height}, 30 fps expected 720×1280.")
    expected = float(outro_report.get("expected_duration") or 0)
    duration = float(final_meta.get("duration_seconds") or 0)
    add("duration", "passed" if abs(duration - expected) <= 0.75 else "failed", f"{duration:.2f} s against the expected {expected:.2f} s (lesson plus outro).")

    decoded = _run([ffmpeg, "-v", "error", "-i", str(final), "-f", "null", "-"], timeout=900)
    add("playable", "passed" if decoded.returncode == 0 and not decoded.stderr.strip() else "failed", "The whole file decodes without errors." if decoded.returncode == 0 and not decoded.stderr.strip() else "ffmpeg reported decode errors.")

    black = _run([ffmpeg, "-v", "info", "-t", f"{lesson_seconds:.3f}", "-i", str(final), "-vf", "blackdetect=d=0.4:pix_th=0.08", "-an", "-f", "null", "-"], timeout=900)
    spans = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", black.stderr)]
    add("black_frames", "failed" if spans else "passed", "No black sections in the lesson." if not spans else f"Black picture at {', '.join(f'{a:.1f}–{b:.1f} s' for a, b in spans[:4])}.", spans=spans)

    volume = _run([ffmpeg, "-v", "info", "-i", str(final), "-af", "volumedetect", "-vn", "-f", "null", "-"], timeout=900)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", volume.stderr)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", volume.stderr)
    peak_db = float(peak.group(1)) if peak else None
    add("clipping", "passed" if peak_db is not None and peak_db < -0.1 else "warning", f"Peak {peak_db} dB." if peak_db is not None else "Peak level could not be measured.", peak_db=peak_db, mean_db=float(mean.group(1)) if mean else None)

    silence = _run([ffmpeg, "-v", "info", "-t", f"{lesson_seconds:.3f}", "-i", str(final), "-af", "silencedetect=n=-55dB:d=2.5", "-vn", "-f", "null", "-"], timeout=900)
    gaps = re.findall(r"silence_start: ([\d.]+)", silence.stderr)
    add("audio_continuity", "passed" if not gaps else "warning", "Sound throughout the lesson." if not gaps else f"Near-silence of 2.5 s or more starts at {', '.join(gaps[:4])} s.")

    teacher = assets / "teacher.mp4"
    pictures = [item for item in plan["decisions"] if item.get("action") == "replace_with_broll"]
    for decision in pictures:
        mid = (float(decision["start"]) + float(decision["end"])) / 2
        shown = _frame(ffmpeg, final, mid)
        clip_path = assets / Path(str(decision.get("asset_path") or "")).name
        clip = _frame(ffmpeg, clip_path, mid - float(decision["start"])) if clip_path.is_file() else None
        lesson = _frame(ffmpeg, teacher, mid) if teacher.is_file() else None
        if shown is None or clip is None:
            add("broll_visible", "warning", f"{decision.get('asset_id')}: frames could not be read.", decision_id=decision["id"])
            continue
        band = (0.3, 0.62)
        with_clip = _corr(_band(shown, *band), _band(clip, *band))
        with_teacher = _corr(_band(shown, *band), _band(lesson, *band)) if lesson is not None else 0.0
        if with_teacher >= 0.6 and with_clip < 0.3:
            status, detail = "failed", f"{decision.get('asset_id')}: the teacher is on screen where this cutaway was approved."
        elif with_clip >= 0.35 and with_clip >= with_teacher:
            status, detail = "passed", f"{decision.get('asset_id')}: the clip is on screen at {mid:.1f} s."
        else:
            status, detail = "warning", f"{decision.get('asset_id')}: could not confirm the clip at {mid:.1f} s (clip {with_clip:.2f}, teacher {with_teacher:.2f})."
        add("broll_visible", status, detail, decision_id=decision["id"], clip_similarity=round(with_clip, 3), teacher_similarity=round(with_teacher, 3))

    caption_decisions = [item for item in plan["decisions"] if item.get("action") == "caption" and (item.get("text") or "").strip()]
    if caption_decisions:
        position = float(((settings or {}).get("captions") or {}).get("position") or 0.8)
        seen = 0
        sample = caption_decisions[:: max(1, len(caption_decisions) // 6)][:6]
        for decision in sample:
            frame = _frame(ffmpeg, final, (float(decision["start"]) + float(decision["end"])) / 2, 180, 320, cover=False)
            if frame is None:
                continue
            band = _band(frame, max(0.0, position - 0.07), min(1.0, position + 0.07))
            if (band > 205).mean() > 0.004:
                seen += 1
        add("captions_visible", "passed" if seen == len(sample) else "warning", f"Caption text found in {seen} of {len(sample)} sampled lines.")
        if settings:
            captions = settings["captions"]
            longest = max(len(item.get("text") or "") for item in caption_decisions)
            lines = max(1, math.ceil(longest * captions["size"] * 0.62 / (720 - 80)))
            bottom = captions["position"] * 1280 + lines * captions["size"] * 1.25 / 2
            add("captions_inside_canvas", "passed" if bottom <= 1280 - 40 else "failed", f"The lowest caption line ends at about {bottom:.0f} px of 1280.")

    if settings:
        html_path = assets.parent / "index.html"
        html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
        if settings["transitions"]["style"] == "cut":
            blended = bool(re.search(r'tl\.fromTo\("#broll-\d+", \{ opacity: 0', html))
            add("no_double_exposure", "failed" if blended else "passed", "Cutaways use hard cuts." if not blended else "A cutaway fades over the teacher although clean cuts were chosen.")
        outro_seconds = float(outro_report.get("outro_duration") or 0)
        if outro_seconds > 0 and settings["outro"]["fill"] == "blur":
            frame = _frame(ffmpeg, final, duration - outro_seconds / 2, 72, 128, cover=False)
            if frame is not None:
                edges = float(np.concatenate([frame[:12].ravel(), frame[-12:].ravel()]).mean())
                add("outro_framing", "passed" if edges > 6 else "failed", "The outro fills the 9:16 frame." if edges > 6 else "The outro shows black bars.", edge_luma=round(edges, 1))
        music = plan["audio"].get("music") or {}
        if outro_seconds > 0 and music.get("path") and settings["music"]["into_outro"] and not outro_report.get("outro_audio"):
            tail = _run([ffmpeg, "-v", "info", "-ss", f"{duration - outro_seconds + 0.2:.3f}", "-t", f"{max(0.5, outro_seconds - 1.5):.3f}", "-i", str(final), "-af", "volumedetect", "-vn", "-f", "null", "-"], timeout=120)
            level = re.search(r"mean_volume:\s*(-?[\d.]+) dB", tail.stderr)
            tail_db = float(level.group(1)) if level else None
            add("outro_music", "passed" if tail_db is not None and tail_db > -60 else "failed", f"Music under the outro measures {tail_db} dB." if tail_db is not None else "The outro audio could not be measured.", outro_db=tail_db)
        speech = sorted((float(s["start"]), float(s["end"])) for s in plan["input_media"]["transcript"]["segments"])
        gaps_between = [(a_end, b_start) for (_, a_end), (b_start, _) in zip(speech, speech[1:]) if b_start - a_end >= 0.5]
        if gaps_between:
            start, end = max(gaps_between, key=lambda gap: gap[1] - gap[0])
            pause = _run([ffmpeg, "-v", "info", "-ss", f"{start + 0.1:.3f}", "-t", f"{max(0.2, end - start - 0.2):.3f}", "-i", str(final), "-af", "volumedetect", "-vn", "-f", "null", "-"], timeout=120)
            level = re.search(r"mean_volume:\s*(-?[\d.]+) dB", pause.stderr)
            level_db = float(level.group(1)) if level else None
            expected_gain = float(music.get("gain_open") or 0)
            if music.get("path") and expected_gain > 0:
                add("music_mixed", "passed" if level_db is not None and level_db > -60 else "warning", f"Music in the longest pause measures {level_db} dB (gain {expected_gain}).", pause_db=level_db, gain=expected_gain)
            else:
                add("music_mixed", "passed", "No music was selected.", pause_db=level_db)

    failed = [item for item in checks if item["status"] == "failed"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {"status": "failed" if failed else ("warnings" if warnings else "passed"), "failed": len(failed), "warnings": len(warnings), "checks": checks}
