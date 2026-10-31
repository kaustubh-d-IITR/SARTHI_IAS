"""Schema validation plus timeline checks the schema cannot express."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

EXCLUSIVE_ACTIONS = {"show_teacher", "replace_with_broll"}
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "editing_plan.schema.json"


def _schema():
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_plan(plan: dict) -> list[str]:
    errors = []
    validator = Draft202012Validator(_schema())
    for error in sorted(validator.iter_errors(plan), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "(root)"
        errors.append(f"{location}: {error.message}")
    if errors:
        return errors
    return _timeline_errors(plan)


def _timeline_errors(plan: dict) -> list[str]:
    errors = []
    duration = plan["input_media"]["duration_seconds"]
    segments = plan["input_media"]["transcript"]["segments"]
    for segment in segments:
        if segment["end"] <= segment["start"]:
            errors.append(f"{segment['id']}: transcript end must be after start")
        if segment["end"] > duration + 1e-6:
            errors.append(f"{segment['id']}: transcript end {segment['end']} is past {duration}")
    exclusive = []
    for decision in plan["decisions"]:
        if decision["end"] <= decision["start"]:
            errors.append(f"{decision['id']}: end must be after start")
        if decision["end"] > duration + 1e-6 or decision["start"] > duration + 1e-6:
            errors.append(f"{decision['id']}: timestamp is outside 0..{duration}")
        if decision["action"] in EXCLUSIVE_ACTIONS:
            exclusive.append(decision)
        if decision["action"] == "caption" and not decision.get("text"):
            errors.append(f"{decision['id']}: caption is missing text")
    exclusive.sort(key=lambda item: (item["start"], item["end"], item["id"]))
    for earlier, later in zip(exclusive, exclusive[1:]):
        if later["start"] < earlier["end"] - 1e-6:
            errors.append(
                f"{earlier['id']} overlaps {later['id']} "
                f"({earlier['start']}-{earlier['end']} vs {later['start']}-{later['end']})"
            )
    if plan["audio"]["preserve_original"] and not plan["audio"]["narration_path"]:
        errors.append("original narration path is empty")
    interval = plan["audio"].get("narration_interval")
    if interval:
        if abs(interval["start"]) > 1e-3:
            errors.append("narration interval must start at 0")
        if abs(interval["end"] - duration) > 0.05:
            errors.append("narration interval must cover the plan duration")
        if "source_out" in interval and "source_in" in interval:
            source_span = interval["source_out"] - interval["source_in"]
            if abs(source_span - (interval["end"] - interval["start"])) > 0.35:
                errors.append("narration source span does not match the plan duration")
    for segment in segments:
        original = segment.get("original_text")
        english = segment.get("english_text")
        if original is not None and english is not None and "english_text" in segment:
            if segment.get("language") == "en" and original != english and segment.get("language_source") == "provider":
                errors.append(f"{segment['id']}: English source text and English caption differ")
        if str(segment.get("language") or "").startswith("hi"):
            romanized = segment.get("romanized_text")
            if english and romanized and english != romanized and english != original:
                for decision in plan["decisions"]:
                    if decision["action"] == "caption" and decision.get("text") == english:
                        errors.append(f"{decision['id']}: Hindi caption is an English translation")
    music = plan["audio"]["music"]
    named = music.get("identity") is not None
    licensed = music.get("source") == "licensed" or music.get("identity_status") == "identified"
    if named or licensed:
        if not music.get("path") or not music.get("license") or not music.get("license_url"):
            errors.append("music identity must stay null until a track is actually identified")
        elif music.get("identity_status") != "identified":
            errors.append("music identity must stay null until a track is actually identified")
    return errors
