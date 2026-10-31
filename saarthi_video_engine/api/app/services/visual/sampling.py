"""Adaptive frame sampling for one uploaded clip.

Frames are spread across the clip, never only the first frame. Black, blank,
and flash frames are rejected and retried once a little later. A frame that is
almost identical to the one kept before it is dropped. Frames are scaled down
before they are saved, and every image is closed after it is measured.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

SAMPLER_VERSION = "1"
FRAME_WIDTH = 336
THUMB_WIDTH = 320
DARK_MEAN = 18.0
BRIGHT_MEAN = 240.0
FLAT_STDDEV = 6.0
DUPLICATE_DIFF = 2.5


@dataclass
class FrameSample:
    timestamp: float
    frame: str
    mean_luma: float
    stddev_luma: float
    kept: bool
    reason: str


def frame_count(duration: float) -> int:
    """Few frames for a short clip, more for a long one, never thousands."""
    if duration <= 0:
        return 0
    if duration < 1.0:
        return 2
    if duration <= 6.0:
        return 4
    if duration <= 20.0:
        return 6
    return min(12, 6 + int((duration - 20.0) // 10.0) + 1)


def timestamps(duration: float) -> list[float]:
    count = frame_count(duration)
    if count == 0:
        return []
    margin = min(0.25, duration * 0.05)
    span = duration - 2 * margin
    return [round(margin + span * (index + 0.5) / count, 3) for index in range(count)]


def measure(image: Image.Image) -> tuple[float, float]:
    grey = image.convert("L")
    stat = ImageStat.Stat(grey)
    grey.close()
    return float(stat.mean[0]), float(stat.stddev[0])


def rejection(mean: float, stddev: float) -> str:
    if mean < DARK_MEAN:
        return "black"
    if mean > BRIGHT_MEAN and stddev < FLAT_STDDEV * 2:
        return "flash"
    if stddev < FLAT_STDDEV:
        return "flat"
    return ""


def _small(image: Image.Image) -> Image.Image:
    return image.convert("L").resize((16, 16), Image.Resampling.BOX)


def near_duplicate(left: Image.Image, right: Image.Image) -> bool:
    a, b = _small(left), _small(right)
    diff = ImageChops.difference(a, b)
    value = ImageStat.Stat(diff).mean[0]
    for item in (a, b, diff):
        item.close()
    return value < DUPLICATE_DIFF


def _grab(ffmpeg: str, source: Path, stamp: float, dest: Path) -> bool:
    completed = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{stamp:.3f}", "-i", str(source), "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", str(dest)],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return completed.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def sample_clip(source: Path, duration: float, folder: Path, ffmpeg: str | None = None) -> dict:
    """Write frames/ and thumb.jpg under folder. Returns the sampling record."""
    ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    frames_dir = folder / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    planned = timestamps(duration)
    step = (duration / max(1, len(planned))) / 2 if planned else 0.0
    samples: list[FrameSample] = []
    previous: Image.Image | None = None
    best: tuple[float, Path] | None = None
    for index, stamp in enumerate(planned):
        dest = frames_dir / f"f{index:02d}.jpg"
        tried = [stamp, min(max(0.0, duration - 0.05), stamp + step * 0.6)]
        record = None
        for attempt, when in enumerate(tried):
            if not _grab(ffmpeg, source, when, dest):
                record = FrameSample(when, "", 0.0, 0.0, False, "unreadable")
                continue
            with Image.open(dest) as image:
                image.load()
                mean, stddev = measure(image)
                reason = rejection(mean, stddev)
                if not reason and previous is not None and near_duplicate(previous, image):
                    reason = "near_duplicate"
                if reason:
                    record = FrameSample(when, "", round(mean, 2), round(stddev, 2), False, reason)
                    if reason == "near_duplicate":
                        break
                    continue
                if previous is not None:
                    previous.close()
                previous = image.copy()
            record = FrameSample(when, dest.name, round(mean, 2), round(stddev, 2), True, "")
            if best is None or stddev > best[0]:
                best = (stddev, dest)
            break
        if record is not None and not record.kept:
            dest.unlink(missing_ok=True)
        if record is not None:
            samples.append(record)
    if previous is not None:
        previous.close()
    thumb = folder / "thumb.jpg"
    thumb.unlink(missing_ok=True)
    if best is not None:
        with Image.open(best[1]) as image:
            ratio = THUMB_WIDTH / float(image.width)
            small = image.convert("RGB").resize((THUMB_WIDTH, max(2, int(image.height * ratio))), Image.Resampling.LANCZOS)
            small.save(thumb, quality=85)
            small.close()
    result = {
        "sampler_version": SAMPLER_VERSION,
        "duration_seconds": round(float(duration), 3),
        "planned": planned,
        "frames": [asdict(item) for item in samples],
        "kept": sum(1 for item in samples if item.kept),
        "thumbnail": "thumb.jpg" if thumb.is_file() else None,
    }
    (folder / "sampling.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
