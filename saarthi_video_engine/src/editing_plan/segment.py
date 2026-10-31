"""Pick a 30–60 second run of real transcript segments."""

from __future__ import annotations


def choose_segment(segments: list[dict], minimum: float = 30.0, maximum: float = 60.0) -> dict:
    """Choose the densest run that starts and ends on segment boundaries.

    The score is the number of transcript words. A pause of at least 0.35 s
    before the next segment adds a small bonus so the cut prefers a breath,
    not the middle of a clause. No times are invented.
    """
    if not segments:
        raise ValueError("no transcript segments")
    ordered = sorted(segments, key=lambda item: (item["start"], item["end"]))
    best = None
    for start_index, opening in enumerate(ordered):
        bucket = []
        for segment in ordered[start_index:]:
            bucket.append(segment)
            duration = segment["end"] - opening["start"]
            if duration < minimum:
                continue
            if duration > maximum:
                break
            text = " ".join(item["text"] for item in bucket).strip()
            words = [word for word in text.split() if word]
            if len(words) < 20:
                continue
            following = ordered[start_index + len(bucket)] if start_index + len(bucket) < len(ordered) else None
            pause = (following["start"] - segment["end"]) if following else 1.0
            score = len(words) + (8 if pause >= 0.35 else 0)
            candidate = {
                "score": score,
                "source_in": round(opening["start"], 3),
                "source_out": round(segment["end"], 3),
                "duration": round(duration, 3),
                "text": text,
                "pause_after": round(pause, 3),
                "segment_ids": [item["id"] for item in bucket],
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
    if best is None:
        raise ValueError("no 30–60 second run of transcript segments")
    best["reason"] = (
        "Densest 30–60 second run of ASR segments, cut on segment boundaries"
        + (" after a pause." if best["pause_after"] >= 0.35 else ".")
    )
    return best


def shift_segments(segments: list[dict], source_in: float, source_out: float) -> list[dict]:
    kept = []
    for segment in segments:
        if segment["end"] <= source_in or segment["start"] >= source_out:
            continue
        start = max(0.0, segment["start"] - source_in)
        end = min(source_out - source_in, segment["end"] - source_in)
        if end - start < 0.2:
            continue
        item = dict(segment)
        item["start"] = round(start, 3)
        item["end"] = round(end, 3)
        item["text"] = segment.get("original_text") or segment["text"]
        words = []
        for word in segment.get("words") or []:
            word_start = word["start"] - source_in
            word_end = word["end"] - source_in
            if word_end <= 0 or word_start >= (source_out - source_in):
                continue
            copied = dict(word)
            copied["start"] = round(max(0.0, word_start), 3)
            copied["end"] = round(word_end, 3)
            words.append(copied)
        item["words"] = words
        item["timing_precision"] = "word" if words else "segment"
        kept.append(item)
    return kept
