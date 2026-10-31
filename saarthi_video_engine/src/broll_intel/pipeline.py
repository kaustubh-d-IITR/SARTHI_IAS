"""Build the Phase 17 plans and call Gemini only for retrieved segments."""

from __future__ import annotations

import json
from pathlib import Path

from broll_intel.coverage import coverage_report
from broll_intel.gemini_api import GeminiError, generate_structured, usage_note, video_generation_status
from broll_intel.planner import plan_lesson
from broll_intel.schema import VerificationResult
from broll_intel.selection import apply_verification, attach_candidate

VERIFY_PROMPT = """You verify one B-roll segment for an educational short.
Describe only what is visible in the frames. The frames are the span that would play.
Decide ACCEPT only when the frames show the required subject and the required action.
REJECT a keyword match that shows a different activity.
REJECT a printing press for a newspaper sentence, a document on a computer for opening YouTube, and shelves when a newspaper was requested.
Use REVIEW when the frames are ambiguous.
Do not treat a confident guess as a substitute for a visible object.

Transcript: {transcript}
Visual brief: {brief}
Required objects: {objects}
Required actions: {actions}
Forbidden: {forbidden}
Negation: {negation}
Overlay requested: {overlay}
"""


def build(transcript: dict, lesson_id: str, candidates: list[dict], *, verify: bool) -> tuple[list[dict], list[dict]]:
    decisions = plan_lesson(transcript, lesson_id)
    calls: list[dict] = []
    used: set[str] = set()
    finished = []
    for decision in decisions:
        if decision.schedule != "broll":
            finished.append(decision)
            continue
        candidate = _match(decision, candidates, used)
        decision = attach_candidate(decision, candidate)
        if candidate and verify:
            frames = decision.verification_evidence.get("frames") or []
            print("verify", lesson_id, decision.transcript_segment_id, decision.primary_subject, flush=True)
            if frames:
                try:
                    result = generate_structured(
                        VERIFY_PROMPT.format(
                            transcript=decision.exact_transcript,
                            brief=decision.visual_brief,
                            objects=", ".join(decision.required_objects),
                            actions=", ".join(decision.required_actions),
                            forbidden=", ".join(decision.forbidden_visuals),
                            negation=decision.negation_kind,
                            overlay=decision.overlay_type or "none",
                        ),
                        VerificationResult,
                        images=[Path(path) for path in frames],
                    )
                    if not isinstance(result, VerificationResult):
                        result = VerificationResult.model_validate(result)
                    decision = apply_verification(decision, result)
                    calls.append({"lesson_id": lesson_id, "segment": decision.transcript_segment_id, "status": "ok", **usage_note()})
                except GeminiError as error:
                    decision = decision.model_copy(
                        update={
                            "schedule": "review",
                            "retrieval_status": "verifier_failed",
                            "review_required": True,
                            "decision_reason": str(error),
                        }
                    )
                    calls.append({"lesson_id": lesson_id, "segment": decision.transcript_segment_id, "status": "error", "detail": str(error)})
            else:
                decision = decision.model_copy(
                    update={
                        "schedule": "review",
                        "review_required": True,
                        "decision_reason": "The candidate has no frames from the played span, so it was not verified.",
                    }
                )
        elif decision.schedule == "review":
            pass
        elif decision.schedule == "broll" and candidate is None:
            decision = decision.model_copy(
                update={
                    "schedule": "teacher",
                    "retrieval_status": "unfilled",
                    "generation_required": True,
                    "decision_reason": "No licensed candidate was available for this sentence. The teacher stays.",
                }
            )
        if decision.selected_asset_id and decision.schedule == "broll":
            used.add(decision.selected_asset_id)
        finished.append(decision)
    return [item.model_dump() for item in finished], calls


def _match(decision, candidates: list[dict], used: set[str]) -> dict | None:
    subject = decision.primary_subject
    for row in candidates:
        if row.get("rejected") or row.get("video_id") not in {None, decision.lesson_id}:
            continue
        if str(row.get("subject") or "") != subject:
            continue
        identity = str(row.get("page_url") or row.get("local_path") or "")
        if not identity or identity in used:
            continue
        return row
    return None


def summarize(decisions: list[dict], lesson_seconds: float) -> dict:
    eligible = [
        (float(row["speech_start"]), float(row["speech_end"]))
        for row in decisions
        if row.get("visual_type") != "none"
    ]
    planned = [
        (float(row["speech_start"]), float(row["speech_end"]))
        for row in decisions
        if row.get("schedule") == "broll"
    ]
    verified = list(planned)
    report = coverage_report(
        lesson_seconds=lesson_seconds,
        eligible_seconds=_span(eligible),
        planned=planned,
        rendered=[],
        verified=verified,
    )
    report["rendered_note"] = "Rendered coverage is filled only after a final MP4 is measured. This figure is the verified plan."
    report["verified_is_plan"] = True
    return report


def _span(intervals: list[tuple[float, float]]) -> float:
    from broll_intel.coverage import union_seconds

    return union_seconds(intervals)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
