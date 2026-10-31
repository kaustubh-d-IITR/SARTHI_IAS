"""System instruction for visual-search descriptions, and the deterministic fallback.

No chat model is configured in this project. ElevenLabs is speech-to-text.
CLIP ranks frames. This module still validates a structured description and
expands queries when the remote call is absent or fails.
"""

from __future__ import annotations

import os

SYSTEM_INSTRUCTION = """
You describe footage for one spoken sentence of an educational short.
The sentence may be Hindi, Romanized Hindi, Hinglish, or English.
Do not translate the stored transcript and do not change its timestamps.

For the sentence, identify all meaningful named entities, people, relationships, places, physical objects, books, food, tools, and activities.
Suggest a specific scene, not a single keyword. Match the subject, the action, and the context together.
A person reading a newspaper is not a printing press. A phone opening YouTube is not a document on a computer.
When the sentence denies an action, still search the concrete object, and say that the action is negated.
Do not treat every negative sentence as the same graphic.
Return several short search queries. Do not assert that generic stock footage
is a real named person, relative, event, or institution.
Keep the sentence text and the start and end times you were given.
Reply with one JSON object: sentence, start, end, subjects[{normalized, category,
scene, queries, identity_claim}]. identity_claim is false unless the file is
known to depict that exact entity.
""".strip()

REQUIRED_SUBJECT_KEYS = ("normalized", "category", "queries")


def llm_configured() -> bool:
    """True only when an operator has pointed at a chat endpoint. None is set here."""
    return bool((os.environ.get("SAARTHI_LLM_URL") or "").strip())


def expand(sentence: str, subjects: list[dict]) -> dict:
    """Queries for one sentence. Remote failure, or no endpoint, uses the dictionaries."""
    if llm_configured():
        try:
            remote = _remote(sentence, subjects)
            errors = validate_description(remote)
            if not errors:
                remote["source"] = "remote"
                return remote
        except Exception:
            pass
    return {
        "sentence": sentence,
        "subjects": [_local(item) for item in subjects],
        "source": "deterministic",
        "instruction": "subject_rules dictionaries; no chat model is configured",
    }


def validate_description(payload: dict) -> list[str]:
    """Reject a model payload that drops the sentence or a query list."""
    errors = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    if not str(payload.get("sentence") or "").strip():
        errors.append("sentence is missing")
    subjects = payload.get("subjects")
    if not isinstance(subjects, list):
        return errors + ["subjects is not a list"]
    for index, item in enumerate(subjects):
        if not isinstance(item, dict):
            errors.append(f"subject {index} is not an object")
            continue
        for key in REQUIRED_SUBJECT_KEYS:
            if key not in item:
                errors.append(f"subject {index} lacks {key}")
        queries = item.get("queries")
        if isinstance(queries, list) and not queries:
            errors.append(f"subject {index} has no queries")
        if item.get("identity_claim") is True:
            errors.append(f"subject {index} claims a generic clip is a named entity")
    return errors


def _local(item: dict) -> dict:
    queries = list(item.get("queries") or [])
    if item.get("query") and item["query"] not in queries:
        queries.append(item["query"])
    return {
        "normalized": item.get("normalized"),
        "category": item.get("category"),
        "scene": item.get("query") or "",
        "queries": queries[:4],
        "identity_claim": False,
        "schedule": item.get("schedule") or "log_only",
    }


def _remote(sentence: str, subjects: list[dict]) -> dict:
    """The endpoint is optional. This build does not call it unless SAARTHI_LLM_URL is set.

    The body would be the system instruction plus the sentence and timestamps.
    A missing or failed call must not discard the dictionary queries.
    """
    raise RuntimeError("no chat client is wired")
