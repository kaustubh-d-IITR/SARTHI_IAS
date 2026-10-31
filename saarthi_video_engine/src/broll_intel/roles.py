"""Ground a model subject in the deterministic sentence roles.

A pronoun such as "maine" is the speaker, not the object of the sentence.
"""

from __future__ import annotations

import re

from broll_intel.taxonomy import classify_sentence
from transcription.romanize import transliterate

_PRONOUNS = {
    "i", "me", "my", "maine", "main", "mein", "mai", "speaker", "narrator",
}
_AGENT = re.compile(r"\b(?:maine|mainne|main|mein|mai|i|me)\b", re.I)


def interpret(
    text: str,
    start: float = 0.0,
    end: float = 1.0,
    language: str = "und",
    model_subject: str | None = None,
    model_negation: bool | None = None,
) -> dict:
    base = classify_sentence(text, start, end, language)
    object_name = base["primary_subject"]
    proposed = (model_subject or "").strip()
    if proposed and proposed.lower() not in _PRONOUNS and not object_name:
        object_name = proposed
    agent = "speaker" if _AGENT.search(transliterate(text)) or proposed.lower() in _PRONOUNS else "unspecified"
    negation = base["negation"] if model_negation is None else bool(base["negation"] or model_negation)
    return {
        "agent": agent,
        "object": object_name,
        "action": base["action"],
        "negation": negation,
        "negation_kind": base["negation_kind"],
        "overlay_type": base["overlay_type"],
        "visual_brief": base["visual_brief"],
        "required_objects": base["required_objects"],
        "forbidden_visuals": base["forbidden_visuals"],
        "visual_type": base["visual_type"],
        "exact_transcript": text,
    }
