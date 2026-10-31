"""Explicit subject rules for B-roll. No model call.

Aliases cover Romanized Hindi and Hinglish. The spoken transcript is not rewritten.
A personal relative, or a named exam, does not become a stock shot of a stranger
or a random building.
"""

from __future__ import annotations

import re

from transcription.romanize import transliterate

# term -> (category, normalized subject, commons query or None when the teacher stays)
TERMS: dict[str, tuple[str, str, str | None]] = {
    "newspaper": ("study_material", "newspaper", "newspaper"),
    "akhbar": ("study_material", "newspaper", "newspaper"),
    "ncert": ("study_material", "NCERT", "textbook"),
    "textbook": ("book", "textbook", "textbook"),
    "book": ("book", "book", "textbook"),
    "kitab": ("book", "book", "textbook"),
    "kitaab": ("book", "book", "textbook"),
    "notebook": ("study_material", "notebook", "notebook"),
    "library": ("place", "library", "library"),
    "hospital": ("place", "hospital", "hospital ward"),
    "aspatal": ("place", "hospital", "hospital ward"),
    "aspataal": ("place", "hospital", "hospital ward"),
    "ppe": ("object", "protective clothing", "medical protective clothing"),
    "mask": ("object", "face mask", "surgical mask"),
    "n95": ("object", "n95 mask", "n95 respirator"),
    "pen": ("tool", "pen", "pen writing"),
    "pencil": ("tool", "pencil", "pencil"),
    "phone": ("tool", "phone", "mobile phone"),
    "laptop": ("tool", "laptop", "laptop computer"),
    "computer": ("tool", "computer", "computer"),
    "school": ("place", "school", "school"),
    "classroom": ("place", "classroom", "classroom"),
    "college": ("place", "college", "college campus"),
    "office": ("place", "office", "office"),
    "village": ("place", "village", "village"),
    "city": ("place", "city", "city street"),
    "teacher": ("person", "teacher", "teacher classroom"),
    "student": ("person", "student", "student studying"),
    "doctor": ("person", "doctor", "doctor"),
    "officer": ("person", "officer", "government office"),
    "friend": ("relationship", "friend", "friends talking"),
    "dost": ("relationship", "friend", "friends talking"),
    "mother": ("relationship", "mother", None),
    "father": ("relationship", "father", None),
    "maa": ("relationship", "mother", None),
    "papa": ("relationship", "father", None),
    "pitaji": ("relationship", "father", None),
    "beta": ("relationship", "son", None),
    "beti": ("relationship", "daughter", None),
    "bhai": ("relationship", "brother", None),
    "behen": ("relationship", "sister", None),
    "bahan": ("relationship", "sister", None),
    "sibling": ("relationship", "sibling", None),
    "cousin": ("relationship", "cousin", None),
    "family": ("relationship", "family", None),
    "upsc": ("examination", "UPSC", None),
    "neet": ("examination", "NEET", None),
    "csat": ("examination", "CSAT", None),
    "covid": ("event", "COVID", None),
    "youtube": ("organization", "YouTube", None),
    "mamc": ("institution", "MAMC", None),
}

PERSONAL = {"mother", "father", "brother", "sister", "sibling", "cousin", "son", "daughter", "family"}

SKIP_REASON = {
    "examination": "The exam name is not a license to show an unrelated hall or building.",
    "organization": "A platform name is not downloaded as ordinary footage.",
    "institution": "The institution is already on the board, so a stock building is not added.",
    "event": "A named event is not illustrated with unrelated footage.",
}


def normalize_alias(token: str) -> str:
    """Fold a spoken token onto the dictionary key. Unknown tokens stay as given."""
    word = transliterate(token).lower().strip(".,?!")
    word = {"bahan": "behen", "kitab": "kitaab", "ma": "maa"}.get(word, word)
    return word


def subjects_in_text(text: str) -> list[dict]:
    """Subjects named in one sentence. The taxonomy lives in subject_rules."""
    from analysis.subject_rules import detect

    return detect(text)


def _conditional_subjects(text: str) -> list[dict]:
    """Activities whose picture depends on who the sentence is about."""
    lowered = transliterate(text).lower().replace("-", " ")
    family = bool(re.search(r"\b(?:behen|bahan|bhai|sibling|sister|brother|cousin)\b", lowered))
    rows = []
    if re.search(r"\b(?:prepare|tayyari|taiyaari)\b", lowered):
        rows.append(
            {
                "term": "prepare",
                "category": "activity",
                "normalized": "studying",
                "proper_noun": False,
                "query": None if family else "student writing notes",
                "action": "skip" if family else "search",
                "reason": "The line is about his own sister, so a stock student would be presented as her." if family else "",
            }
        )
    if re.search(r"\bprocess\b", lowered) and re.search(r"\b(?:you|aap)\b", lowered) and not family:
        rows.append(
            {
                "term": "process",
                "category": "activity",
                "normalized": "study process",
                "proper_noun": False,
                "query": "student writing notes",
                "action": "search",
                "reason": "",
            }
        )
    if re.search(r"\b(?:ghar|home|house)\b", lowered):
        rows.append(
            {
                "term": "ghar",
                "category": "place",
                "normalized": "home",
                "proper_noun": False,
                "query": None,
                "action": "skip",
                "reason": "A stock house would be presented as his home.",
            }
        )
    return rows


def opportunity_records(transcript: dict) -> list[dict]:
    """One record per sentence. Related words in that sentence share the record."""
    from analysis.subject_rules import records

    return records(transcript)
