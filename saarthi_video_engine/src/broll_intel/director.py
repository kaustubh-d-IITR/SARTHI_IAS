"""Semantic director.

Pass A reads one sentence. Local rules decide the object when the model
returns a pronoun. Pass B may schedule only a candidate the verifier accepted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from broll_intel.queries import build_queries
from broll_intel.roles import interpret

_BARE = {
    "newspaper", "ncert", "book", "youtube", "phone", "hospital", "exam", "pen",
}


class DirectorRead(BaseModel):
    """What Gemini is allowed to return. Rules still win on pronouns and bans."""

    agent: str = ""
    subject: str = ""
    object: str = ""
    action: str = ""
    entities: list[str] = Field(default_factory=list)
    semantic_intent: str = ""
    negation: bool = False
    contrast: str = "none"
    visual_type: Literal["literal", "representative", "diagram", "document", "symbolic", "none"] = "none"
    visual_action: str = ""
    visual_brief: str = ""
    required_visual_elements: list[str] = Field(default_factory=list)
    forbidden_visual_elements: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    overlay_instructions: str = ""
    preferred_duration: float = 0.0
    decision_reason: str = ""
    review_required: bool = False


def analyze(
    text: str,
    start: float = 0.0,
    end: float = 1.0,
    language: str = "und",
    model: DirectorRead | None = None,
    neighbors: tuple[str, str] = ("", ""),
) -> dict:
    """Ground a sentence. Neighbor text is context only and is not rewritten into the line."""
    proposed = ""
    model_negation = None
    if model is not None:
        proposed = model.object or model.subject
        model_negation = model.negation
    base = interpret(
        text,
        start,
        end,
        language,
        model_subject=proposed or None,
        model_negation=model_negation,
    )
    classified = {
        "primary_subject": base["object"],
        "action": base["action"],
        "overlay_type": base["overlay_type"],
        "negation": base["negation"],
    }
    duration = max(0.0, float(end) - float(start))
    visual_type = base["visual_type"]
    if visual_type == "none":
        duration = 0.0
    else:
        duration = min(duration, 8.0)
    overlay = base["overlay_type"] or ""
    return {
        "segment_spoken_text": text,
        "neighbor_before": neighbors[0],
        "neighbor_after": neighbors[1],
        "agent": base["agent"],
        "subject": base["object"],
        "object": base["object"],
        "action": base["action"],
        "entities": [base["object"]] if base["object"] else [],
        "semantic_intent": base["visual_brief"],
        "negation": base["negation"],
        "contrast": base["negation_kind"] if base["negation_kind"] != "none" else "none",
        "visual_type": visual_type,
        "visual_action": base["action"],
        "visual_brief": base["visual_brief"],
        "required_visual_elements": list(base["required_objects"]),
        "forbidden_visual_elements": list(base["forbidden_visuals"]),
        "search_queries": _queries(
            classified,
            model.search_queries if model else [],
            list(base["forbidden_visuals"]),
        ),
        "overlay_instructions": "negation_cross" if overlay == "negation_cross" else "",
        "preferred_duration": round(duration, 3),
        "decision_reason": base["visual_brief"],
        "review_required": False,
    }


def merge_director(row: dict, grounded: dict, source: str) -> dict:
    """Attach the read. Verification status and source bounds stay as they were."""
    updated = dict(row)
    updated["primary_subject"] = grounded["object"] or row.get("primary_subject") or ""
    updated["action"] = grounded["action"] or row.get("action") or "mentioned"
    updated["negation"] = bool(grounded["negation"])
    updated["visual_type"] = grounded["visual_type"]
    updated["visual_brief"] = grounded["visual_brief"]
    updated["required_objects"] = list(grounded["required_visual_elements"])
    updated["forbidden_visuals"] = list(grounded["forbidden_visual_elements"])
    updated["search_queries"] = list(grounded["search_queries"])
    if grounded["overlay_instructions"] == "negation_cross":
        updated["overlay_type"] = "negation_cross"
    updated["director"] = {"source": source, **grounded}
    return updated


def refine(row: dict, used_pages: set[str]) -> dict:
    """Pass B. A rejection stays a rejection. A repeated page is not played twice."""
    updated = dict(row)
    status = updated.get("retrieval_status") or ""
    page = updated.get("selected_asset_id") or (updated.get("verification_evidence") or {}).get("page_url") or ""
    if status != "verified":
        if status == "API_UNAVAILABLE":
            updated["schedule"] = "review"
            updated["review_required"] = True
        else:
            updated["schedule"] = "teacher"
            updated["review_required"] = False
        updated["selected_asset_id"] = "" if status == "rejected" else updated.get("selected_asset_id") or ""
        return updated
    if not page or updated.get("selected_source_in") is None:
        updated["schedule"] = "review"
        updated["review_required"] = True
        updated["decision_reason"] = "Verified row is missing a source page or in-point."
        return updated
    if page in used_pages:
        updated["schedule"] = "teacher"
        updated["retrieval_status"] = "rejected"
        updated["selected_asset_id"] = ""
        updated["decision_reason"] = "This source was already scheduled in the lesson."
        return updated
    used_pages.add(page)
    updated["schedule"] = "broll"
    updated["review_required"] = False
    return updated


def assets_for_render(plans: dict[str, list[dict]], catalogue: dict[str, dict]) -> list[dict]:
    """Rows the existing renderer may cut. One page per lesson."""
    accepted: list[dict] = []
    for rows in plans.values():
        used: set[str] = set()
        for row in rows:
            chosen = refine(row, used)
            if chosen.get("schedule") != "broll":
                continue
            page = chosen.get("selected_asset_id") or ""
            source = catalogue.get(page)
            if not source or not source.get("license") or not source.get("local_path"):
                continue
            copied = dict(source)
            copied["clip_in"] = chosen.get("selected_source_in")
            if chosen.get("overlay_type") == "negation_cross":
                copied["graphic"] = "negation_cross"
            accepted.append(copied)
    return accepted


def _queries(classified: dict, model_queries: list[str], forbidden: list[str]) -> list[str]:
    kept = build_queries(classified)
    bare = (classified.get("primary_subject") or "").lower()
    banned = [item.lower() for item in forbidden if item]
    for query in model_queries:
        text = " ".join(str(query).split())
        lowered = text.lower()
        if not text or lowered in _BARE or lowered == bare or len(text.split()) < 3:
            continue
        if any(phrase in lowered for phrase in banned):
            continue
        if text not in kept:
            kept.append(text)
    return kept[:4]


DIRECTOR_PROMPT = """You are reading one spoken line from a teaching video.
The words maine, main, mein, and I are the speaker, not the thing the viewer should see.
Name the physical object and the action. If the line denies that action, set negation true.
Do not ask for library shelves when the line is about a newspaper, a textbook, or writing.
Do not ask for a military exam when the line names a specific civilian exam.
Return only the schema.

Line: {text}
Before: {before}
After: {after}
"""
