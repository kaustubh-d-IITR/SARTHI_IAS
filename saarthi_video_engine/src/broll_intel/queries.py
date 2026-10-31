"""Several retrieval queries for one visual brief. A single noun is not a query."""

from __future__ import annotations


def build_queries(classified: dict) -> list[str]:
    subject = classified.get("primary_subject") or ""
    action = classified.get("action") or ""
    overlay = classified.get("overlay_type") or ""
    queries: list[str] = []
    if subject == "newspaper" and overlay == "negation_cross":
        queries = [
            "printed newspaper lying unopened on a desk",
            "close view of a newspaper",
            "person holding a newspaper",
        ]
    elif subject == "newspaper":
        queries = [
            "person reading a printed newspaper",
            "hands opening a newspaper",
            "newspaper close up",
        ]
    elif subject == "NCERT" and classified.get("negation"):
        queries = [
            "textbooks closed on a desk",
            "stack of school textbooks",
            "student beside an unopened textbook",
        ]
    elif subject in {"NCERT", "book", "textbook"}:
        queries = [
            "student reading a textbook at a desk",
            "open textbook and notes",
            "person studying with books",
        ]
    elif subject == "YouTube":
        queries = [
            "hand tapping a video app on a smartphone",
            "person opening a video app on a phone",
            "student watching a video on a mobile phone",
        ]
    elif subject in {"hospital", "COVID"}:
        queries = [
            "doctor examining a patient in a hospital room",
            "clinicians and medical equipment in a hospital ward",
            "hospital staff caring for a patient",
        ]
    elif subject == "protective clothing":
        queries = [
            "clinician putting on a protective gown",
            "medical gloves and gown close view",
            "hospital staff wearing protective equipment",
        ]
    elif subject == "studying":
        queries = [
            "student studying at a desk without a written plan",
            "scattered notes and a pen on a desk",
            "person preparing for an exam at a desk",
        ]
    elif action in {"studying", "writing"} or subject in {"study process", "exam paper", "GS papers"}:
        queries = [
            "student writing notes at a desk",
            "person filling an examination paper",
            "fountain pen writing on paper",
        ]
    elif subject:
        for item in classified.get("subjects") or []:
            if item.get("normalized") == subject:
                queries.extend(str(query) for query in (item.get("queries") or [])[:3])
                break
    kept: list[str] = []
    for query in queries:
        text = " ".join(query.split())
        if text and text not in kept:
            kept.append(text)
    return kept[:4]
