"""Transcribe with ElevenLabs when a local key is set, and write a lesson analysis.

The default command refuses to run when ELEVENLABS_API_KEY is empty.
`--use-existing-transcript` reads the Phase 6 transcript and editing plan.
It does not call ElevenLabs and it does not claim a new render.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from analysis.lesson import (  # noqa: E402
    acquire_broll,
    build_lesson_analysis,
    mark_approved,
    mark_downloaded,
    mark_rendered_from_plan,
    quality_warnings,
    record_candidate,
    validate_analysis,
)
from assets.concepts import denies, visual_queries  # noqa: E402
from assets.video_commons import accept_video_candidate, download_video, search_videos  # noqa: E402
from config.env import ConfigError, load_project_env, require_elevenlabs_key  # noqa: E402
import run_phase4 as phase4  # noqa: E402
import run_phase5 as phase5  # noqa: E402
from transcription.captions import prepare_segment  # noqa: E402
from transcription.elevenlabs import REVIEW_LOGPROB, ElevenLabsError, compare_transcripts, transcribe_file  # noqa: E402

PHASE6 = ROOT / "outputs" / "dr_shivin_phase6"
OUT = ROOT / "outputs" / "dr_shivin_phase7"
RAW = ROOT.parent / "DR_SHIVIN_RAW"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    load_project_env()
    if "--use-existing-transcript" in args:
        return analyze_existing()
    if "--assets" in args:
        return collect_assets()
    if "--render" in args:
        return render_lessons()
    try:
        api_key = require_elevenlabs_key()
    except ConfigError as error:
        print(error)
        return 2
    if "--transcribe-only" in args:
        return transcribe_lessons(api_key, search=False)
    return transcribe_lessons(api_key)


def transcribe_lessons(api_key: str, search: bool = True) -> int:
    """Send each raw lesson to Scribe and search licensed B-roll. Does not render."""
    OUT.mkdir(parents=True, exist_ok=True)
    language = (os.environ.get("ELEVENLABS_LANGUAGE_CODE") or "").strip()
    model = (os.environ.get("ELEVENLABS_STT_MODEL") or "").strip() or "scribe_v2"
    for video_id in ("VIDEO_1", "VIDEO_2"):
        source = RAW / f"{video_id}.mp4"
        if not source.is_file():
            print(f"{video_id} was not found at {source}")
            return 1
        work = OUT / video_id / "work"
        work.mkdir(parents=True, exist_ok=True)
        audio = work / "speech_16k.wav"
        if not audio.is_file():
            print(f"extracting original audio for {video_id}")
            phase4._extract_audio(source, audio)
        print(f"transcribing {video_id} with {model}")
        try:
            transcript = transcribe_file(
                audio,
                api_key=api_key,
                model_id=model,
                language_code=language,
                cache_dir=OUT / "transcript_cache",
                raw_path=OUT / video_id / "elevenlabs_response.json",
            )
        except ElevenLabsError as error:
            print(video_id, "transcription failed:", error)
            return 1
        transcript["source_media"] = source.name
        transcript["audio_extract"] = audio.name
        media = _probe(source)
        analysis = build_lesson_analysis(
            video_id=video_id,
            source={
                "path": str(source),
                "duration_seconds": media.get("duration_seconds"),
                "width": media.get("width"),
                "height": media.get("height"),
            },
            transcript=transcript,
            visual_observation="not observed in this pass",
        )
        if search:
            acquire_broll(
                analysis,
                OUT / video_id / "assets",
                search_videos,
                download_video,
                accept_video_candidate,
            )
        errors = validate_analysis(analysis)
        if errors:
            print(video_id)
            print("\n".join(errors))
            return 1
        destination = OUT / video_id / "lesson_analysis.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        transcript_path = OUT / video_id / "transcript.json"
        transcript_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        (work / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        _write_caption_review(video_id, transcript)
        _write_comparison(video_id, transcript)
        coverage = analysis["coverage"]
        print(video_id, "planned", f"{coverage['planned_seconds']}s", "rendered", "0 until a render attaches the files")
        for warning in quality_warnings(analysis):
            print("warning:", warning)
    print("Transcripts and analysis are in outputs/dr_shivin_phase7.")
    print("This command does not render. python run_phase6.py still builds the current picture.")
    return 0


def collect_assets() -> int:
    """Search Commons for concrete spoken subjects and download files that pass the license check."""
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.CACHE.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    downloads = 0
    for video_id in ("VIDEO_1", "VIDEO_2"):
        path = OUT / video_id / "work" / "transcript.json"
        if not path.is_file():
            path = OUT / video_id / "transcript.json"
        transcript = json.loads(path.read_text(encoding="utf-8"))
        if transcript.get("method") != "elevenlabs":
            print(f"{video_id} transcript is not an ElevenLabs transcript.")
            return 1
        _write_caption_review(video_id, transcript)
        speech = phase5._speech_text(transcript)
        seen: set[str] = set()
        for item in visual_queries(speech):
            if item["action"] == "skip":
                rows.append(
                    {
                        "video_id": video_id,
                        "kind": "video",
                        "term": item["term"],
                        "rejected": item["reason"],
                    }
                )
                continue
            query = item["query"]
            if query in seen:
                continue
            seen.add(query)
            term = item["term"]
            denial = denies(speech, term)
            try:
                hits = search_videos(query, limit=5)
                time.sleep(1.2)
            except Exception as error:  # noqa: BLE001
                rows.append(
                    {
                        "video_id": video_id,
                        "query": query,
                        "kind": "video",
                        "rejected": f"search failed: {type(error).__name__}",
                    }
                )
                continue
            accepted_one = False
            for hit in hits:
                haystack = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
                reason = accept_video_candidate(hit, query, speech=speech)
                if reason is None and denial and any(word in haystack for word in ("reading", "reads", "reader")):
                    reason = "clip depicts the denied action of reading"
                if reason:
                    rows.append(
                        {
                            "video_id": video_id,
                            "query": query,
                            "kind": "video",
                            "title": hit.get("title"),
                            "page_url": hit.get("page_url"),
                            "license": hit.get("license"),
                            "rejected": reason,
                        }
                    )
                    continue
                if downloads >= 5:
                    rows.append(
                        {
                            "video_id": video_id,
                            "query": query,
                            "kind": "video",
                            "title": hit.get("title"),
                            "page_url": hit.get("page_url"),
                            "rejected": "download cap reached before this file",
                        }
                    )
                    continue
                suffix = Path(urllib.parse.urlparse(hit["download_url"]).path).suffix.lower() or ".mp4"
                dest = phase5.CACHE / f"{phase4._slug(hit.get('title') or 'clip')}{suffix}"
                if not dest.is_file():
                    try:
                        download_video(hit["download_url"], dest)
                    except Exception as error:  # noqa: BLE001
                        rows.append(
                            {
                                "video_id": video_id,
                                "query": query,
                                "title": hit.get("title"),
                                "page_url": hit.get("page_url"),
                                "rejected": f"download failed: {type(error).__name__}",
                            }
                        )
                        time.sleep(2)
                        continue
                    time.sleep(2)
                downloads += 1
                record = dict(hit)
                note = f"Commons video matched the spoken word {term}."
                if denial:
                    note += " The spoken line denies this subject, so a negation mark is drawn. negation mark"
                record.update(
                    {
                        "video_id": video_id,
                        "query": query,
                        "match_terms": [term],
                        "kind": "video",
                        "local_path": str(dest),
                        "rejected": None,
                        "reason": note,
                    }
                )
                phase4._describe_media(record, dest)
                rows.append(record)
                accepted_one = record.get("rejected") is None
                if accepted_one:
                    break
            if not accepted_one:
                rows.append(
                    {
                        "video_id": video_id,
                        "query": query,
                        "kind": "video",
                        "rejected": "no licensed video matched this spoken query",
                    }
                )
    rows.extend(phase5._music_record())
    (phase5.CACHE / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [phase5.MUSIC_PAGE]
    pages.extend(
        row["page_url"]
        for row in rows
        if row.get("kind") == "video" and row.get("page_url") and not row.get("rejected") and row.get("local_path")
    )
    (OUT / "accepted.json").write_text(
        json.dumps(
            {
                "page_urls": pages,
                "note": "A page is listed only after the file was saved and the license check passed.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    kept = [row for row in rows if row.get("kind") == "video" and not row.get("rejected") and row.get("local_path")]
    print("downloaded", len(kept))
    for row in kept:
        print(row.get("video_id"), row.get("query"), row.get("license"), row.get("title"))
    return 0


def render_lessons() -> int:
    """Build a fresh composition from the ElevenLabs transcript and render both lessons."""
    for video_id in ("VIDEO_1", "VIDEO_2"):
        path = OUT / video_id / "work" / "transcript.json"
        if not path.is_file():
            path = OUT / video_id / "transcript.json"
        if not path.is_file():
            print(f"{video_id} has no transcript. Run python run_phase7.py --transcribe-only first.")
            return 1
        transcript = json.loads(path.read_text(encoding="utf-8"))
        if transcript.get("method") != "elevenlabs":
            print(f"{video_id} transcript method is {transcript.get('method')}. Refusing to render it as ElevenLabs.")
            return 1
        work = OUT / video_id / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        _write_caption_review(video_id, transcript)
    observations = PHASE6 / "visual_observations.json"
    if not observations.is_file():
        print("Phase 6 visual observations are missing. The board headings were not re-watched.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "visual_observations.json").write_text(observations.read_text(encoding="utf-8"), encoding="utf-8")
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    if not (phase5.CACHE / "candidates.json").is_file():
        collect_assets()
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase5.FINAL_SUFFIX = "phase7"
    phase5.render()
    _refresh_analysis_after_render()
    return 0


def _write_caption_review(video_id: str, transcript: dict) -> None:
    language = transcript.get("language") or "und"
    rows = []
    for segment in transcript.get("segments") or []:
        prepared = prepare_segment(segment, language)
        words = segment.get("words") or []
        uncertain = [
            word.get("text")
            for word in words
            if isinstance(word.get("logprob"), (int, float)) and word["logprob"] < REVIEW_LOGPROB
        ]
        notes = list(prepared.get("normalization_notes") or [])
        if uncertain:
            notes.append("words below the local logprob review mark: " + ", ".join(uncertain))
        if "repeated_recognition" in prepared["flags"]:
            notes.append("repeated or thin recognition withheld from the caption")
        rows.append(
            {
                "start": segment.get("start"),
                "end": segment.get("end"),
                "recognizer_output": prepared["original_text"],
                "romanized_text": prepared["romanized_text"],
                "caption": prepared["caption_text"],
                "confidence": None,
                "logprob_review_words": uncertain,
                "flags": prepared["flags"] + list(segment.get("review_flags") or []),
                "correction": "withheld" if not prepared["caption_text"] else ("automatic" if notes else "unchanged"),
                "verification": "scribe_v2_word_timestamps",
                "reason": "; ".join(notes) if notes else "shown as recognized, transliterated when the script is Devanagari",
            }
        )
    destination = OUT / video_id / "caption_review.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _write_comparison(video_id: str, transcript: dict) -> None:
    previous_path = PHASE6 / video_id / "work" / "transcript.json"
    if not previous_path.is_file():
        return
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    report = compare_transcripts(previous, transcript)
    report["previous_method"] = previous.get("method")
    report["current_method"] = transcript.get("method")
    report["current_model"] = transcript.get("model")
    (OUT / video_id / "transcript_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def _refresh_analysis_after_render() -> None:
    for video_id in ("VIDEO_1", "VIDEO_2"):
        plan_path = OUT / video_id / "editing_plan.json"
        analysis_path = OUT / video_id / "lesson_analysis.json"
        outro_path = OUT / video_id / "outro.json"
        if not plan_path.is_file() or not analysis_path.is_file():
            continue
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if outro_path.is_file():
            outro = json.loads(outro_path.read_text(encoding="utf-8"))
            analysis["coverage"]["lesson_seconds"] = outro.get("lesson_plan_duration")
        mark_rendered_from_plan(analysis, plan)
        analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        coverage = analysis["coverage"]
        print(video_id, "rendered coverage", f"{coverage['rendered_fraction']:.1%}", coverage.get("shortfall_reason") or "")


def analyze_existing() -> int:
    if not PHASE6.is_dir():
        print("Phase 6 outputs are missing, so the existing transcript cannot be analysed.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = json.loads((PHASE6 / video_id / "work" / "transcript.json").read_text(encoding="utf-8"))
        plan = json.loads((PHASE6 / video_id / "editing_plan.json").read_text(encoding="utf-8"))
        outro = json.loads((PHASE6 / video_id / "outro.json").read_text(encoding="utf-8"))
        analysis = build_lesson_analysis(
            video_id=video_id,
            source={
                "path": str(RAW / f"{video_id}.mp4"),
                "duration_seconds": outro.get("lesson_plan_duration") or outro.get("lesson_duration"),
                "width": plan["input_media"].get("width"),
                "height": plan["input_media"].get("height"),
            },
            transcript=transcript,
            visual_observation="Taken from the Phase 6 plan. This pass did not watch a new frame.",
        )
        _apply_rendered_assets(analysis, plan)
        mark_rendered_from_plan(analysis, plan)
        errors = validate_analysis(analysis)
        if errors:
            print(video_id)
            print("\n".join(errors))
            return 1
        destination = OUT / video_id / "lesson_analysis.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        coverage = analysis["coverage"]
        print(
            video_id,
            "rendered",
            f"{coverage['rendered_fraction']:.1%}",
            "of",
            coverage["lesson_seconds"],
            "s",
        )
        for warning in quality_warnings(analysis):
            print("warning:", warning)
        if coverage.get("shortfall_reason"):
            print("shortfall:", coverage["shortfall_reason"])
    print("analysis written under outputs/dr_shivin_phase7")
    print("provider: existing transcript, not a new ElevenLabs call")
    return 0


def _probe(source: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"duration_seconds": None, "width": None, "height": None}
    stream = (payload.get("streams") or [{}])[0]
    duration = (payload.get("format") or {}).get("duration")
    return {
        "duration_seconds": round(float(duration), 3) if duration else None,
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def _apply_rendered_assets(analysis: dict, plan: dict) -> None:
    for decision in plan.get("decisions") or []:
        if decision.get("action") != "replace_with_broll" or not decision.get("asset_path"):
            continue
        for segment in analysis["timeline"]:
            slot = segment["broll"]
            if not slot["slot_planned"]:
                continue
            if float(decision["end"]) <= float(slot["proposed_start"]) or float(decision["start"]) >= float(slot["proposed_end"]):
                continue
            local_path = decision["asset_path"]
            record_candidate(
                slot,
                {
                    "title": decision.get("creator") or decision.get("asset_id"),
                    "page_url": decision.get("page_url"),
                    "license": decision.get("license"),
                    "license_url": decision.get("license_url"),
                    "local_path": local_path,
                    "status": "found",
                },
            )
            mark_downloaded(slot, local_path)
            mark_approved(slot, local_path)


if __name__ == "__main__":
    raise SystemExit(main())
