"""Several search phrases and one picture description for a spoken subject.

The stored transcript is not rewritten. A denial looks for the object itself.
An instruction to read looks for a person reading. A hospital line looks for
care inside a room, not the front of a building.
"""

from __future__ import annotations

from assets.concepts import denies

# (query, negation) -> search phrases. The first phrase is the one Commons is asked first.
_SEARCH: dict[tuple[str, bool], tuple[str, ...]] = {
    ("newspaper", True): ("stack of newspapers", "newspapers on a table", "printed newspaper"),
    ("newspaper", False): ("person reading a newspaper", "man reading newspaper", "open newspaper"),
    ("textbook", True): ("textbooks on a desk", "stack of textbooks", "school textbooks"),
    ("textbook", False): ("student studying with textbooks", "person reading a book", "open textbook"),
    ("hospital ward", False): ("doctor examining patient", "hospital room patient", "hospital ward"),
    ("medical protective clothing", False): (
        "doctor wearing protective gown",
        "clinician in hospital gown",
        "personal protective equipment hospital",
    ),
    ("surgical mask", False): ("clinician wearing mask", "doctor surgical mask hospital",),
}

_PROMPT: dict[tuple[str, bool], str] = {
    ("newspaper", True): "a close view of printed newspapers, not a printing press",
    ("newspaper", False): "a person reading a large printed newspaper",
    ("textbook", True): "school textbooks filling a desk",
    ("textbook", False): "a student studying with open textbooks at a desk",
    ("hospital ward", False): "a doctor examining a patient in a hospital room",
    ("medical protective clothing", False): "a clinician in a protective gown inside a hospital room",
    ("surgical mask", False): "a clinician wearing a mask in a hospital room",
}

# A specific noun from the scene. Generic words such as "student" are not anchors.
_ANCHORS = {
    "newspaper": ("newspaper", "newspapers"),
    "textbook": ("textbook", "textbooks", "books", "book"),
    "textbooks on a desk": ("textbook", "textbooks", "books"),
    "hospital ward": ("hospital", "patient", "clinic", "ward"),
    "medical protective clothing": ("protective", "gown", "ppe"),
    "surgical mask": ("mask", "respirator", "n95"),
    "student writing notes": ("pen", "notebook"),
    "person using a laptop": ("youtube", "phone", "smartphone"),
    "youtube on a phone": ("youtube", "phone", "smartphone"),
    "examination answer booklet": ("exam", "examination", "booklet"),
    "student writing an exam": ("exam", "examination"),
    "student studying at a desk": ("textbook", "notebook"),
    "library bookshelves": ("bookshelf", "bookshelves", "library"),
}

# Description words that mean the picture is the wrong kind of clip for this subject.
_BLOCKS = {
    "newspaper": (
        "president", "parliament", "corruption", "printing", "letterpress", "rotary",
        "tryck", "press", "typesetting", "layout", "ombrytning", "tidning", "wikipedia",
        "conference", "bookshelf", "bookshelves", "shelves",
    ),
    "textbook": ("author", "quote", "lecture"),
    "hospital ward": ("hotel", "airport", "briefing", "interview", "roundtable"),
    "medical protective clothing": ("fashion", "costume party", "briefing", "interview", "roundtable"),
    "surgical mask": ("fashion", "costume party"),
}


def description_block(blob: str, plan: dict) -> str | None:
    """Why a description cannot illustrate this sentence, or None when it may."""
    lowered = blob.lower()
    for word in plan["blocked_words"]:
        if word in lowered:
            return "description is the wrong kind of footage"
    return None


def retrieval_plan(query: str, sentence: str, term: str = "") -> dict:
    """Queries, a visual prompt, and words that disqualify a description."""
    negation = denies(sentence or "", term or query.split()[0])
    key = (query, negation)
    if key not in _SEARCH and (query, False) in _SEARCH:
        key = (query, False)
        negation = False
    phrases = list(_SEARCH.get(key) or (query,))
    return {
        "query": query,
        "negation": negation,
        "phrases": phrases,
        "prompt": _PROMPT.get(key) or phrases[0],
        "anchors": list(_ANCHORS.get(query, (query.split()[0],))),
        "blocked_words": list(_BLOCKS.get(query, ())),
    }
