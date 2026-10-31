"""Production subject taxonomy. Dictionaries, not a model.

The spoken transcript is not rewritten. A personal relative is searched and then
left on the teacher, because a stock person would be presented as that relative.
A named exam or institution is not illustrated with a random building.
"""

from __future__ import annotations

import re

from transcription.romanize import transliterate

# term -> category, normalized, primary query, portrayal
# portrayal: relative, home, named, object, activity, place
_ROWS: dict[str, tuple[str, str, str, str]] = {
    "newspaper": ("study_material", "newspaper", "newspaper", "object"),
    "akhbar": ("study_material", "newspaper", "newspaper", "object"),
    "ncert": ("study_material", "NCERT", "textbook", "object"),
    "textbook": ("book", "textbook", "textbook", "object"),
    "book": ("book", "book", "textbook", "object"),
    "kitab": ("book", "book", "textbook", "object"),
    "kitaab": ("book", "book", "textbook", "object"),
    "notebook": ("study_material", "notebook", "notebook", "object"),
    "magazine": ("study_material", "magazine", "magazine", "object"),
    "notes": ("study_material", "notes", "student writing notes", "object"),
    "syllabus": ("study_material", "syllabus", "textbooks on a desk", "object"),
    "paper": ("study_material", "exam paper", "examination answer booklet", "object"),
    "essay": ("study_material", "essay paper", "student writing an exam", "object"),
    "prelims": ("examination", "Prelims", "examination answer booklet", "named"),
    "csat": ("examination", "CSAT", "examination answer booklet", "named"),
    "gs": ("examination", "GS papers", "examination answer booklet", "object"),
    "library": ("place", "library", "library bookshelves", "place"),
    "hospital": ("place", "hospital", "hospital ward", "place"),
    "aspatal": ("place", "hospital", "hospital ward", "place"),
    "aspataal": ("place", "hospital", "hospital ward", "place"),
    "ppe": ("object", "protective clothing", "medical protective clothing", "object"),
    "mask": ("object", "face mask", "surgical mask", "object"),
    "n95": ("object", "n95 mask", "n95 respirator", "object"),
    "pen": ("tool", "pen", "pen writing", "object"),
    "pencil": ("tool", "pencil", "pencil writing", "object"),
    "phone": ("tool", "phone", "mobile phone", "object"),
    "laptop": ("tool", "laptop", "laptop computer", "object"),
    "computer": ("tool", "computer", "computer", "object"),
    "youtube": ("organization", "YouTube", "youtube on a phone", "object"),
    "school": ("place", "school", "school", "place"),
    "classroom": ("place", "classroom", "classroom", "place"),
    "college": ("place", "college", "college campus", "place"),
    "office": ("place", "office", "office", "place"),
    "village": ("place", "village", "village", "place"),
    "city": ("place", "city", "city street", "place"),
    "teacher": ("person", "teacher", "teacher classroom", "activity"),
    "student": ("person", "student", "student studying", "activity"),
    "doctor": ("person", "doctor", "doctor with patient", "activity"),
    "officer": ("person", "officer", "government office", "named"),
    "friend": ("relationship", "friend", "friends talking", "relative"),
    "dost": ("relationship", "friend", "friends talking", "relative"),
    "mitra": ("relationship", "friend", "friends talking", "relative"),
    "mother": ("relationship", "mother", "mother and child", "relative"),
    "father": ("relationship", "father", "father and child", "relative"),
    "maa": ("relationship", "mother", "mother and child", "relative"),
    "ma": ("relationship", "mother", "mother and child", "relative"),
    "mata": ("relationship", "mother", "mother and child", "relative"),
    "papa": ("relationship", "father", "father and child", "relative"),
    "pitaji": ("relationship", "father", "father and child", "relative"),
    "pita": ("relationship", "father", "father and child", "relative"),
    "beta": ("relationship", "son", "parent and son", "relative"),
    "beti": ("relationship", "daughter", "parent and daughter", "relative"),
    "bhai": ("relationship", "brother", "brothers", "relative"),
    "bhaiya": ("relationship", "brother", "brothers", "relative"),
    "behen": ("relationship", "sister", "sisters", "relative"),
    "bahan": ("relationship", "sister", "sisters", "relative"),
    "sibling": ("relationship", "sibling", "siblings", "relative"),
    "cousin": ("relationship", "cousin", "cousins", "relative"),
    "family": ("relationship", "family", "family at home", "relative"),
    "uncle": ("relationship", "uncle", "uncle", "relative"),
    "aunt": ("relationship", "aunt", "aunt", "relative"),
    "husband": ("relationship", "husband", "husband and wife", "relative"),
    "wife": ("relationship", "wife", "husband and wife", "relative"),
    "upsc": ("examination", "UPSC", "civil service examination", "named"),
    "neet": ("examination", "NEET", "medical entrance exam", "named"),
    "covid": ("event", "COVID", "hospital ward", "object"),
    "mamc": ("institution", "MAMC", "medical college", "named"),
    "lecture": ("activity", "lecture", "lecture on a screen", "activity"),
    "roti": ("food", "bread", "indian bread", "object"),
    "khana": ("food", "meal", "meal on a plate", "object"),
    "food": ("food", "food", "meal on a plate", "object"),
}

_PLACE_PORTRAYAL = {"object", "activity", "place"}
_FAMILY = re.compile(r"\b(?:behen|bahan|bhai|bhaiya|sibling|sister|brother|cousin)\b")


def detect(text: str) -> list[dict]:
    """Subjects in one sentence, in dictionary order, one row per normalized name."""
    lowered = transliterate(text).lower().replace("-", " ")
    found: list[dict] = []
    seen: set[str] = set()
    for term, (category, normalized, query, portrayal) in _ROWS.items():
        if normalized in seen or not re.search(rf"\b{re.escape(term)}\b", lowered):
            continue
        seen.add(normalized)
        found.append(_row(term, category, normalized, query, portrayal, lowered))
    found.extend(_activities(lowered))
    return found


def records(transcript: dict) -> list[dict]:
    """One opportunity per sentence. Search rows carry queries even when no clip is placed."""
    rows = []
    for segment in transcript.get("segments") or []:
        text = str(segment.get("text") or segment.get("original_text") or "")
        subjects = detect(text)
        placeable = any(item["schedule"] == "place" for item in subjects)
        scene = _scene(text, subjects)
        rows.append(
            {
                "text": text,
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "subjects": subjects,
                "teacher_preferred": not placeable,
                "scene": scene,
                "rationale": scene["intent"],
            }
        )
    return rows


def _scene(text: str, subjects: list[dict]) -> dict:
    """Subject, action, and framing for one sentence. The spoken words stay as stored."""
    from assets.concepts import denies

    lowered = transliterate(text).lower()
    names = [item.get("normalized") or "" for item in subjects]
    negated = [name for name in names if name and denies(text, name.lower().split()[0])]
    if negated:
        framing = "negative"
    elif re.search(r"\b(?:would have|would've|if i|agar)\b", lowered):
        framing = "hypothetical"
    else:
        framing = "positive"
    action = "mentioned"
    if re.search(r"\b(?:padh|padha|padhi|read|reading)\b", lowered):
        action = "reading"
    elif re.search(r"\b(?:youtube|khola|open|opened)\b", lowered) and "YouTube" in names:
        action = "opening"
    elif re.search(r"\b(?:wear|wearing|ppe|kit)\b", lowered):
        action = "wearing"
    elif re.search(r"\b(?:hospital|covid|duty)\b", lowered):
        action = "working"
    elif re.search(r"\b(?:writ|likh|exam|paper)\b", lowered):
        action = "writing"
    elif re.search(r"\b(?:prepar|study|padh)\b", lowered):
        action = "studying"
    overlay = "negation_cross" if "newspaper" in negated and action == "reading" else ""
    lead = names[0] if names else "the teacher"
    return {
        "subjects": names,
        "action": action,
        "framing": framing,
        "negated": negated,
        "overlay": overlay,
        "intended_scene": f"{action} {lead}".strip(),
        "intent": (
            "Show the named object and mark the denied action."
            if overlay
            else "Show the action the sentence describes."
            if framing == "positive"
            else "Search the subject, then leave the teacher if a stock clip would impersonate someone."
        ),
    }


def _row(term: str, category: str, normalized: str, query: str, portrayal: str, sentence: str) -> dict:
    named = portrayal == "named" or category in {"examination", "institution", "organization"}
    schedule = "place" if portrayal in _PLACE_PORTRAYAL and portrayal != "named" else "log_only"
    if portrayal == "named":
        schedule = "log_only"
    if category == "organization" and normalized == "YouTube":
        schedule = "place"
        portrayal = "object"
    reason = ""
    if portrayal == "relative":
        schedule = "log_only"
        reason = "A stock person would be presented as his own relative."
    elif portrayal == "named":
        reason = "A generic building or hall would be presented as this named institution or exam."
    elif portrayal == "home":
        schedule = "log_only"
        reason = "A stock house would be presented as his home."
    queries = _queries(normalized, query, sentence)
    return {
        "term": term,
        "category": category,
        "normalized": normalized,
        "proper_noun": named or normalized in {"NCERT"},
        "query": query,
        "queries": queries,
        "action": "search",
        "schedule": schedule,
        "portrayal": portrayal,
        "identity_claim": False,
        "reason": reason,
        "aliases": [term],
    }


def _activities(lowered: str) -> list[dict]:
    family = bool(_FAMILY.search(lowered))
    rows = []
    if re.search(r"\b(?:prepare|preparing|tayyari|taiyaari|karana|karane)\b", lowered) and "prepare" not in lowered:
        pass
    if re.search(r"\b(?:prepare|preparing|tayyari|taiyaari)\b", lowered):
        rows.append(
            _activity(
                "prepare",
                "studying",
                "student studying at a desk",
                "relative" if family else "activity",
                "The line is about his own sister, so a stock student would be presented as her." if family else "",
                "log_only" if family else "place",
            )
        )
    if re.search(r"\bprocess\b", lowered) and re.search(r"\b(?:you|aap)\b", lowered) and not family:
        rows.append(_activity("process", "study process", "student writing notes", "activity", "", "place"))
    if re.search(r"\b(?:ghar|home|house)\b", lowered):
        rows.append(
            _activity(
                "ghar",
                "home",
                "house exterior",
                "home",
                "A stock house would be presented as his home.",
                "log_only",
            )
        )
    if re.search(r"\b(?:everybody|everyone|sab|log)\b", lowered) and re.search(r"\b(?:mistake|mistakes|galati)\b", lowered):
        rows.append(_activity("everybody", "students making mistakes", "student checking a marked paper", "activity", "", "place"))
    return rows


def _activity(term: str, normalized: str, query: str, portrayal: str, reason: str, schedule: str) -> dict:
    return {
        "term": term,
        "category": "activity" if portrayal != "home" else "place",
        "normalized": normalized,
        "proper_noun": False,
        "query": query,
        "queries": _queries(normalized, query, ""),
        "action": "search",
        "schedule": schedule,
        "portrayal": portrayal,
        "identity_claim": False,
        "reason": reason,
        "aliases": [term],
    }


def _queries(normalized: str, query: str, sentence: str) -> list[str]:
    """Several short queries. Long phrases fail the Commons word check, so anchors stay short."""
    from assets.concepts import denies

    denied = denies(sentence, normalized.lower().split()[0]) if sentence else False
    table = {
        "newspaper": ["person reading a newspaper", "newspaper close up", "newspaper"] if denied else ["person reading a newspaper", "open newspaper", "newspaper"],
        "NCERT": ["textbooks on a desk", "school textbooks", "textbook"],
        "textbook": ["textbooks on a desk", "student studying textbook", "textbook"],
        "book": ["textbooks on a desk", "library bookshelves", "textbook"],
        "notebook": ["student writing notes", "notebook", "writing"],
        "notes": ["student writing notes", "hand writing", "notebook"],
        "syllabus": ["textbooks on a desk", "library bookshelves"],
        "exam paper": ["written examination", "exam answer sheet", "examination answer booklet"],
        "essay paper": ["student writing an exam", "written examination"],
        "GS papers": ["written examination", "exam answer sheet"],
        "Prelims": ["examination answer booklet"],
        "CSAT": ["examination answer booklet"],
        "library": ["library bookshelves", "library"],
        "hospital": ["hospital ward patient", "doctor examining patient", "hospital"],
        "protective clothing": ["doctor wearing protective gown", "hospital protective suit", "ppe"],
        "face mask": ["doctor surgical mask", "n95 mask"],
        "n95 mask": ["n95 respirator", "doctor wearing mask"],
        "YouTube": ["smartphone opening youtube", "youtube app on a phone", "youtube"],
        "study process": ["fountain pen writing", "student writing notes", "pen"],
        "studying": ["student studying at a desk", "student writing notes", "studying"],
        "students making mistakes": ["student checking a marked paper", "student studying"],
        "lecture": ["lecture on a screen", "classroom lecture"],
        "pen": ["fountain pen writing", "hand writing", "pen"],
        "COVID": ["hospital ward", "doctor protective clothing"],
    }
    queries = list(table.get(normalized) or [query])
    if query not in queries:
        queries.append(query)
    return queries[:4]
