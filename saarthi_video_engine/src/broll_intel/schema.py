"""Validated editorial and verification records.

Model JSON is accepted only after this schema accepts it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    decision: Literal["ACCEPT", "REJECT", "REVIEW"]
    visual_summary: str = ""
    subject_match: bool = False
    action_match: bool = False
    context_match: bool = False
    contradiction_detected: bool = False
    required_elements_missing: list[str] = Field(default_factory=list)
    evidence: str = ""
    confidence: float = 0.0
    reason: str = ""


class EditorialDecision(BaseModel):
    lesson_id: str
    transcript_segment_id: str
    speech_start: float
    speech_end: float
    exact_transcript: str
    language: str = "und"
    primary_subject: str = ""
    entities: list[str] = Field(default_factory=list)
    action: str = "mentioned"
    semantic_intent: str = ""
    visual_type: Literal["literal", "representative", "diagram", "document", "symbolic", "none"] = "none"
    visual_brief: str = ""
    required_objects: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    forbidden_visuals: list[str] = Field(default_factory=list)
    negation: bool = False
    negation_kind: Literal["none", "historical_absence", "warning", "comparison", "hypothetical"] = "none"
    contrast_type: str = "none"
    overlay_type: str = ""
    search_queries: list[str] = Field(default_factory=list)
    retrieval_status: str = "not_searched"
    candidate_ids: list[str] = Field(default_factory=list)
    selected_asset_id: str = ""
    selected_source_in: float | None = None
    selected_source_out: float | None = None
    confidence: float = 0.0
    verification_evidence: dict = Field(default_factory=dict)
    generation_required: bool = False
    review_required: bool = False
    decision_reason: str = ""
    schedule: Literal["broll", "teacher", "review"] = "teacher"


def accept_verification(result: VerificationResult, *, minimum: float = 0.55) -> VerificationResult:
    """A high self-reported score cannot accept a clip that misses the subject or action."""
    if result.contradiction_detected or not result.subject_match or not result.action_match:
        return result.model_copy(update={"decision": "REJECT"})
    if result.decision == "ACCEPT" and result.confidence < minimum:
        return result.model_copy(update={"decision": "REVIEW"})
    if result.decision == "ACCEPT" and not result.context_match:
        return result.model_copy(update={"decision": "REVIEW"})
    return result
