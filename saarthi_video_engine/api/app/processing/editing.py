"""Manual review of a plan. Every accepted change becomes a new, validated version."""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.errors import StudioError
from app.processing.plan_adapt import (
    BACK_TO_BACK_MIN,
    MIN_CUTAWAY,
    build_engine_plan,
    engine_broll_cap,
    public_plan,
    reconcile,
    validate_envelope,
)
from app.processing.director import find_phrase, phrase_at
from app.processing.intervals import fit_cutaway
from app.processing.settings import merge
from app.processing.titles import clean_title, title_record
from app.processing.versions import load_version, save_version
from app.storage import StudioStore

TOLERANCE = 0.05
RENDER_ACTIVE = {"render_queued", "preparing", "composing", "rendering", "verifying", "assembling_outro"}


def _number(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "invalid_edit", f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise StudioError(422, "invalid_edit", f"{label} must be a number.")
    return round(number, 3)


def _library(store: StudioStore, record: dict, current: dict, work: Path) -> dict[str, dict]:
    """Clips this session may use. Mode B: its own uploads. Mode A: its approved selector files."""
    previous = {item["asset_id"]: item for item in (current["studio"].get("placements") or []) + (current["studio"].get("rejected_placements") or [])}
    library: dict[str, dict] = {}
    if record["mode"] == "my_broll":
        owned = {item["upload_id"] for item in record.get("broll") or []}
        for item in current["studio"].get("catalogue") or []:
            if item["asset_id"] in owned:
                library[item["asset_id"]] = {
                    "filename": item.get("filename"),
                    "description": item.get("description"),
                    "clip_duration": float(item.get("duration_seconds") or 0.0),
                    "visual_summary": (item.get("visual_analysis") or {}).get("summary") if isinstance(item.get("visual_analysis"), dict) else "",
                    "origin": "user",
                }
        return library
    index_path = work / "footage_index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8")).get("assets") or [] if index_path.is_file() else []
    for entry in entries:
        asset_id = entry["asset_id"]
        known = previous.get(asset_id) or {}
        duration = known.get("clip_duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            from app.media_probe import probe_video

            path = (work / entry["relative_path"]).resolve()
            if work.resolve() not in path.parents or not path.is_file():
                continue
            duration = probe_video(path)["duration_seconds"]
        library[asset_id] = {
            "filename": known.get("filename") or entry.get("filename") or "Commons clip",
            "description": known.get("description") or entry.get("description") or "",
            "clip_duration": float(duration),
            "license": known.get("license") or entry.get("license") or "",
            "license_url": entry.get("license_url") or "",
            "page_url": known.get("page_url") or entry.get("page_url") or "",
            "artist": entry.get("artist") or "",
            "origin": "commons",
        }
    return library


def _assets(mode: str, placements: list[dict], library: dict[str, dict]) -> list[dict]:
    rows = []
    for placement in placements:
        info = library[placement["asset_id"]]
        item = {
            "id": placement["asset_id"],
            "path": f"studio-assets/{placement['asset_id']}/media",
            "kind": "video",
            "window": [placement["speech_start"], placement["speech_end"]],
            "duration_seconds": round(placement["speech_end"] - placement["speech_start"], 3),
            "reason": placement["explanation"],
            "confidence": min(1.0, max(0.0, float(placement.get("score") or 0.5))),
            "origin": info["origin"],
        }
        if mode == "auto_edit":
            for key in ("license", "license_url", "page_url", "artist"):
                if info.get(key):
                    item[key] = info[key]
        rows.append(item)
    return rows


def apply_edits(store: StudioStore, session_id: str, owner: str, body: dict, lock) -> dict:
    with lock:
        record = store.get_session(session_id, owner)
        if record.get("status") != "plan_ready":
            raise StudioError(409, "plan_not_ready", "Edit the plan after processing has finished.")
        if (record.get("render") or {}).get("status") in RENDER_ACTIVE:
            raise StudioError(409, "render_in_progress", "Wait for the current render to finish before changing the plan.")
        base = str(body.get("base_version_id") or "")
        if not base or base != record.get("plan_version"):
            raise StudioError(409, "stale_plan", "The plan changed since you opened it. Reload it and try again.")
        work = store.work_dir(session_id)
        current = load_version(work, base)
        transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
        duration = float(current["engine_plan"]["input_media"]["duration_seconds"])
        library = _library(store, record, current, work)
        previous = {item["asset_id"]: item for item in (current["studio"].get("placements") or []) + (current["studio"].get("rejected_placements") or [])}

        requested = body.get("placements")
        if not isinstance(requested, list):
            raise StudioError(422, "invalid_edit", "Send the full list of placements.")
        approved: list[dict] = []
        rejected: list[dict] = []
        seen: set[str] = set()
        for raw in requested:
            if not isinstance(raw, dict):
                raise StudioError(422, "invalid_edit", "Each placement must be an object.")
            asset_id = str(raw.get("asset_id") or "")
            if asset_id not in library:
                raise StudioError(422, "asset_not_in_session", "A placement uses a clip that does not belong to this session.")
            if asset_id in seen:
                raise StudioError(422, "duplicate_asset", "Each clip can be placed once.")
            seen.add(asset_id)
            info = library[asset_id]
            approval = raw.get("approval")
            if approval not in {"approved", "rejected"}:
                raise StudioError(422, "invalid_edit", "Approval must be approved or rejected.")
            start = _number(raw.get("speech_start"), "Timeline start")
            end = _number(raw.get("speech_end"), "Timeline end")
            source_in = _number(raw.get("source_in"), "Source in")
            source_out = _number(raw.get("source_out"), "Source out")
            override = None
            if raw.get("target_phrase"):
                target = str(raw["target_phrase"])[:200]
                found = find_phrase(transcript, target)
                if found is None:
                    raise StudioError(422, "phrase_not_found", f"{info['filename']}: “{target}” was not found in the transcript.")
                length = _number(raw["duration_seconds"], "Duration") if raw.get("duration_seconds") is not None else round(end - start, 3)
                start = found["start"]
                end = round(min(start + length, duration), 3)
                source_out = round(min(source_in + (end - start), info["clip_duration"]), 3)
                if source_out - source_in < end - start - 1e-3:
                    source_in = round(max(0.0, source_out - (end - start)), 3)
                override = {"target_phrase": target, "matched_phrase": found["phrase"], "by": "user", "duration_seconds": round(end - start, 3)}
            old = previous.get(asset_id) or {}
            unchanged = bool(old) and all(abs(float(old.get(key) or 0) - value) < 1e-3 for key, value in (("speech_start", start), ("speech_end", end), ("source_in", source_in), ("source_out", source_out)))
            row = dict(old) if unchanged else {
                "asset_id": asset_id,
                "segment_ids": _segments(transcript, start, end),
                "speech_text": _speech(transcript, start, end),
                "description_relevance": None,
                "visual_relevance": None,
                "score": None,
                "confidence": None,
                "evidence": "manual",
                "evidence_sources": ["user_review"],
                "explanation": "Placed by the user in the Studio review." + (f" Earlier evidence: {old['explanation']}" if old.get("explanation") else ""),
            }
            row.update(
                {
                    "filename": info["filename"],
                    "description": info["description"],
                    "visual_summary": info.get("visual_summary") or old.get("visual_summary") or "",
                    "speech_start": start,
                    "speech_end": end,
                    "source_in": source_in,
                    "source_out": source_out,
                    "clip_duration": info["clip_duration"],
                    "duration_seconds": info["clip_duration"],
                    "origin": info["origin"],
                    "approval": approval,
                }
            )
            if record["mode"] == "auto_edit":
                row["license"] = info.get("license") or ""
                row["page_url"] = info.get("page_url") or ""
            if approval != old.get("approval") or not unchanged:
                row["approved_by"] = "user"
            if not unchanged:
                spoken = phrase_at(transcript, start)
                if spoken:
                    row.update({"matched_phrase": spoken["phrase"], "phrase_start": spoken["phrase_start"], "phrase_end": spoken["phrase_end"], "segment_ids": spoken["segment_ids"]})
                row["placement_rule"] = "user"
            if override:
                row["user_override"] = override
                row["explanation"] = f"Placed by the user on “{override['matched_phrase']}”." + (f" Earlier evidence: {old['explanation']}" if old.get("explanation") else "")
            row.pop("reason", None)
            row.pop("trimmed", None)
            if approval == "rejected":
                row["reason"] = "Rejected in the Studio review; the teacher stays on screen."
                rejected.append(row)
                continue
            fitted = fit_cutaway(start, end, source_in, info["clip_duration"] or None, BACK_TO_BACK_MIN, keep_source=True)
            if not fitted["ok"]:
                raise StudioError(422, "invalid_source", f"{info['filename']}: {fitted['reason']}")
            if fitted["adjusted"]:
                end, source_in, source_out = fitted["speech_end"], fitted["source_in"], fitted["source_out"]
                row.update({"speech_end": end, "source_in": source_in, "source_out": source_out, "adjusted": fitted["adjusted"]})
            else:
                row.pop("adjusted", None)
            if start < 0 or end > duration + 1e-3 or end - start < BACK_TO_BACK_MIN - 1e-6:
                raise StudioError(422, "invalid_timeline", f"{info['filename']}: the cutaway must last at least {MIN_CUTAWAY:.0f} s inside the lesson (0–{duration:.2f} s).")
            if source_in < 0 or source_out > info["clip_duration"] + 1e-3 or source_out <= source_in:
                raise StudioError(422, "invalid_source", f"{info['filename']}: the source interval must lie inside the clip (0–{info['clip_duration']:.2f} s).")
            if abs((source_out - source_in) - (end - start)) > TOLERANCE:
                raise StudioError(422, "invalid_source", f"{info['filename']}: a full-screen cutaway plays in real time, so the source interval must be as long as its time on screen.")
            approved.append(row)
        approved.sort(key=lambda item: item["speech_start"])
        for left, right in zip(approved, approved[1:]):
            if right["speech_start"] < left["speech_end"] - 1e-3:
                raise StudioError(422, "overlapping_cutaways", f"{left['filename']} and {right['filename']} overlap on the timeline.")
        for index, item in enumerate(approved):
            if item["speech_end"] - item["speech_start"] >= MIN_CUTAWAY - 1e-6:
                continue
            following = approved[index + 1] if index + 1 < len(approved) else None
            if following is None or following["speech_start"] - item["speech_end"] > 0.05:
                raise StudioError(422, "invalid_timeline", f"{item['filename']}: a cutaway shorter than {MIN_CUTAWAY:.0f} s must run straight into the next cutaway.")
        covered = sum(item["speech_end"] - item["speech_start"] for item in approved)
        cap = engine_broll_cap()
        if covered > cap * duration + 1e-3:
            raise StudioError(422, "broll_cap", f"Cutaways would cover {covered:.1f} s, over the {cap:.0%} limit of {cap * duration:.1f} s.")

        title = dict(current.get("title") or title_record("", "none"))
        if "title" in body and not (body.get("origin") == "undo" and (body.get("title") or "") == title.get("text", "")):
            text = clean_title(body.get("title"))
            title = title_record(text, "user") | {"suggestion": (current.get("title") or {}).get("suggestion")}
        settings = None if body.get("clear_settings") else current.get("settings")
        if body.get("settings") is not None or settings is not None:
            settings = merge(settings, body.get("settings"))
            if settings["music"]["source"] == "upload":
                music = store.get_upload(settings["music"]["upload_id"], owner)
                if music.get("kind") != "music" or music.get("validation_status") != "valid":
                    raise StudioError(422, "invalid_settings", "The music upload is not a validated audio file.")
            if settings["music"]["source"] == "library":
                from app.services.music import library_track

                library_track(settings["music"]["track_id"])
        max_words = settings["captions"]["max_words"] if settings else 3
        engine_plan = build_engine_plan(transcript, _assets(record["mode"], approved, library), duration, title["text"], max_words)
        kept, dropped = reconcile(approved, engine_plan)
        if dropped or any(item.get("trimmed") for item in kept):
            raise StudioError(422, "builder_changed_plan", "The plan builder could not keep every cutaway as requested. Shorten or move them.")
        catalogue = current["studio"].get("catalogue") or []
        validation = validate_envelope(record, engine_plan, kept, catalogue)
        if not validation["ok"]:
            raise StudioError(422, "plan_invalid", "; ".join(validation["errors"][:6]))
        envelope = public_plan(record, engine_plan, kept, validation, catalogue, current["studio"].get("evaluations") or [], rejected)
        for key in ("visual_analysis",):
            if key in current["studio"]:
                envelope[key] = current["studio"][key]
        if body.get("assistant_operations"):
            envelope["assistant_operations"] = body["assistant_operations"]
        version = save_version(
            work,
            session=record,
            engine_plan=engine_plan,
            studio=envelope,
            origin=body.get("origin") if body.get("origin") in {"assistant", "undo"} else "manual_edit",
            parent_version_id=base,
            source_hashes=current.get("source_hashes") or {},
            matching_config=current.get("matching_config") or {},
            title=title,
            make_current=True,
            settings=settings,
        )
        from app.processing.jobs import _make_current

        _make_current(record, version, transcript)
        record["note"] = "The editing plan was changed in review and validated again."
        store.write_session(record)
        return version["studio"]


def _segments(transcript: dict, start: float, end: float) -> list[str]:
    return [str(item.get("id")) for item in transcript.get("segments") or [] if float(item["start"]) < end and float(item["end"]) > start]


def _speech(transcript: dict, start: float, end: float) -> str:
    return " ".join(
        (item.get("original_text") or item.get("text") or "").strip()
        for item in transcript.get("segments") or []
        if float(item["start"]) < end and float(item["end"]) > start
    ).strip()
