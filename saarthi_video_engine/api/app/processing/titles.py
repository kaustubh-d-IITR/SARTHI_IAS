"""Video title. The user's text is kept exactly; a suggestion is only ever a suggestion."""

from __future__ import annotations

import re
from collections import Counter

from app.errors import StudioError

MAX_TITLE = 120
_WORD = re.compile(r"[A-Za-z][A-Za-z']+")
_STOP = {
    "this", "that", "with", "from", "have", "been", "will", "just", "they", "them", "then", "than",
    "what", "when", "where", "which", "while", "your", "about", "there", "their", "into", "were",
    "would", "could", "should", "because", "even", "also", "very", "really", "okay", "right", "like",
    "know", "going", "doing", "coming", "back", "time", "some", "more", "most", "only", "same",
    "every", "everybody", "something", "nothing", "things", "thing", "here", "these", "those",
    "make", "made", "take", "want", "need", "said", "says", "tell", "told", "think", "used", "using",
    "first", "last", "next", "still", "single", "whole", "best", "perfect", "available", "trying",
}


def clean_title(raw: object) -> str:
    """Exact text, minus surrounding whitespace. Empty means no title."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise StudioError(422, "invalid_title", "The title must be text.")
    text = raw.strip()
    if len(text) > MAX_TITLE:
        raise StudioError(422, "title_too_long", f"Keep the title to {MAX_TITLE} characters.")
    if any(ord(char) < 32 and char != "\n" for char in text):
        raise StudioError(422, "invalid_title", "The title contains control characters.")
    return text


def title_record(text: str, source: str) -> dict:
    return {"text": text, "status": "user_confirmed" if text else "none", "source": source if text else "none"}


def suggest_title(transcript: dict) -> dict | None:
    """Recurring topic words from the transcript, offered for the user to confirm or ignore.

    Acronyms such as UPSC or NCERT count once seen; other words must recur. No
    suggestion is offered when fewer than two topic words recur.
    """
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for segment in transcript.get("segments") or []:
        for word in _WORD.findall(segment.get("original_text") or segment.get("text") or ""):
            key = word.lower().strip("'")
            if len(key) < 4 and not (word.isupper() and len(word) >= 2):
                continue
            if key in _STOP:
                continue
            counts[key] += 1
            if word.isupper() and len(word) >= 2:
                display[key] = word
            else:
                display.setdefault(key, word.capitalize())
    ranked = []
    for key, count in counts.most_common():
        acronym = display[key].isupper()
        if count >= 2 or (acronym and len(key) >= 3):
            ranked.append((count + (1 if acronym else 0), key))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    words = [display[key] for _, key in ranked[:3]]
    if len(words) < 2:
        return None
    text = " and ".join(words) if len(words) == 2 else f"{words[0]}, {words[1]} and {words[2]}"
    return {
        "text": text,
        "status": "ai_suggestion",
        "basis": "Most frequent topic words in the transcript: " + ", ".join(f"{display[key]} ({counts[key]}x)" for _, key in ranked[:3]),
    }


def apply_title(engine_plan: dict, text: str) -> None:
    """Write the confirmed title into the existing title decision. Empty leaves the title line blank."""
    for decision in engine_plan.get("decisions") or []:
        if decision.get("action") != "title":
            continue
        decision["text"] = text
        decision["source"] = "observed" if text else "default"
        decision["confidence"] = 1.0 if text else 0.3
        decision["reason"] = (
            "Title entered and confirmed by the user in the Studio. The text is used exactly as typed."
            if text
            else "No title was confirmed in the Studio, so the title line is left empty instead of using a transcript fragment."
        )
