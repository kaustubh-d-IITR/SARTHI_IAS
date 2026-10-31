"""Generate the local media the proof renders. No network and no reference footage."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg is not on PATH")
    return found


def _run(args: list[str]) -> None:
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        tail = (completed.stderr or "")[-800:]
        raise RuntimeError(f"ffmpeg failed: {tail}")


def generate_synthetic_media(asset_dir: Path, duration: float = 8.0) -> dict[str, str]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    teacher = asset_dir / "teacher.mp4"
    broll = asset_dir / "broll.mp4"
    narration = asset_dir / "narration.wav"
    music = asset_dir / "music_placeholder.wav"
    logo = asset_dir / "logo.svg"

    _run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c=0x1d4e89:s=720x1280:r=30:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "30", "-g", "30", "-keyint_min", "30", "-movflags", "+faststart",
            "-c:a", "aac",
            str(teacher),
        ]
    )
    _run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c=0xc44900:s=720x1280:r=30:d={duration}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "30", "-g", "30", "-keyint_min", "30", "-movflags", "+faststart",
            "-c:a", "aac",
            str(broll),
        ]
    )
    _run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            str(narration),
        ]
    )
    _run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={duration}",
            "-filter:a", "volume=0.2",
            str(music),
        ]
    )
    logo.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="0 0 72 72">
  <circle cx="36" cy="36" r="34" fill="#f5c518"/>
  <text x="36" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="14" fill="#111">TEST</text>
</svg>
""",
        encoding="utf-8",
    )
    return {
        "teacher": "assets/teacher.mp4",
        "broll": "assets/broll.mp4",
        "narration": "assets/narration.wav",
        "music": "assets/music_placeholder.wav",
        "logo": "assets/logo.svg",
    }
