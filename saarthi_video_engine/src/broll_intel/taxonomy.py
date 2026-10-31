"""Sentence meaning from the production taxonomy, plus negation kind.

The spoken words are not translated or rewritten.
"""

from __future__ import annotations

import re

from analysis.subject_rules import records
from transcription.romanize import transliterate

_WARNING = re.compile(r"\b(?:don't|do not|never|mat|nahi karna|nahin karna)\b", re.I)
_HYPOTHETICAL = re.compile(r"\b(?:would have|would've|if i|agar)\b", re.I)


def classify_sentence(text: str, start: float, end: float, language: str = "und") -> dict:
    row = records({"segments": [{"text": text, "start": start, "end": end}]})[0]
    scene = row.get("scene") or {}
    subjects = row.get("subjects") or []
    names = [item.get("normalized") or "" for item in subjects if item.get("normalized")]
    placeable = [item for item in subjects if item.get("schedule") == "place"]
    clinical = [item for item in placeable if item.get("normalized") in {"hospital", "COVID", "protective clothing", "face mask", "n95 mask"}]
    ordered = clinical + [item for item in placeable if item not in clinical]
    primary = (ordered[0].get("normalized") if ordered else "") or ""
    negated = list(scene.get("negated") or [])
    lowered = transliterate(text)
    if negated and primary in negated:
        kind = "historical_absence"
    elif negated:
        kind = "historical_absence"
    elif _WARNING.search(lowered):
        kind = "warning"
    elif _HYPOTHETICAL.search(lowered) or scene.get("framing") == "hypothetical":
        kind = "hypothetical"
    else:
        kind = "none"
    overlay = scene.get("overlay") or ""
    if kind == "warning":
        overlay = ""
    if primary in {"newspaper", "NCERT"} and primary in negated and scene.get("action") == "reading":
        overlay = "negation_cross"
    visual_type = "none"
    if ordered and overlay == "negation_cross":
        visual_type = "symbolic"
    elif ordered:
        visual_type = "literal"
    elif names and not placeable:
        visual_type = "none"
    brief = _brief(primary, scene.get("action") or "mentioned", overlay, kind)
    return {
        "text": text,
        "language": language,
        "subjects": subjects,
        "entities": names,
        "primary_subject": primary,
        "action": scene.get("action") or "mentioned",
        "negated": negated,
        "negation": bool(negated),
        "negation_kind": kind,
        "contrast_type": kind if kind != "none" else "none",
        "overlay_type": overlay,
        "visual_type": visual_type,
        "visual_brief": brief,
        "required_objects": _objects(primary),
        "required_actions": _actions(scene.get("action") or "", overlay),
        "forbidden_visuals": _forbidden(primary),
        "semantic_intent": scene.get("intent") or brief,
        "teacher_preferred": not bool(ordered),
        "schedule_reason": (ordered[0].get("reason") if ordered else "") or row.get("rationale") or "",
    }


def _brief(subject: str, action: str, overlay: str, kind: str) -> str:
    if not subject:
        return "Keep the teacher. The sentence does not name a scene that stock footage can show."
    if overlay == "negation_cross":
        return f"Show a real {subject} while the speaker says the {action} did not happen, and draw a cross on that insert only."
    if subject == "YouTube":
        return "Show a hand opening the YouTube app on a phone. A document on a computer is not that action."
    if subject in {"hospital", "COVID"}:
        return "Show clinicians, a patient, or medical equipment in a care setting."
    if subject == "protective clothing":
        return "Show a person wearing or putting on medical protective clothing."
    if action == "reading":
        return f"Show a person reading a {subject}."
    if action in {"writing", "studying"}:
        return "Show a person studying or writing at a desk, with the materials visible."
    return f"Show {action} {subject}.".strip()


def _objects(subject: str) -> list[str]:
    table = {
        "newspaper": ["newspaper"],
        "NCERT": ["textbook"],
        "book": ["book"],
        "YouTube": ["phone", "youtube"],
        "hospital": ["hospital", "clinician"],
        "COVID": ["protective clothing", "clinician"],
        "protective clothing": ["gown", "mask"],
        "exam paper": ["exam paper"],
        "GS papers": ["exam paper"],
        "study process": ["pen", "paper"],
    }
    return list(table.get(subject, [subject] if subject else []))


def _actions(action: str, overlay: str) -> list[str]:
    if overlay == "negation_cross":
        return [action or "reading", "crossed out"]
    if action and action != "mentioned":
        return [action]
    return []


def _forbidden(subject: str) -> list[str]:
    table = {
        "newspaper": ["printing press", "library shelves", "wikipedia talk"],
        "NCERT": ["printing press", "unrelated factory"],
        "YouTube": ["pdf on a computer", "wikisource", "download explainer", "tracking phone"],
        "hospital": ["building entrance only", "ppe training titled as ebola when the line is only arrival"],
        "book": ["sheriff", "police"],
    }
    return list(table.get(subject, []))
