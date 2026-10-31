"""Depictable speech concepts for a video search.

The map is a general list of concrete nouns, not a lesson-specific script.
Abstract words and personal statements are skipped. YouTube is named so a
caller can record why it was not downloaded.
"""

from __future__ import annotations

import re

QUERIES = {
    "newspaper": "newspaper",
    "akhbar": "newspaper",
    "ncert": "textbook",
    "textbook": "textbook",
    "book": "textbook",
    "kitab": "textbook",
    "kitaab": "textbook",
    "hospital": "hospital ward",
    "aspatal": "hospital ward",
    "aspataal": "hospital ward",
    "ppe": "medical protective clothing",
    "mask": "surgical mask",
    "gown": "medical gown",
    "river": "river",
    "mountain": "mountain landscape",
    "parliament": "parliament building",
    "glacier": "glacier",
    "forest": "forest",
    "vaccine": "vaccine",
    "ambulance": "ambulance",
    "monument": "monument",
    "temple": "temple",
    "election": "election ballot",
    "court": "courtroom",
    "train": "passenger train",
    "farmer": "farmer field",
    "flood": "flood water",
    "classroom": "classroom",
    "ocean": "ocean waves",
    "desert": "desert",
    "map": "world map",
    "rain": "rainfall",
    "city": "city street",
    "village": "village",
    "school": "school",
    "factory": "factory",
    "bridge": "bridge",
    "dam": "concrete dam",
    "airport": "airport",
    "market": "street market",
    "farm": "farmland",
    "crop": "crops",
}

SKIP = {
    "youtube": "YouTube is a platform name. An ordinary YouTube video is not downloaded or reused.",
    "sibling": "A personal family line is not illustrated with footage of other people.",
    "bhai": "A personal family line is not illustrated with footage of other people.",
    "behen": "A personal family line is not illustrated with footage of other people.",
    "mistake": "The point is abstract, so the teacher stays on screen.",
    "mistakes": "The point is abstract, so the teacher stays on screen.",
    "exam": "An unrelated exam-hall clip would not show this sentence.",
    "upsc": "The institution name is not a license to show an unrelated building.",
    "syllabus": "Syllabus text is usually already on the board.",
    "guidance": "The point is abstract, so the teacher stays on screen.",
    "advice": "The point is abstract, so the teacher stays on screen.",
    "cousin": "A personal family line is not illustrated with footage of other people.",
    "lecture": "He says he did not use the lecture, so a class clip is not shown as his own.",
}


def denies(text: str, term: str) -> bool:
    """True when the sentence rejects the thing it names."""
    from transcription.romanize import transliterate

    lowered = transliterate(text).lower().replace("-", " ")
    if term.lower() not in lowered:
        return False
    if any(flag in lowered for flag in ("didn't", "did not", "do not", "don't", "never ")):
        return True
    return re.search(r"\b(?:na+ hi|nahi+n?|nahin|naheen)\b", lowered) is not None


VARIANTS = {
    "textbook": ("library bookshelf", "books on a shelf", "school library"),
    "medical protective clothing": ("personal protective equipment", "donning personal protective equipment"),
    "surgical mask": ("n95 respirator",),
    "newspaper": ("printing press",),
    "hospital ward": ("hospital building",),
}


def query_variants(query: str) -> list[str]:
    """The mapped query, then more specific phrases. The caller stops when one clip is kept."""
    variants = [query]
    for extra in VARIANTS.get(query, ()):
        if extra not in variants:
            variants.append(extra)
    return variants


def visual_queries(text: str) -> list[dict]:
    """Search rows and skip rows for one piece of speech."""
    lowered = text.lower().replace("-", " ")
    rows: list[dict] = []
    seen: set[str] = set()
    for term, query in QUERIES.items():
        if query in seen or not _has_term(lowered, term):
            continue
        seen.add(query)
        rows.append({"term": term, "query": query, "action": "search", "reason": ""})
    for term, reason in SKIP.items():
        if _has_term(lowered, term.replace("-", " ")):
            rows.append({"term": term, "query": None, "action": "skip", "reason": reason})
    return rows


def _has_term(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text) is not None
