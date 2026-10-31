"""Cut the licensed lecture, build the phase-2 plan, and write the composition.

The original file in test_assets/educator_source is not modified. Picture and
narration for the short are a new cut of the same in and out points.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from editing_plan.segment import choose_segment  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from transcription.whisper_cpp import transcript_from_payload  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

SOURCE_DIR = ROOT / "test_assets" / "educator_source"
ORIGINAL = SOURCE_DIR / "Prof_Walter_Lewin_Atwoods_Machine.ogv"
ASR_JSON = SOURCE_DIR / "asr.json"
PROJECT = ROOT / "compositions" / "phase2_real_video"
OUTPUT = ROOT / "outputs" / "phase2"
DEFAULTS_PATH = ROOT / "configs" / "render.defaults.json"

PAGE = "https://commons.wikimedia.org/wiki/File:Prof_Walter_Lewin_Atwoods_Machine.ogv"
MEDIA = "https://upload.wikimedia.org/wikipedia/commons/e/e4/Prof_Walter_Lewin_Atwoods_Machine.ogv"
LICENSE = "CC BY 3.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/3.0"
ATTRIBUTION = "Walter Lewin / MIT OpenCourseWare. CC BY 3.0. Adapted excerpt."


def main() -> int:
    if not ORIGINAL.is_file() or not ASR_JSON.is_file():
        print("missing original lecture or asr.json")
        return 1
    payload = json.loads(ASR_JSON.read_text(encoding="utf-8"))
    transcript = transcript_from_payload(
        payload,
        requested_language="en",
        translate=False,
        model_name="ggml-tiny.bin",
        provenance=(
            "local whisper-cli 1.8.2, model ggml-tiny.bin, language forced to en "
            "with -l en, translate=false. Word times come from token offsets."
        ),
    )
    selection = choose_segment(transcript["segments"])
    digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
    source_probe = _probe(ORIGINAL)
    _write_source_metadata(digest, source_probe, selection)
    _write_source_readme()
    assets = PROJECT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    teacher = assets / "teacher.mp4"
    narration = assets / "narration.wav"
    _cut(selection["source_in"], selection["source_out"], teacher, narration)
    teacher_probe = _probe(teacher)
    narration_probe = _probe(narration)
    duration = round(float(narration_probe["format"]["duration"]), 3)
    selection = dict(selection)
    selection["rendered_duration"] = duration
    (SOURCE_DIR / "segment_manifest.json").write_text(
        json.dumps(
            {
                "source_file": ORIGINAL.name,
                "source_in": selection["source_in"],
                "source_out": selection["source_out"],
                "duration_seconds": selection["duration"],
                "rendered_narration_seconds": duration,
                "reason": selection["reason"],
                "segment_ids": selection["segment_ids"],
                "text": selection["text"],
                "picture": "assets/teacher.mp4 in the phase-2 composition, cut from the original",
                "narration": "assets/narration.wav, the same in and out points, stream 0",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    dataset = load_dataset()
    plan = build_phase2_plan(
        dataset,
        transcript=transcript,
        selection=selection,
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": int(teacher_probe["streams"][0]["width"]),
            "height": int(teacher_probe["streams"][0]["height"]),
            "fps": 30,
            "attribution_line": ATTRIBUTION,
            "license": LICENSE,
            "license_url": LICENSE_URL,
            "changes": "Excerpt re-framed to 9:16 with English captions. Speech was not replaced.",
        },
        assets=[],
    )
    _fit_duration(plan, duration)
    plan["limitations"].append(
        "Commons stills of a one-mass pulley and a historical Atwood apparatus were downloaded and rejected. "
        "Neither picture matches the spoken force sum, so the teacher stays visible."
    )
    errors = validate_plan(plan)
    if errors:
        for error in errors:
            print(error)
        return 1
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    write_composition(plan, defaults, PROJECT / "index.html")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _dump(PROJECT / "editing_plan.json", plan)
    _dump(OUTPUT / "editing_plan.json", plan)
    _dump(OUTPUT / "transcript.json", {"language": transcript["language"], "language_source": transcript["language_source"], "method": transcript["method"], "model": transcript["model"], "provenance": transcript["provenance"], "segments": [segment for segment in transcript["segments"] if segment["id"] in set(selection["segment_ids"])]})
    _dump(
        OUTPUT / "captions.json",
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
    _write_review(plan, selection, teacher_probe, narration_probe)
    print(f"segment {selection['source_in']}..{selection['source_out']} narration {duration}s")
    print(f"composition {PROJECT / 'index.html'}")
    return 0


def _write_source_metadata(digest: str, probe: dict, selection: dict) -> None:
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    payload = {
        "page_url": PAGE,
        "direct_media_url": MEDIA,
        "creator": "Walter Lewin",
        "institution": "MIT OpenCourseWare, published on Wikimedia Commons",
        "title": "Prof. Walter Lewin explains Atwood's machine",
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "commons_license_template": "{{cc-by-3.0}}",
        "permission_ticket": "2011051010013473",
        "attribution_required": True,
        "attribution": ATTRIBUTION,
        "embedded_vorbis_license_tag": "CC BY-NC-SA 3.0 US",
        "license_note": (
            "The Commons page and the file's wikitext grant CC BY 3.0 and cite VRT ticket "
            "2011051010013473. The Ogg Vorbis comment still says CC BY-NC-SA 3.0 US, which is "
            "the tag from the original encode. The VRT email was not opened. This project uses "
            "the Commons CC BY 3.0 grant, keeps the embedded tag in this record, and attributes "
            "Walter Lewin and MIT OpenCourseWare. The file is a published lecture, not an unedited camera master."
        ),
        "ocw_course_page": "https://ocw.mit.edu/courses/physics/8-01-physics-i-classical-mechanics-fall-1999/video-lectures/",
        "parent_archive_tag": "http://www.archive.org/details/MIT8.01F99",
        "retrieval_date": "2026-10-22",
        "sha256": digest,
        "bytes": ORIGINAL.stat().st_size,
        "duration_seconds": float(probe["format"]["duration"]),
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("r_frame_rate"),
        },
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
            "stream_index": audio.get("index"),
        },
        "selected_segment": {
            "source_in": selection["source_in"],
            "source_out": selection["source_out"],
            "reason": selection["reason"],
        },
        "why_selected": (
            "A real lecturer, a direct Commons download, and a CC BY 3.0 grant that allows "
            "an adapted excerpt with attribution. The speech is a physics explanation."
        ),
    }
    (SOURCE_DIR / "source_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_source_readme() -> None:
    (SOURCE_DIR / "README.md").write_text(
        """# Educator source

Walter Lewin's explanation of Atwood's machine, downloaded from Wikimedia Commons.

- Page: https://commons.wikimedia.org/wiki/File:Prof_Walter_Lewin_Atwoods_Machine.ogv
- File: `Prof_Walter_Lewin_Atwoods_Machine.ogv`
- License used: CC BY 3.0 (https://creativecommons.org/licenses/by/3.0)
- Commons template: `{{cc-by-3.0}}`, VRT ticket 2011051010013473
- Attribution: Walter Lewin / MIT OpenCourseWare. Say that the short is an adapted excerpt.

The Ogg comment still says CC BY-NC-SA 3.0 US. That tag is recorded in `source_metadata.json` and was not erased. The controlling grant for this Commons file is the CC BY 3.0 statement on the file page. The VRT email itself was not read.

The recording includes camera changes between the lecturer and the blackboard. It is not an unedited camera master.

Do not commit the media file. Generated cuts live under the phase-2 composition, not as a second copy of this original.
""",
        encoding="utf-8",
    )


def _cut(start: float, end: float, teacher: Path, narration: Path) -> None:
    ffmpeg = _tool("ffmpeg")
    common = [ffmpeg, "-y", "-i", str(ORIGINAL), "-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
    subprocess.run(
        common + ["-map", "0:v:0", "-an", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", "-keyint_min", "30", "-movflags", "+faststart", str(teacher)],
        check=True,
    )
    subprocess.run(
        common + ["-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(narration)],
        check=True,
    )


def _fit_duration(plan: dict, duration: float) -> None:
    """Clip the plan to the narration file. The Ogg timestamps are damaged, so the cut can be slightly short."""
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
        if decision["end"] <= decision["start"]:
            continue
        decisions.append(decision)
    plan["decisions"] = decisions


def _write_review(plan: dict, selection: dict, teacher_probe: dict, narration_probe: dict) -> None:
    lines = [
        "# Phase 2 review",
        "",
        "Review is optional. This file was written after the first draft, not before it.",
        "",
        "## Source",
        "",
        f"- File: `{ORIGINAL}`",
        f"- Page: {PAGE}",
        f"- License: {LICENSE} ({LICENSE_URL})",
        f"- Interval: {selection['source_in']} s to {selection['source_out']} s",
        f"- Why this interval: {selection['reason']}",
        "",
        "The interval is the force balance on the pulley and the no-slip rope condition. "
        "Frames around 120 s and 160 s show the lecturer and a blackboard already labeled Atwood's machine.",
        "",
        "## Transcript and English captions",
        "",
        "Speech is English. `language_source` is `configured` because whisper.cpp was run with `-l en`. "
        "The English caption is the same string as the original text. It was not rewritten.",
        "",
    ]
    for segment in plan["input_media"]["transcript"]["segments"]:
        lines.append(
            f"- {segment['start']:.2f}–{segment['end']:.2f} ({segment['timing_precision']}): {segment['original_text']}"
        )
    lines.extend(
        [
            "",
            "## Visual decisions",
            "",
            "One `show_teacher` interval covers the whole short. The title is `FORCES ON THE PULLEY`, from the sentence about the sum of forces.",
            "",
            "Style evidence is the completed bounded-card family, exemplar SHORT_03. "
            "SHORT_30, SHORT_32, and SHORT_37 were not used.",
            "",
            "## B-roll",
            "",
            "No still was used.",
            "",
            "- https://commons.wikimedia.org/wiki/File:Pulley_force_diagram.jpg — public domain, Andrepaoliello. "
            "Rejected after viewing: it is one hanging mass and a 1.0 m distance, not the two tensions in the speech.",
            "- https://commons.wikimedia.org/wiki/File:Atwoods_machine.png — public domain. "
            "Rejected after viewing: it is a historical fall-machine engraving, and the word Atwood is not in this interval's transcript.",
            "- https://commons.wikimedia.org/wiki/File:Atwood.png — CC BY-SA 3.0. Not used. The download was a 1 KB preview, not a usable diagram.",
            "",
            "## Render inputs",
            "",
            f"- Teacher cut: {teacher_probe['streams'][0]['width']}x{teacher_probe['streams'][0]['height']} "
            f"{teacher_probe['streams'][0]['codec_name']} {teacher_probe['format']['duration']} s",
            f"- Narration: {narration_probe['streams'][0]['codec_name']} {narration_probe['streams'][0]['sample_rate']} Hz "
            f"{narration_probe['streams'][0]['channels']} ch {narration_probe['format']['duration']} s",
            "",
            "Validation of the rendered MP4 is appended by `validate_phase2.py` after the render.",
            "",
        ]
    )
    (OUTPUT / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8")


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


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
