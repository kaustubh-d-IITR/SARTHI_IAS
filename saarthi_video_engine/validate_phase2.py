"""Probe the rendered phase-2 MP4 and compare its audio with the source cut.

This does not listen to the file. Continuity is a PCM correlation, not a silence check.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from validation.audio_compare import compare_wavs  # noqa: E402

OUTPUT = ROOT / "outputs" / "phase2"
RENDERED = OUTPUT / "phase2.mp4"
NARRATION = ROOT / "compositions" / "phase2_real_video" / "assets" / "narration.wav"
PLAN = OUTPUT / "editing_plan.json"


def main() -> int:
    if not RENDERED.is_file() or RENDERED.stat().st_size == 0:
        print("rendered mp4 is missing or empty")
        return 1
    probe = _probe(RENDERED)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    wav = OUTPUT / "rendered_audio.wav"
    subprocess.run(
        [_tool("ffmpeg"), "-y", "-i", str(RENDERED), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
        check=True,
        capture_output=True,
    )
    comparison = compare_wavs(NARRATION, wav, max_lag_seconds=0.75)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    snaps = OUTPUT / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    times = _sample_times(plan)
    for stamp in times:
        subprocess.run(
            [
                _tool("ffmpeg"), "-y", "-ss", f"{stamp:.3f}", "-i", str(RENDERED),
                "-frames:v", "1", str(snaps / f"t{stamp:.2f}.jpg"),
            ],
            check=True,
            capture_output=True,
        )
    width = int(video["width"])
    height = int(video["height"])
    report = {
        "path": str(RENDERED),
        "bytes": RENDERED.stat().st_size,
        "width": width,
        "height": height,
        "aspect_9_16": abs((width / height) - (9 / 16)) < 0.01,
        "video_codec": video.get("codec_name"),
        "fps": video.get("r_frame_rate"),
        "video_duration": float(video.get("duration") or probe["format"]["duration"]),
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": audio.get("sample_rate"),
        "audio_channels": audio.get("channels"),
        "audio_duration": float(audio.get("duration") or probe["format"]["duration"]),
        "plan_duration": plan["input_media"]["duration_seconds"],
        "narration_comparison": comparison,
        "snapshot_times": times,
    }
    (OUTPUT / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    review = OUTPUT / "REVIEW.md"
    existing = review.read_text(encoding="utf-8") if review.is_file() else ""
    marker = "## Rendered output"
    block = _markdown(report)
    if marker in existing:
        existing = existing.split(marker)[0].rstrip() + "\n\n"
    review.write_text(existing + "\n" + block, encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["aspect_9_16"] and report["audio_duration"] > 1 else 1


def _sample_times(plan: dict) -> list[float]:
    duration = plan["input_media"]["duration_seconds"]
    captions = [item for item in plan["decisions"] if item["action"] == "caption"]
    picks = []
    if captions:
        picks.append(captions[0]["start"] + 0.4)
        picks.append(captions[len(captions) // 2]["start"] + 0.4)
        picks.append(max(0.2, captions[-1]["end"] - 0.4))
    picks.append(min(duration - 0.2, duration * 0.5))
    unique = []
    for value in picks:
        rounded = round(max(0.1, min(value, duration - 0.1)), 2)
        if rounded not in unique:
            unique.append(rounded)
    return unique


def _markdown(report: dict) -> str:
    audio = report["narration_comparison"]
    return "\n".join(
        [
            "## Rendered output",
            "",
            f"- File: `{report['path']}`",
            f"- Size: {report['bytes']} bytes",
            f"- Picture: {report['width']}x{report['height']} {report['video_codec']} {report['fps']} for {report['video_duration']} s",
            f"- Audio: {report['audio_codec']} {report['audio_sample_rate']} Hz {report['audio_channels']} ch for {report['audio_duration']} s",
            f"- 9:16: {report['aspect_9_16']}",
            f"- Plan duration: {report['plan_duration']} s",
            "",
            "Narration continuity compares the rendered PCM with the narration wav cut from the same source interval. "
            "It is a Pearson correlation after a lag search. It is not silence detection and it is not a claim that the AAC file is bit-identical.",
            "",
            f"- Lag: {audio['lag_seconds']} s",
            f"- Correlation: {audio['correlation']}",
            f"- Reference length: {audio['reference_seconds']} s",
            f"- Rendered length: {audio['rendered_seconds']} s",
            "",
            "Snapshots: " + ", ".join(f"{stamp:.2f}s" for stamp in report["snapshot_times"]),
            "",
        ]
    )


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if local.is_file():
        return str(local)
    raise RuntimeError(f"{name} was not found")


if __name__ == "__main__":
    raise SystemExit(main())
