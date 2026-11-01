"""Map engine footage rows and user matches into one schema-valid plan."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import ENGINE_ROOT

_DATASET = None
# A cutaway that runs straight into the next one may be this short; on its own it needs MIN_CUTAWAY.
BACK_TO_BACK_MIN = 1.0
MIN_CUTAWAY = 2.0


def _engine():
    src = str(ENGINE_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _dataset() -> dict:
    global _DATASET
    if _DATASET is None:
        _engine()
        from dataset_adapter.loader import load_dataset

        _DATASET = load_dataset()
    return _DATASET


def assets_from_user(matches: list[dict]) -> list[dict]:
    rows = []
    for match in matches:
        rows.append(
            {
                "id": match["asset_id"],
                "path": f"studio-assets/{match['asset_id']}/media",
                "kind": "video",
                "window": [match["speech_start"], match["speech_end"]],
                "duration_seconds": match["speech_end"] - match["speech_start"],
                "reason": match["explanation"],
                "confidence": min(1.0, max(0.0, float(match["score"]))),
                "origin": "user",
            }
        )
    return rows


def engine_asset_id(row: dict, index: int) -> str:
    return str(row.get("sha256") or row.get("page_url") or f"engine-{index}")[:64]


def write_footage_index(work: Path, rows: list[dict]) -> None:
    """Private map from an approved asset id to a file already inside this session."""
    entries = []
    root = work.resolve()
    for index, row in enumerate(rows or []):
        if row.get("rejected"):
            continue
        local = row.get("local_path")
        if not local:
            continue
        path = Path(str(local)).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        entries.append(
            {
                "asset_id": engine_asset_id(row, index),
                "sha256": row.get("sha256") or "",
                "relative_path": relative,
                "filename": row.get("title") or "",
                "description": row.get("description") or "",
                "license": row.get("license") or "",
                "license_url": row.get("license_url") or "",
                "page_url": row.get("page_url") or "",
                "artist": row.get("artist") or "",
            }
        )
    (work / "footage_index.json").write_text(json.dumps({"assets": entries}, indent=2), encoding="utf-8")


def assets_from_engine(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Copy fields the selector actually returned. Do not fill in a licence or URL."""
    assets = []
    placements = []
    for index, row in enumerate(rows):
        if row.get("rejected"):
            continue
        window = row.get("source_window")
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            continue
        start, end = float(window[0]), float(window[1])
        if end - start < 2.0:
            continue
        asset_id = engine_asset_id(row, index)
        clip_in = float(row.get("clip_in") or 0.0)
        play = end - start
        placements.append(
            {
                "asset_id": asset_id,
                "filename": row.get("title") or "Commons clip",
                "description": row.get("description") or "",
                "segment_ids": [],
                "speech_text": "",
                "speech_start": round(start, 3),
                "speech_end": round(end, 3),
                "source_in": round(clip_in, 3),
                "source_out": round(clip_in + play, 3),
                "score": float(row.get("relevance") or 0.0),
                "explanation": row.get("reason") or "Returned by the existing footage selector.",
                "evidence": "existing_pipeline",
                "approval": "approved",
                "page_url": row.get("page_url") or "",
                "license": row.get("license") or "",
                "origin": "commons",
                "clip_duration": row.get("duration_seconds"),
            }
        )
        item = {
            "id": asset_id,
            "path": f"studio-assets/{asset_id}/media",
            "kind": "video",
            "window": [round(start, 3), round(end, 3)],
            "duration_seconds": round(play, 3),
            "reason": placements[-1]["explanation"],
            "confidence": min(1.0, max(0.0, float(row.get("relevance") or 0.0))),
            "origin": "commons",
        }
        if row.get("license"):
            item["license"] = row["license"]
        if row.get("license_url"):
            item["license_url"] = row["license_url"]
        if row.get("page_url"):
            item["page_url"] = row["page_url"]
        if row.get("artist"):
            item["artist"] = row["artist"]
        assets.append(item)
    return assets, placements


def engine_broll_cap() -> float:
    _engine()
    from editing_plan.phase2 import BROLL_CAP

    return float(BROLL_CAP)


def build_engine_plan(transcript: dict, assets: list[dict], duration: float, title: str = "", caption_max_words: int = 3) -> dict:
    _engine()
    from editing_plan.phase2 import build_phase2_plan
    from treatment.reference_look import lesson_grade

    from app.processing.titles import apply_title

    plan = build_phase2_plan(
        _dataset(),
        transcript=transcript,
        selection={"source_in": 0, "source_out": duration, "duration": duration, "text": "", "reason": "studio"},
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": 720,
            "height": 1280,
            "fps": 30,
            "attribution_line": "",
            "license": "not stated",
            "license_url": "not-stated-in-file",
            "changes": "Studio plan only. No render in this phase.",
        },
        assets=assets,
        grade=lesson_grade(duration),
        plan_id="studio-plan",
        primary_category="lesson",
        min_cutaway=BACK_TO_BACK_MIN,
        caption_max_words=caption_max_words,
    )
    plan["render"]["object_fit"] = "cover"
    plan["render"]["corner_logo"] = "assets/logo.png"
    apply_title(plan, title)
    return plan


def reconcile(placements: list[dict], engine_plan: dict) -> tuple[list[dict], list[dict]]:
    """Match placements to the cutaways the builder kept. Trimmed ones follow the builder; dropped ones are listed."""
    decisions = {item.get("asset_id"): item for item in engine_plan.get("decisions") or [] if item.get("action") == "replace_with_broll"}
    kept, dropped = [], []
    for placement in placements:
        if placement.get("approval") == "rejected":
            continue
        decision = decisions.get(placement["asset_id"])
        if decision is None:
            dropped.append(dict(placement, approval="rejected", reason="The plan builder dropped it (shorter than 2 s, overlapping, or over the 58% cap)."))
            continue
        row = dict(placement)
        length = round(float(decision["end"]) - float(decision["start"]), 3)
        if abs(float(row["speech_end"]) - float(decision["end"])) > 1e-3 or abs(float(row["speech_start"]) - float(decision["start"])) > 1e-3:
            row["speech_start"] = decision["start"]
            row["speech_end"] = decision["end"]
            row["source_out"] = round(float(row["source_in"]) + length, 3)
            row["trimmed"] = "Trimmed by the plan builder to respect the 58% B-roll cap or the clip length."
        kept.append(row)
    return kept, dropped


def picture_role(action: str) -> str:
    if action == "replace_with_broll":
        return "show_broll"
    if action == "show_teacher":
        return "show_teacher"
    return action


PLACEMENT_FIELDS = (
    "asset_id",
    "filename",
    "description",
    "visual_summary",
    "segment_ids",
    "speech_text",
    "speech_start",
    "speech_end",
    "source_in",
    "source_out",
    "description_relevance",
    "visual_relevance",
    "score",
    "confidence",
    "explanation",
    "evidence",
    "evidence_sources",
    "approval",
    "approved_by",
    "origin",
    "clip_duration",
    "license",
    "page_url",
    "trimmed",
    "reason",
    "matched_phrase",
    "phrase_start",
    "phrase_end",
    "scores",
    "occurrence",
    "placement_rule",
    "user_override",
    "adjusted",
)


def fit_placements(placements: list[dict], catalogue: list[dict], min_seconds: float = MIN_CUTAWAY) -> tuple[list[dict], list[dict]]:
    """Every approved placement inside its clip's real footage, or moved to the rejected list with a reason.

    Back-to-back cutaways (one ends where the next starts) may be as short as BACK_TO_BACK_MIN.
    """
    from app.processing.intervals import fit_cutaway, usable_seconds

    footage = {item["asset_id"]: usable_seconds(item) for item in catalogue}
    ordered = sorted(placements, key=lambda item: float(item.get("speech_start") or 0))
    kept, rejected = [], []
    for index, item in enumerate(ordered):
        clip = footage.get(item["asset_id"])
        if clip is None:
            clip = usable_seconds(item)
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        adjacent = following is not None and abs(float(following["speech_start"]) - float(item["speech_end"])) <= 0.05
        fitted = fit_cutaway(item["speech_start"], item["speech_end"], item.get("source_in") or 0.0, clip, BACK_TO_BACK_MIN if adjacent else min_seconds)
        if fitted["ok"] and adjacent and fitted["speech_end"] < float(item["speech_end"]) - 0.05 and fitted["speech_end"] - fitted["speech_start"] < min_seconds - 1e-3:
            fitted = {"ok": False, "reason": f"Its {clip:.2f} s of footage no longer reaches the next cutaway and is shorter than {min_seconds:.1f} s."}
        if not fitted["ok"]:
            rejected.append(dict(item, approval="rejected", reason=f"{fitted['reason']} The teacher stays on screen."))
            continue
        row = dict(item, speech_end=fitted["speech_end"], source_in=fitted["source_in"], source_out=fitted["source_out"])
        if clip is not None:
            row["clip_duration"] = clip
        if fitted["adjusted"]:
            row["adjusted"] = fitted["adjusted"]
        kept.append(row)
    return kept, rejected


def public_plan(
    session: dict,
    engine_plan: dict,
    placements: list[dict],
    validation: dict,
    catalogue: list[dict],
    evaluations: list[dict] | None = None,
    rejected: list[dict] | None = None,
) -> dict:
    from app.services.visual.analysis import drop_vectors

    by_id = {item["asset_id"]: item for item in placements}
    pictures = []
    for decision in engine_plan.get("decisions") or []:
        if decision.get("action") not in {"show_teacher", "replace_with_broll"}:
            continue
        role = picture_role(decision["action"])
        placement = by_id.get(decision.get("asset_id") or "")
        row = {
            "decision_id": decision["id"],
            "picture_role": role,
            "start": decision["start"],
            "end": decision["end"],
            "reason": decision.get("reason") or "",
        }
        if placement and role == "show_broll":
            row.update({key: placement.get(key) for key in PLACEMENT_FIELDS if key in placement})
        pictures.append(row)
    title = next((item for item in engine_plan.get("decisions") or [] if item.get("action") == "title"), {})
    return {
        "session_id": session["session_id"],
        "mode": session["mode"],
        "status": "plan_ready" if validation.get("ok") else "failed",
        "raw_video": {
            "upload_id": session["raw_video"]["upload_id"],
            "filename": session["raw_video"]["filename"],
            "duration_seconds": session["raw_video"].get("duration_seconds"),
        },
        "transcript": {
            "source_sha256": (session.get("transcript_meta") or {}).get("source_sha256"),
            "model": (session.get("transcript_meta") or {}).get("model"),
            "provider": (session.get("transcript_meta") or {}).get("method"),
            "segment_count": len((engine_plan.get("input_media") or {}).get("transcript", {}).get("segments") or []),
        },
        "picture_decisions": pictures,
        "placements": [{key: item.get(key) for key in PLACEMENT_FIELDS if key in item} for item in placements],
        "rejected_placements": [{key: item.get(key) for key in PLACEMENT_FIELDS if key in item} for item in rejected or []],
        "evaluations": evaluations or [],
        "title_decision_text": title.get("text", ""),
        "catalogue": [
            {
                "asset_id": item["asset_id"],
                "filename": item["filename"],
                "description": item["description"],
                "duration_seconds": item.get("duration_seconds"),
                "width": item.get("width"),
                "height": item.get("height"),
                "frame_rate": item.get("frame_rate"),
                "codec": item.get("codec") or "",
                "size_bytes": item.get("size_bytes"),
                "validation_status": item.get("validation_status"),
                "sha256": item.get("sha256"),
                "visual_analysis": drop_vectors(item["visual_analysis"]) if isinstance(item.get("visual_analysis"), dict) else item.get("visual_analysis"),
                "thumbnail": bool(isinstance(item.get("visual_analysis"), dict) and item["visual_analysis"].get("thumbnail")),
                "origin": item.get("origin") or "user",
            }
            for item in catalogue
        ],
        "broll_cap": engine_broll_cap(),
        "validation": validation,
        "rendered": False,
    }


def validate_envelope(session: dict, engine_plan: dict, placements: list[dict], catalogue: list[dict]) -> dict:
    _engine()
    from validation.plan import validate_plan

    errors = list(validate_plan(engine_plan))
    owned = {item["upload_id"] for item in session.get("broll") or []}
    owned.add(session["raw_video"]["upload_id"])
    durations = {item["asset_id"]: item.get("duration_seconds") for item in catalogue}
    mode = session["mode"]
    for placement in placements:
        if not (placement.get("explanation") or "").strip():
            errors.append(f"{placement.get('asset_id')}: matching explanation is missing")
        start = float(placement.get("source_in") if placement.get("source_in") is not None else -1)
        end = float(placement.get("source_out") if placement.get("source_out") is not None else -1)
        if start < 0 or end <= start:
            errors.append(f"{placement.get('asset_id')}: source interval is invalid")
        duration = durations.get(placement.get("asset_id"))
        if duration is None:
            duration = placement.get("clip_duration")
        if isinstance(duration, (int, float)) and end > float(duration) + 1e-3:
            errors.append(f"{placement.get('asset_id')} ({placement.get('filename') or 'clip'}): source interval {start:.3f}–{end:.3f} s is past the clip duration of {float(duration):.3f} s")
        if mode == "my_broll":
            if placement.get("asset_id") not in owned:
                errors.append(f"{placement.get('asset_id')}: asset is not part of this session")
            page = str(placement.get("page_url") or "")
            if page or placement.get("origin") == "commons":
                errors.append(f"{placement.get('asset_id')}: Mode B cannot use external footage")
        speech_start = float(placement.get("speech_start") or 0)
        speech_end = float(placement.get("speech_end") or 0)
        if speech_start < 0 or speech_end <= speech_start:
            errors.append(f"{placement.get('asset_id')}: speech interval is invalid")
    cutaways = [item for item in engine_plan.get("decisions") or [] if item.get("action") == "replace_with_broll"]
    by_asset = {item.get("asset_id"): item for item in cutaways}
    for placement in placements:
        decision = by_asset.get(placement.get("asset_id"))
        if placement.get("approval") == "rejected":
            if decision is not None:
                errors.append(f"{placement.get('asset_id')}: a rejected placement is still on the timeline")
            continue
        if decision is None:
            errors.append(f"{placement.get('asset_id')}: approved placement has no cutaway on the timeline")
            continue
        length = float(decision["end"]) - float(decision["start"])
        source_length = float(placement.get("source_out") or 0) - float(placement.get("source_in") or 0)
        if abs(float(placement.get("speech_start") or 0) - float(decision["start"])) > 0.05:
            errors.append(f"{placement.get('asset_id')}: placement start does not match the timeline")
        if source_length + 0.05 < length:
            errors.append(f"{placement.get('asset_id')}: source interval is shorter than its time on screen")
    duration = float((engine_plan.get("input_media") or {}).get("duration_seconds") or 0)
    if duration > 0:
        covered = sum(float(item["end"]) - float(item["start"]) for item in cutaways)
        if covered > engine_broll_cap() * duration + 1e-3:
            errors.append(f"B-roll covers {covered:.2f} s, over the {engine_broll_cap():.0%} cap")
    if mode == "my_broll":
        for decision in cutaways:
            if decision.get("page_url") or str(decision.get("asset_path") or "").startswith("http"):
                errors.append(f"{decision.get('id')}: Mode B decision points outside the user library")
            if decision.get("asset_id") not in owned:
                errors.append(f"{decision.get('asset_id')}: cutaway is not one of this session's uploads")
    return {"ok": not errors, "errors": errors}


def lesson_duration(transcript: dict, probed: float | None) -> float:
    ends = [float(segment["end"]) for segment in transcript.get("segments") or [] if segment.get("end") is not None]
    spoken = max(ends) if ends else 0.0
    if isinstance(probed, (int, float)) and probed > 0:
        return round(max(spoken, float(probed)), 3)
    return round(spoken, 3)
