"""Editorial examples taken from the reference Shorts.

A cutaway is a timeline interval the dataset marks hidden_by_broll.
A teacher interval is one marked visible_primary with no B-roll asset.
Partial records are not examples. Spoken wording is copied, not rewritten.
"""

from __future__ import annotations

import re

from broll_intel.director import analyze
from dataset_adapter.loader import is_trusted
from transcription.romanize import transliterate

EXAMPLE_VERSION = "phase20.1"
_TOKEN = re.compile(r"[a-z0-9]{4,}")
_STOP = {
    "that", "this", "with", "from", "have", "will", "just", "been", "were",
    "your", "they", "them", "then", "than", "into", "over", "also", "only",
    "very", "more", "some", "such", "there", "their", "about", "would",
    "could", "should", "because", "after", "before", "during", "still",
    "even", "like", "okay", "right", "what", "when", "where", "which",
    "hain", "nahi", "nahin", "mein", "main", "kare", "karo", "kiya",
}


def examples_from_dataset(dataset: dict) -> list[dict]:
    """One example per observed timeline interval on a trusted Short."""
    rows: list[dict] = []
    for record in dataset.get("shorts") or []:
        if not is_trusted(record):
            continue
        segments = {item.get("id"): item for item in record.get("broll_segments") or []}
        for interval in record.get("timeline") or []:
            example = _example(record, interval, segments)
            if example is not None:
                rows.append(example)
    return rows


def retrieve_examples(text: str, examples: list[dict], limit: int = 4, exclude_ids: set[str] | None = None) -> list[dict]:
    """Token overlap with the reference phrase and the described picture. Not embeddings."""
    mine = _tokens(text)
    if not mine:
        return []
    blocked = exclude_ids or set()
    ranked = []
    for example in examples:
        if example["reference_id"] in blocked:
            continue
        shared = mine & example["tokens"]
        if not shared:
            continue
        score = len(shared) / len(mine | example["tokens"])
        ranked.append((score, shared, example))
    ranked.sort(key=lambda item: item[0], reverse=True)
    hits = []
    for score, shared, example in ranked[:limit]:
        hits.append(
            {
                "reference_id": example["reference_id"],
                "score": round(score, 4),
                "shared_tokens": sorted(shared),
                "replaced_teacher": example["replaced_teacher"],
                "spoken_phrase": example["spoken_phrase"],
                "visible": example["visible"],
                "relation_to_speech": example["relation_to_speech"],
                "duration_seconds": example["duration_seconds"],
                "confidence": example["confidence"],
            }
        )
    return hits


def choose_cutaway(text: str, start: float, end: float, examples: list[dict], exclude_ids: set[str] | None = None) -> dict:
    """Editorial read and visual feasibility stay separate. The spoken line is not edited."""
    grounded = analyze(text, start, end)
    hits = retrieve_examples(text, examples, limit=6, exclude_ids=exclude_ids)
    positives = [hit for hit in hits if hit["replaced_teacher"]]
    negatives = [hit for hit in hits if not hit["replaced_teacher"]]
    best_pos = positives[0]["score"] if positives else 0.0
    best_neg = negatives[0]["score"] if negatives else 0.0
    feasible = grounded["visual_type"] != "none"
    resembles = bool(feasible and positives and best_pos > best_neg)
    if not feasible:
        reason = "The sentence has no depictable action, so the teacher stays."
    elif not hits:
        reason = "No trusted reference interval shares the phrase's content words."
    elif not positives:
        reason = "The closest reference intervals kept the teacher on screen."
    elif best_pos <= best_neg:
        reason = "A reference interval that kept the teacher is at least as close as any cutaway."
    else:
        reason = (
            f"Closer to {positives[0]['reference_id']} "
            f"({positives[0]['relation_to_speech']}, {positives[0]['duration_seconds']}s) "
            f"than to a teacher-only interval."
        )
    return {
        "spoken_text": text,
        "speech_start": start,
        "speech_end": end,
        "select_cutaway": resembles,
        "uncertainty": "low" if resembles or (hits and not feasible) else "high",
        "decision_reason": reason,
        "editorial": {
            "reference_support": resembles,
            "closest_cutaway_score": best_pos,
            "closest_teacher_score": best_neg,
            "examples": hits[:4],
        },
        "feasibility": {
            "visual_type": grounded["visual_type"],
            "object": grounded["object"],
            "action": grounded["action"],
            "negation": grounded["negation"],
            "visual_brief": grounded["visual_brief"],
            "required_visual_elements": grounded["required_visual_elements"],
            "forbidden_visual_elements": grounded["forbidden_visual_elements"],
            "search_queries": grounded["search_queries"],
            "overlay_instructions": grounded["overlay_instructions"],
            "preferred_duration": grounded["preferred_duration"],
        },
    }


def holdout_counts(dataset: dict, holdout_size: int = 8) -> dict:
    """Predict held-out intervals from the other trusted Shorts. Same-short examples are excluded."""
    examples = examples_from_dataset(dataset)
    ids = sorted({row["reference_id"] for row in examples})
    held = set(ids[-holdout_size:])
    train = [row for row in examples if row["reference_id"] not in held]
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "holdout_ids": sorted(held)}
    for row in examples:
        if row["reference_id"] not in held or not row["spoken_phrase"]:
            continue
        decision = choose_cutaway(
            row["spoken_phrase"],
            row["start"],
            row["end"],
            train,
            exclude_ids={row["reference_id"]},
        )
        predicted = decision["select_cutaway"]
        actual = row["replaced_teacher"]
        if predicted and actual:
            counts["tp"] += 1
        elif predicted and not actual:
            counts["fp"] += 1
        elif actual:
            counts["fn"] += 1
        else:
            counts["tn"] += 1
    precision = _rate(counts["tp"], counts["tp"] + counts["fp"])
    recall = _rate(counts["tp"], counts["tp"] + counts["fn"])
    counts["precision"] = precision
    counts["recall"] = recall
    counts["note"] = (
        "Scores are token overlap between spoken phrases. "
        "A low recall means many reference cutaways do not share content words with other Shorts."
    )
    return counts


def _example(record: dict, interval: dict, segments: dict) -> dict | None:
    visibility = interval.get("teacher_visibility") or ""
    asset_id = interval.get("broll_or_asset")
    if visibility == "hidden_by_broll" and asset_id:
        replaced = True
    elif visibility == "visible_primary" and not asset_id:
        replaced = False
    else:
        return None
    spoken = str(interval.get("spoken_wording") or "").strip()
    if not spoken:
        return None
    start = float(interval.get("start") or 0)
    end = float(interval.get("end") or start)
    detail = segments.get(asset_id) or {}
    visible = str(detail.get("visible") or interval.get("visual_content") or "")
    confidence = str(interval.get("confidence") or "")
    complete = bool(spoken and end > start and confidence == "observed")
    return {
        "version": EXAMPLE_VERSION,
        "reference_id": record["reference_id"],
        "start": start,
        "end": end,
        "duration_seconds": round(max(0.0, end - start), 3),
        "spoken_phrase": spoken,
        "replaced_teacher": replaced,
        "visual_treatment": "cutaway" if replaced else "teacher",
        "visible": visible,
        "relation_to_speech": str(detail.get("relation_to_speech") or ("none" if not replaced else "unspecified")),
        "asset_type": str(detail.get("asset_type") or ""),
        "placement": str(detail.get("placement") or ("replaces_card" if replaced else "teacher")),
        "evidence": str(interval.get("evidence") or ""),
        "confidence": confidence,
        "complete": complete,
        "safe_to_learn_from": complete,
        "tokens": _tokens(f"{spoken} {visible}"),
    }


def _tokens(text: str) -> set[str]:
    roman = transliterate(text).lower()
    return {token for token in _TOKEN.findall(roman) if token not in _STOP}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
