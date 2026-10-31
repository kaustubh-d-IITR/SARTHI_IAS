"""Baseline pass: current Phase 2 planner, no algorithm changes.

Writes compositions and plans under outputs/dr_shivin_evaluation.
Does not modify the source files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from editing_plan.segment import choose_segment  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from transcription.whisper_cpp import transcript_from_payload  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

RAW = ROOT.parent / "DR_SHIVIN_RAW"
EVAL = ROOT / "outputs" / "dr_shivin_evaluation"
DEFAULTS = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
SOURCES = {
    "VIDEO_1": RAW / "VIDEO_1.mp4",
    "VIDEO_2": RAW / "VIDEO_2.mp4",
}


def main() -> int:
    manifest = []
    for video_id, source in SOURCES.items():
        manifest.append(process(video_id, source))
    (EVAL / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("baseline plans written")
    return 0


def process(video_id: str, source: Path) -> dict:
    work = EVAL / video_id / "work"
    payload = json.loads((work / "asr.json").read_text(encoding="utf-8"))
    transcript = transcript_from_payload(
        payload,
        requested_language="auto",
        model_name="ggml-tiny.bin",
        provenance="whisper.cpp ggml-tiny, -l auto, translate=false. Baseline pass.",
    )
    selection = choose_segment(transcript["segments"])
    composition = EVAL / video_id / "baseline" / "composition"
    assets = composition / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    teacher = assets / "teacher.mp4"
    narration = assets / "narration.wav"
    _cut(source, selection["source_in"], selection["source_out"], teacher, narration)
    narration_probe = _probe(narration)
    teacher_probe = _probe(teacher)
    duration = round(float(narration_probe["format"]["duration"]), 3)
    video = next(stream for stream in teacher_probe["streams"] if stream["codec_type"] == "video")
    plan = build_phase2_plan(
        load_dataset(),
        transcript=transcript,
        selection=selection,
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": 30,
            "attribution_line": "Dr. Shivin. Local recording supplied for this evaluation.",
            "license": "not stated in the file",
            "license_url": "not-stated-in-file",
            "changes": "Baseline excerpt. Speech was not replaced.",
        },
        assets=[],
    )
    _fit(plan, duration)
    errors = validate_plan(plan)
    if errors:
        raise SystemExit(video_id + "\n" + "\n".join(errors))
    write_composition(plan, DEFAULTS, composition / "index.html")
    out_dir = EVAL / video_id / "baseline"
    _dump(out_dir / "editing_plan.json", plan)
    _dump(out_dir / "transcript.json", transcript)
    _dump(
        out_dir / "captions.json",
        [
            {
                "start": segment["start"],
                "end": segment["end"],
                "original_text": segment["original_text"],
                "english_text": segment["english_text"],
            }
            for segment in plan["input_media"]["transcript"]["segments"]
        ],
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    probe = _probe(source)
    return {
        "video_id": video_id,
        "path": str(source),
        "bytes": source.stat().st_size,
        "sha256": digest,
        "duration_seconds": float(probe["format"]["duration"]),
        "selected_in": selection["source_in"],
        "selected_out": selection["source_out"],
        "selection_reason": selection["reason"],
        "language": transcript["language"],
        "language_source": transcript["language_source"],
        "english_caption_count": sum(1 for item in plan["decisions"] if item["action"] == "caption"),
        "plan_id": plan["plan_id"],
        "layout": plan["style"]["layout_family"],
        "exemplar": plan["style"]["exemplar_reference_id"],
    }


def _fit(plan: dict, duration: float) -> None:
    plan["input_media"]["duration_seconds"] = duration
    plan["audio"]["narration_interval"]["end"] = duration
    kept = []
    for segment in plan["input_media"]["transcript"]["segments"]:
        if segment["start"] >= duration - 0.05:
            continue
        segment["end"] = round(min(segment["end"], duration), 3)
        if segment["end"] - segment["start"] >= 0.2:
            kept.append(segment)
    plan["input_media"]["transcript"]["segments"] = kept
    live = {segment["id"] for segment in kept}
    decisions = []
    for decision in plan["decisions"]:
        if decision["action"] == "caption" and decision["id"][4:] not in live:
            continue
        if decision["start"] >= duration:
            continue
        decision["end"] = round(min(decision["end"], duration), 3)
        if decision["end"] > decision["start"]:
            decisions.append(decision)
    plan["decisions"] = decisions


def _cut(source: Path, start: float, end: float, teacher: Path, narration: Path) -> None:
    ffmpeg = _tool("ffmpeg")
    common = [ffmpeg, "-y", "-i", str(source), "-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
    subprocess.run(
        common + ["-map", "0:v:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-g", "30", "-keyint_min", "30", "-movflags", "+faststart", str(teacher)],
        check=True,
    )
    subprocess.run(
        common + ["-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(narration)],
        check=True,
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
    raise RuntimeError(name)


def _dump(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
