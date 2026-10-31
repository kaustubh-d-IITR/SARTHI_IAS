"""Attach a retrieved segment and accept it only after the verifier agrees."""

from __future__ import annotations

from pathlib import Path

from broll_intel.schema import EditorialDecision, VerificationResult, accept_verification


def attach_candidate(decision: EditorialDecision, candidate: dict | None) -> EditorialDecision:
    if decision.schedule != "broll" or not candidate:
        return decision
    frames = [path for path in (candidate.get("visual_evidence") or []) if Path(path).is_file()]
    update = {
        "candidate_ids": [str(candidate.get("page_url") or candidate.get("local_path") or "")],
        "selected_asset_id": str(candidate.get("page_url") or ""),
        "selected_source_in": candidate.get("clip_in"),
        "selected_source_out": _source_out(candidate),
        "retrieval_status": "candidate",
        "verification_evidence": {
            "provider": candidate.get("provider") or "commons",
            "license": candidate.get("license"),
            "license_url": candidate.get("license_url"),
            "creator": candidate.get("artist"),
            "page_url": candidate.get("page_url"),
            "local_path": candidate.get("local_path"),
            "clip_score": candidate.get("relevance"),
            "frames": frames,
            "title": candidate.get("title"),
        },
        "review_required": True,
        "decision_reason": "A segment was retrieved. It is not accepted until verification.",
        "schedule": "review",
    }
    return decision.model_copy(update=update)


def apply_verification(decision: EditorialDecision, result: VerificationResult) -> EditorialDecision:
    gated = accept_verification(result)
    evidence = dict(decision.verification_evidence)
    evidence["gemini"] = gated.model_dump()
    if gated.decision == "ACCEPT":
        return decision.model_copy(
            update={
                "schedule": "broll",
                "retrieval_status": "verified",
                "confidence": gated.confidence,
                "review_required": False,
                "verification_evidence": evidence,
                "decision_reason": gated.reason or gated.visual_summary,
                "generation_required": False,
            }
        )
    if gated.decision == "REVIEW":
        return decision.model_copy(
            update={
                "schedule": "review",
                "retrieval_status": "review",
                "confidence": gated.confidence,
                "review_required": True,
                "verification_evidence": evidence,
                "decision_reason": gated.reason or "The verifier did not accept the segment.",
                "generation_required": False,
            }
        )
    return decision.model_copy(
        update={
            "schedule": "teacher",
            "retrieval_status": "rejected",
            "confidence": gated.confidence,
            "review_required": False,
            "selected_asset_id": "",
            "selected_source_in": None,
            "selected_source_out": None,
            "verification_evidence": evidence,
            "decision_reason": gated.reason or "The segment does not show the requested subject and action.",
            "generation_required": True,
        }
    )


def _source_out(candidate: dict) -> float | None:
    start = candidate.get("clip_in")
    limit = candidate.get("play_limit")
    if start is None or limit is None:
        return None
    return round(float(start) + float(limit), 3)
