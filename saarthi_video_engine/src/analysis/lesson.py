"""Lesson analysis records. A planned B-roll slot is not a rendered clip.

The timeline uses the same field names as the reference visual notes
(spoken_wording, teacher_visibility, broll_or_asset) and adds an explicit
acquisition record so each stage can be told apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from assets.concepts import denies, visual_queries

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "lesson_analysis.schema.json"
TARGET_LOW = 0.30
TARGET_HIGH = 0.40


def build_lesson_analysis(
    *,
    video_id: str,
    source: dict,
    transcript: dict,
    visual_observation: str = "",
) -> dict:
    """Plan slots from the transcript. Nothing here is downloaded or rendered yet."""
    segments = []
    for segment in transcript.get("segments") or []:
        text = (segment.get("original_text") or segment.get("text") or "").strip()
        queries = visual_queries(text)
        searches = [item for item in queries if item["action"] == "search"]
        skips = [item for item in queries if item["action"] == "skip"]
        negation = any(denies(text, item["term"]) for item in searches)
        appropriate = bool(searches)
        if skips and not searches:
            reason = skips[0]["reason"]
        elif not searches:
            reason = "The sentence has no concrete depictable subject, so the teacher stays visible."
        elif negation:
            reason = (
                "The sentence names a concrete subject and also denies it. "
                "A related clip may show the object. It must not show someone doing the action the line denies."
            )
        else:
            reason = "The sentence names a concrete subject that can be shown without replacing board evidence."
        start = float(segment["start"])
        end = float(segment["end"])
        segments.append(
            {
                "id": segment.get("id") or f"a{len(segments)}",
                "start": start,
                "end": end,
                "spoken_wording": text,
                "words": segment.get("words") or [],
                "review_flags": list(segment.get("review_flags") or []),
                "teacher_visibility": "visible_primary",
                "visual_observation": visual_observation or "not observed in this pass",
                "visual_observation_confidence": "not_observed",
                "concept": searches[0]["term"] if searches else (skips[0]["term"] if skips else None),
                "broll": {
                    "appropriate": appropriate,
                    "reason": reason,
                    "queries": [item["query"] for item in searches],
                    "proposed_start": start,
                    "proposed_end": end,
                    "placement": "replace_with_broll" if appropriate else "show_teacher",
                    "transition": "hard_cut" if appropriate else "none",
                    "negation": negation,
                    "slot_planned": appropriate,
                    "candidates": [],
                    "downloaded": False,
                    "approved": False,
                    "rendered": False,
                    "failure_reason": None if appropriate else reason,
                },
            }
        )
    provider = transcript.get("method") or "unknown"
    return {
        "contract_version": "1.0.0",
        "video_id": video_id,
        "source": {
            "path": source.get("path") or "",
            "duration_seconds": source.get("duration_seconds"),
            "width": source.get("width"),
            "height": source.get("height"),
        },
        "transcription": {
            "provider": provider,
            "model": transcript.get("model"),
            "endpoint": transcript.get("endpoint"),
            "language": transcript.get("language"),
            "language_probability": transcript.get("language_probability"),
            "confidence_note": transcript.get("confidence_note")
            or "This transcript record does not include a provider confidence score.",
        },
        "timeline": segments,
        "broll_target": {"low": TARGET_LOW, "high": TARGET_HIGH, "basis": "main lesson duration, excluding the outro"},
        "coverage": {
            "planned_seconds": round(sum(_span(item) for item in segments if item["broll"]["slot_planned"]), 3),
            "rendered_seconds": 0.0,
            "lesson_seconds": source.get("duration_seconds"),
            "rendered_fraction": 0.0,
            "shortfall_reason": None,
        },
    }


def record_candidate(slot: dict, candidate: dict) -> None:
    """Append one search result. Downloaded and approved stay false until those steps happen."""
    slot["candidates"].append(
        {
            "title": candidate.get("title"),
            "page_url": candidate.get("page_url"),
            "license": candidate.get("license"),
            "license_url": candidate.get("license_url"),
            "local_path": candidate.get("local_path"),
            "status": candidate.get("status") or "found",
            "rejected": candidate.get("rejected"),
        }
    )


def mark_downloaded(slot: dict, local_path: str) -> None:
    slot["downloaded"] = True
    for candidate in slot["candidates"]:
        if candidate.get("local_path") == local_path:
            candidate["status"] = "downloaded"


def mark_approved(slot: dict, local_path: str) -> None:
    if not slot.get("downloaded"):
        raise ValueError("a clip cannot be approved before it is downloaded")
    slot["approved"] = True
    for candidate in slot["candidates"]:
        if candidate.get("local_path") == local_path:
            candidate["status"] = "approved"


def mark_rendered_from_plan(analysis: dict, plan: dict) -> None:
    """A slot is rendered only when the editing plan has a B-roll decision over that interval."""
    decisions = [
        item
        for item in plan.get("decisions") or []
        if item.get("action") == "replace_with_broll" and item.get("asset_path")
    ]
    rendered = 0.0
    for segment in analysis["timeline"]:
        slot = segment["broll"]
        if not slot["slot_planned"]:
            continue
        match = _overlap(slot, decisions)
        if match is None:
            slot["rendered"] = False
            if slot["approved"] and not slot.get("failure_reason"):
                slot["failure_reason"] = "An approved asset was not attached to the editing plan."
            elif not slot["approved"] and not slot.get("failure_reason"):
                slot["failure_reason"] = "No approved asset was available for this planned slot."
            continue
        slot["rendered"] = True
        slot["failure_reason"] = None
        rendered += max(0.0, float(match["end"]) - float(match["start"]))
    lesson = analysis["coverage"].get("lesson_seconds") or analysis["source"].get("duration_seconds") or 0
    analysis["coverage"]["rendered_seconds"] = round(rendered, 3)
    analysis["coverage"]["rendered_fraction"] = round(rendered / lesson, 4) if lesson else 0.0
    fraction = analysis["coverage"]["rendered_fraction"]
    if fraction < TARGET_LOW:
        analysis["coverage"]["shortfall_reason"] = _shortfall(analysis)
    else:
        analysis["coverage"]["shortfall_reason"] = None


def acquire_broll(analysis: dict, destination: Path, search, download, accept) -> None:
    """Search and download for planned slots. Rendered stays false until a plan attaches the file."""
    destination.mkdir(parents=True, exist_ok=True)
    for index, segment in enumerate(analysis["timeline"]):
        slot = segment["broll"]
        if not slot["slot_planned"] or slot["approved"]:
            continue
        speech = segment["spoken_wording"]
        for query in slot["queries"]:
            try:
                hits = search(query) or []
            except Exception as error:
                slot["failure_reason"] = f"Search failed: {type(error).__name__}"
                continue
            for hit in hits[:4]:
                reason = accept(hit, query, speech)
                local_name = _asset_name(index, hit.get("title") or "clip")
                record_candidate(
                    slot,
                    {
                        "title": hit.get("title"),
                        "page_url": hit.get("page_url"),
                        "license": hit.get("license"),
                        "license_url": hit.get("license_url"),
                        "local_path": str(destination / local_name) if reason is None else None,
                        "status": "rejected" if reason else "found",
                        "rejected": reason,
                    },
                )
                if reason:
                    continue
                local_path = destination / local_name
                try:
                    download(hit["download_url"], local_path)
                except Exception as error:
                    slot["failure_reason"] = f"Download failed: {type(error).__name__}"
                    continue
                if not local_path.is_file() or local_path.stat().st_size < 50_000:
                    slot["failure_reason"] = "Downloaded file was missing or too small."
                    continue
                mark_downloaded(slot, str(local_path))
                mark_approved(slot, str(local_path))
                slot["failure_reason"] = None
                break
            if slot["approved"]:
                break
        if slot["slot_planned"] and not slot["approved"] and not slot.get("failure_reason"):
            slot["failure_reason"] = "No licensed video matched this spoken query."


def quality_warnings(analysis: dict) -> list[str]:
    """Warn when a planned slot never reached the render. A low percentage alone is not a failure."""
    warnings = []
    planned = [item for item in analysis["timeline"] if item["broll"]["slot_planned"]]
    rendered = [item for item in planned if item["broll"]["rendered"]]
    if planned and not rendered:
        reasons = [item["broll"].get("failure_reason") or item["id"] for item in planned]
        warnings.append(
            "B-roll was planned but none of it was rendered. " + " ".join(str(reason) for reason in reasons)
        )
    for item in planned:
        slot = item["broll"]
        if slot["approved"] and not slot["rendered"]:
            warnings.append(f"{item['id']} was approved and was not attached to the render.")
    return warnings


def validate_analysis(analysis: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = []
    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(analysis), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "(root)"
        errors.append(f"{location}: {error.message}")
    return errors


def _overlap(slot: dict, decisions: list[dict]) -> dict | None:
    start = float(slot["proposed_start"])
    end = float(slot["proposed_end"])
    for decision in decisions:
        if float(decision["end"]) <= start or float(decision["start"]) >= end:
            continue
        return decision
    return None


def _asset_name(index: int, title: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in title)
    cleaned = "-".join(part for part in cleaned.split("-") if part)[:48]
    return f"{index:02d}-{cleaned or 'clip'}.mp4"


def _span(item: dict) -> float:
    return max(0.0, float(item["end"]) - float(item["start"]))


def _shortfall(analysis: dict) -> str:
    blocked = [
        item["broll"]["reason"]
        for item in analysis["timeline"]
        if not item["broll"]["slot_planned"]
    ]
    failed = [
        item["broll"]["failure_reason"]
        for item in analysis["timeline"]
        if item["broll"]["slot_planned"] and item["broll"].get("failure_reason")
    ]
    parts = []
    if blocked:
        parts.append("Teacher stays visible where the speech is personal, abstract, or already on the board.")
    if failed:
        parts.append("Unfilled slots: " + "; ".join(failed))
    if not parts:
        parts.append("Suitable licensed footage did not cover the 30-40% planning target.")
    return " ".join(parts)
