"""Improved pass for the two Dr. Shivin recordings.

Uses the detected Hindi transcript, a separate whisper.cpp English translation,
and a layout chosen from the completed reference set. Does not modify the sources.
"""

from __future__ import annotations

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
from editing_plan.style_choice import choose_exemplar  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from transcription.align import attach_english  # noqa: E402
from transcription.whisper_cpp import transcript_from_payload  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

EVAL = ROOT / "outputs" / "dr_shivin_evaluation"
RAW = ROOT.parent / "DR_SHIVIN_RAW"
DEFAULTS = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
PPE = {
    "id": "ppe-hospital",
    "path": "assets/ppe_hospital.png",
    "kind": "image",
    "match_terms": ["hospital", "covid", "protective"],
    "reason": (
        "The sentence says he is on hospital COVID duty in a PPE kit. "
        "The still is a hospital PPE gown-up area, checked by looking at the file. "
        "China News Service, CC BY 4.0."
    ),
    "page_url": "https://commons.wikimedia.org/wiki/File:AsiaWorld-Expo_makeshift_hospital_PPE_Gown_up_area_20200801.png",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0",
    "artist": "China News Service",
}


def main() -> int:
    dataset = load_dataset()
    for video_id, filename in (("VIDEO_1", "VIDEO_1.mp4"), ("VIDEO_2", "VIDEO_2.mp4")):
        process(dataset, video_id, RAW / filename)
    print("improved plans written")
    return 0


def process(dataset: dict, video_id: str, source: Path) -> None:
    work = EVAL / video_id / "work"
    original = transcript_from_payload(
        json.loads((work / "asr.json").read_text(encoding="utf-8")),
        requested_language="auto",
        model_name="ggml-tiny.bin",
        provenance="whisper.cpp ggml-tiny, -l auto, translate=false.",
    )
    translated = transcript_from_payload(
        json.loads((work / "asr_en.json").read_text(encoding="utf-8")),
        requested_language="hi",
        translate=True,
        model_name="ggml-tiny.bin",
        provenance="whisper.cpp ggml-tiny, -l hi -tr.",
    )
    transcript = original
    if original["language"] not in {"en", "english"}:
        transcript = attach_english(original, translated["segments"])
    selection = choose_segment(transcript["segments"])
    probe = _probe(source)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    portrait = int(video["height"]) > int(video["width"])
    english_query = " ".join(
        segment.get("english_text") or "" for segment in transcript["segments"]
    )
    hit = choose_exemplar(dataset, english_query or selection["text"], portrait=portrait)
    assets = []
    if any(term in english_query.lower() for term in ("ppe", "hospital", "covid")):
        assets.append(PPE)
    composition = EVAL / video_id / "composition"
    asset_dir = composition / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    _reuse_or_cut(video_id, source, selection, asset_dir)
    if assets:
        shutil.copyfile(EVAL / "asset_cache" / "ppe_hospital.png", asset_dir / "ppe_hospital.png")
        (EVAL / "asset_cache" / "ppe_hospital.json").write_text(
            json.dumps({key: value for key, value in PPE.items() if key != "path"}, indent=2),
            encoding="utf-8",
        )
    narration_probe = _probe(asset_dir / "narration.wav")
    duration = round(float(narration_probe["format"]["duration"]), 3)
    credit = "Dr. Shivin. Local recording supplied for this evaluation."
    license_name = "not stated in the file"
    license_url = "not-stated-in-file"
    if assets:
        credit += " PPE photograph: China News Service, CC BY 4.0."
        license_name = "CC BY 4.0 for the PPE still; source-recording license is not stated"
        license_url = PPE["license_url"]
    plan = build_phase2_plan(
        dataset,
        transcript=transcript,
        selection=selection,
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": 30,
            "attribution_line": credit,
            "license": license_name,
            "license_url": license_url,
            "changes": "Vertical excerpt with English captions from a whisper.cpp translation pass.",
        },
        assets=assets,
        exemplar_id=hit["reference_id"],
        plan_id=f"dr-shivin-{video_id.lower()}",
        primary_category=hit.get("primary_category") or "lecture",
        secondary_categories=["upsc"],
    )
    plan["render"]["object_fit"] = "cover" if portrait else "contain"
    _fit(plan, duration)
    plan["limitations"].append(
        f"Style exemplar {hit['reference_id']} scored {hit['score']} on token overlap with the English translation. "
        "That score is overlap, not a measured edit match."
    )
    plan["limitations"].append(
        "English captions are whisper.cpp -tr output from ggml-tiny. They were not hand-corrected. "
        "The original-language pass did not return Devanagari; language was still the provider's Hindi detection."
    )
    errors = validate_plan(plan)
    if errors:
        raise SystemExit(video_id + "\n" + "\n".join(errors))
    write_composition(plan, DEFAULTS, composition / "index.html")
    _dump(EVAL / video_id / "editing_plan.json", plan)
    _dump(EVAL / video_id / "transcript.json", transcript)
    _dump(
        EVAL / video_id / "captions.json",
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
    print(video_id, plan["plan_id"], plan["style"]["layout_family"], hit["reference_id"], "captions", sum(1 for item in plan["decisions"] if item["action"] == "caption"))


def _reuse_or_cut(video_id: str, source: Path, selection: dict, asset_dir: Path) -> None:
    baseline = EVAL / video_id / "baseline" / "editing_plan.json"
    same = False
    if baseline.is_file():
        old = json.loads(baseline.read_text(encoding="utf-8"))
        interval = old["audio"]["narration_interval"]
        same = abs(interval["source_in"] - selection["source_in"]) < 0.05 and abs(interval["source_out"] - selection["source_out"]) < 0.05
    if same:
        base_assets = EVAL / video_id / "baseline" / "composition" / "assets"
        shutil.copyfile(base_assets / "teacher.mp4", asset_dir / "teacher.mp4")
        shutil.copyfile(base_assets / "narration.wav", asset_dir / "narration.wav")
        return
    ffmpeg = _tool("ffmpeg")
    common = [ffmpeg, "-y", "-i", str(source), "-ss", f"{selection['source_in']:.3f}", "-to", f"{selection['source_out']:.3f}"]
    subprocess.run(common + ["-map", "0:v:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-g", "30", "-movflags", "+faststart", str(asset_dir / "teacher.mp4")], check=True)
    subprocess.run(common + ["-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(asset_dir / "narration.wav")], check=True)


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


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
