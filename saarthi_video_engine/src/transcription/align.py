"""Attach an English translation pass onto an original-language transcript.

Alignment is time overlap. Missing overlap stays an empty English string.
Nothing is copied from the original into English when the languages differ.
"""

from __future__ import annotations


def attach_english(transcript: dict, translated_segments: list[dict]) -> dict:
    updated = dict(transcript)
    updated["segments"] = []
    updated["translated_to_english"] = True
    note = " English captions come from a separate whisper.cpp -tr pass, aligned by timestamp overlap."
    updated["provenance"] = (transcript.get("provenance") or "") + note
    for segment in transcript["segments"]:
        item = dict(segment)
        match = _best_overlap(segment, translated_segments)
        item["english_text"] = match if match else ""
        updated["segments"].append(item)
    return updated


def _best_overlap(segment: dict, translated_segments: list[dict]) -> str:
    best_text = ""
    best_overlap = 0.0
    for other in translated_segments:
        overlap = min(segment["end"], other["end"]) - max(segment["start"], other["start"])
        if overlap > best_overlap:
            text = (other.get("original_text") or other.get("text") or "").strip()
            if text:
                best_text = text
                best_overlap = overlap
    if best_overlap < 0.3:
        return ""
    return best_text
