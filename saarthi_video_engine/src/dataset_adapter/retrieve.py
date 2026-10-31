"""Structured retrieval. Token overlap, not an embedding index."""

from __future__ import annotations

import re

from .loader import is_trusted

_TOKEN = re.compile(r"[a-z0-9]+")


def get_record(dataset: dict, reference_id: str) -> dict:
    for record in dataset["shorts"]:
        if record["reference_id"] == reference_id:
            return record
    raise KeyError(reference_id)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def record_text(record: dict) -> str:
    category = record.get("category") or {}
    parts = [
        category.get("primary") or "",
        category.get("main_topic") or "",
        category.get("core_message") or "",
        category.get("emotional_tone") or "",
        category.get("narrative_structure") or "",
        " ".join(category.get("secondary") or []),
        " ".join(category.get("subtopics") or []),
        record.get("layout_group") or "",
        (record.get("visual_style") or {}).get("template_family") or "",
    ]
    return " ".join(parts)


def similarity(query: str, record: dict) -> float:
    """Jaccard overlap between the query and the record's category fields."""
    left = _tokens(query)
    right = _tokens(record_text(record))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def retrieve(
    dataset: dict,
    *,
    category: str | None = None,
    layout_group: str | None = None,
    template_family: str | None = None,
    query: str | None = None,
    trusted_only: bool = True,
    limit: int = 5,
) -> list[dict]:
    """Return ranked hits. Each hit keeps the record id, score, and trust flag.

    trusted_only drops partial records from rule selection. Pass False to inspect
    a partial record's available fields without promoting it into the baseline.
    """
    hits = []
    for record in dataset["shorts"]:
        trusted = is_trusted(record)
        if trusted_only and not trusted:
            continue
        if category and (record.get("category") or {}).get("primary") != category:
            continue
        if layout_group and record.get("layout_group") != layout_group:
            continue
        style = record.get("visual_style") or {}
        if template_family and style.get("template_family") != template_family:
            continue
        score = similarity(query, record) if query else 1.0
        if query and score <= 0:
            continue
        hits.append(
            {
                "reference_id": record["reference_id"],
                "score": round(score, 4),
                "trusted_for_rules": trusted,
                "quality_status": record["quality"]["status"],
                "layout_group": record.get("layout_group"),
                "template_family": style.get("template_family"),
                "primary_category": (record.get("category") or {}).get("primary"),
                "limitations": list((record.get("quality") or {}).get("limitations") or []),
            }
        )
    hits.sort(key=lambda item: (-item["score"], item["reference_id"]))
    return hits[:limit]


def similar(dataset: dict, reference_id: str, limit: int = 5) -> list[dict]:
    record = get_record(dataset, reference_id)
    category = record.get("category") or {}
    query = " ".join(
        [
            category.get("primary") or "",
            category.get("main_topic") or "",
            record.get("layout_group") or "",
        ]
    )
    hits = retrieve(
        dataset,
        layout_group=record.get("layout_group"),
        query=query,
        trusted_only=True,
        limit=limit + 1,
    )
    return [hit for hit in hits if hit["reference_id"] != reference_id][:limit]
