"""Spoken intervals that can take B-roll. Finding an interval does not render a clip."""

from __future__ import annotations

import re

from assets.concepts import QUERIES, denies, query_variants, visual_queries
from transcription.captions import prepare_segment
from transcription.romanize import transliterate


def broll_opportunities(transcript: dict) -> list[dict]:
    """One row per skipped term, and one row per non-overlapping spoken window."""
    segments = transcript.get("segments") or []
    language = transcript.get("language") or "und"
    speech = " ".join(_line(segment, language) for segment in segments)
    rows: list[dict] = []
    for item in visual_queries(speech):
        if item["action"] == "skip":
            rows.append(
                {
                    "term": item["term"],
                    "action": "skip",
                    "reason": item["reason"],
                    "windows": [],
                }
            )
            continue
        for start, end, sentence in _windows(segments, item["term"]):
            rows.append(
                {
                    "term": item["term"],
                    "query": item["query"],
                    "variants": query_variants(item["query"]),
                    "action": "search",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "sentence": sentence,
                    "negation": denies(sentence, item["term"]),
                }
            )
    return _separate(rows)


def lesson_coverage(intervals: list[tuple[float, float]], lesson: float) -> float:
    """Visible seconds inside the lesson. Time past the lesson, including an outro, is dropped."""
    clipped = []
    for start, end in intervals:
        start = max(0.0, float(start))
        end = min(float(lesson), float(end))
        if end > start:
            clipped.append((start, end))
    return coverage_seconds(clipped)


def coverage_seconds(intervals: list[tuple[float, float]]) -> float:
    """Union of intervals. Overlaps are counted once."""
    pieces = sorted((float(start), float(end)) for start, end in intervals if end > start)
    if not pieces:
        return 0.0
    used = 0.0
    cursor_start, cursor_end = pieces[0]
    for start, end in pieces[1:]:
        if start <= cursor_end:
            cursor_end = max(cursor_end, end)
            continue
        used += cursor_end - cursor_start
        cursor_start, cursor_end = start, end
    used += cursor_end - cursor_start
    return round(used, 3)


def _line(segment: dict, language: str) -> str:
    prepared = prepare_segment(segment, language)
    return prepared["romanized_text"] or prepared["original_text"] or ""


def _windows(segments: list[dict], term: str) -> list[tuple[float, float, str]]:
    hits: list[tuple[float, float, dict]] = []
    for segment in segments:
        for word in segment.get("words") or []:
            shown = transliterate(str(word.get("text") or ""))
            if not re.search(rf"\b{re.escape(term)}\b", shown.lower()):
                continue
            start = word.get("start")
            end = word.get("end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            hits.append((float(start), float(end), segment))
    if not hits:
        return []
    clusters: list[list[tuple[float, float, dict]]] = [[hits[0]]]
    for hit in hits[1:]:
        if hit[0] - clusters[-1][-1][1] <= 8:
            clusters[-1].append(hit)
            continue
        clusters.append([hit])
    windows = []
    ordered = sorted(segments, key=lambda item: item["start"])
    for cluster in clusters:
        start = cluster[0][0]
        segment = cluster[-1][2]
        end = float(segment["end"])
        if end - start < 2 and not _other_subject(segment, term, start):
            start = float(segment["start"])
        if end - start < 2:
            index = ordered.index(segment)
            if index + 1 < len(ordered) and float(ordered[index + 1]["start"]) - end <= 1:
                end = float(ordered[index + 1]["end"])
        if end - start < 2:
            continue
        if term in {"book", "kitaab", "kitab", "textbook"}:
            index = ordered.index(segment)
            if index > 0:
                previous = ordered[index - 1]
                gap = float(segment["start"]) - float(previous["end"])
                earlier = transliterate(
                    str(previous.get("text") or previous.get("original_text") or "")
                ).lower()
                if gap <= 1.2 and any(word in earlier for word in ("syllabus", "exam")):
                    start = min(start, float(previous["start"]))
        sentence = " ".join(
            str(item.get("original_text") or item.get("text") or "")
            for item in ordered
            if float(item["end"]) > start and float(item["start"]) < end
        )
        windows.append((start, end, transliterate(sentence)))
    return windows


def _other_subject(segment: dict, term: str, before: float) -> bool:
    """True when an earlier word in this sentence names a different searchable subject."""
    for word in segment.get("words") or []:
        stamp = word.get("start")
        if not isinstance(stamp, (int, float)) or float(stamp) >= before:
            continue
        shown = transliterate(str(word.get("text") or "")).lower()
        for other in QUERIES:
            if other == term:
                continue
            if re.search(rf"\b{re.escape(other)}\b", shown):
                return True
    return False


def _separate(rows: list[dict]) -> list[dict]:
    """Keep skips, and trim later search windows so two clips do not cover the same second."""
    skips = [row for row in rows if row["action"] != "search"]
    chosen = sorted((row for row in rows if row["action"] == "search"), key=lambda item: item["start"])
    kept: list[dict] = []
    for row in chosen:
        current = dict(row)
        if kept and float(current["start"]) < float(kept[-1]["end"]):
            earlier = kept[-1]
            overlap = float(earlier["end"]) - float(current["start"])
            later_length = float(current["end"]) - float(current["start"])
            if later_length > 0 and overlap >= later_length * 0.5:
                continue
            earlier["end"] = round(float(current["start"]), 3)
            if float(earlier["end"]) - float(earlier["start"]) < 2:
                kept.pop()
        if float(current["end"]) - float(current["start"]) < 2:
            continue
        if any(row["query"] == "medical protective clothing" and float(row["start"]) <= float(current["start"]) and float(current["end"]) <= float(row["end"]) + 0.2 for row in kept):
            if current.get("query") == "surgical mask":
                continue
        kept.append(current)
    return skips + kept
