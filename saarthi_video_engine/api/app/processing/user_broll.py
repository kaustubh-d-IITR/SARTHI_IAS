"""User-supplied B-roll catalogue and description matching. No Commons calls."""

from __future__ import annotations

import re
from pathlib import Path

from app.processing.transcribe import file_sha256

_STOP = {
    "with", "from", "that", "this", "into", "during", "their", "there", "about",
    "your", "have", "been", "will", "just", "they", "them", "then", "than",
    "what", "when", "where", "which", "while", "over", "under", "after", "before",
}
_ACTIONS = ("watching", "writing", "entering", "walking", "opening", "reading", "speaking")
_TOKEN = re.compile(r"[A-Za-z]{4,}")


def content_tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(text or "") if token.lower() not in _STOP]


def analyse_description(text: str) -> dict:
    """Facts taken from the supplied sentence. Nothing is added from outside it."""
    tokens = content_tokens(text)
    lowered = (text or "").lower()
    actions = [word for word in _ACTIONS if word in lowered]
    return {
        "text": " ".join((text or "").split()),
        "main_subject": tokens[0] if tokens else "",
        "action": actions[0] if actions else "",
        "objects": tokens,
        "concepts": tokens,
        "phrases": [" ".join((text or "").split())] if text and text.strip() else [],
    }


def _usable(meta: dict, path: Path) -> tuple[float | None, float | None]:
    """Container duration and the picture's own duration (probed now for uploads stored before it was recorded)."""
    container = meta.get("duration_seconds")
    picture = meta.get("video_duration_seconds")
    if picture is None:
        try:
            from app.media_probe import probe_video

            picture = probe_video(path).get("video_duration_seconds")
        except Exception:  # noqa: BLE001 - the stored duration still applies
            picture = None
    values = [float(v) for v in (container, picture) if isinstance(v, (int, float)) and float(v) > 0]
    return (float(container) if isinstance(container, (int, float)) else None), (min(values) if values else None)


def catalogue_asset(meta: dict, path: Path) -> dict:
    description = analyse_description(meta.get("description") or "")
    container, usable = _usable(meta, path)
    return {
        "asset_id": meta["upload_id"],
        "filename": meta.get("filename") or "clip",
        "stored_name": meta.get("stored_name") or path.name,
        "description": description["text"],
        "user_description": meta.get("description") or "",
        "analysis": description,
        "duration_seconds": usable,
        "media_duration_seconds": container,
        "width": meta.get("width"),
        "height": meta.get("height"),
        "frame_rate": meta.get("frame_rate"),
        "codec": meta.get("codec") or "",
        "size_bytes": meta.get("size_bytes"),
        "validation_status": meta.get("validation_status") or "valid",
        "sha256": file_sha256(path),
        "thumbnail": None,
        "visual_analysis": "unavailable",
    }


def _score(description: str, speech: str) -> float:
    described = content_tokens(description)
    spoken = set(content_tokens(speech))
    if not described or not spoken:
        return 0.0
    shared = [token for token in described if token in spoken]
    if not shared:
        return 0.0
    coverage = len(shared) / len(described)
    if len(shared) >= 2:
        coverage = max(coverage, 0.5)
    return round(min(1.0, coverage), 3)


def match_broll(transcript: dict, assets: list[dict]) -> tuple[list[dict], list[dict]]:
    """Greedy one-clip assignment. A weak overlap stays on the teacher."""
    segments = list(transcript.get("segments") or [])
    proposals = []
    for asset in assets:
        for segment in segments:
            speech = segment.get("original_text") or segment.get("text") or ""
            score = _score(asset.get("description") or "", speech)
            if score < 0.5:
                continue
            start = float(segment["start"])
            end = float(segment["end"])
            duration = asset.get("duration_seconds")
            if end - start < 2.0:
                continue
            if not isinstance(duration, (int, float)) or duration <= 0:
                continue
            play = min(float(duration), end - start)
            if play < 2.0:
                continue
            proposals.append(
                {
                    "asset_id": asset["asset_id"],
                    "filename": asset["filename"],
                    "description": asset["description"],
                    "segment_ids": [segment.get("id")],
                    "speech_text": speech,
                    "speech_start": round(start, 3),
                    "speech_end": round(start + play, 3),
                    "source_in": 0.0,
                    "source_out": round(play, 3),
                    "score": score,
                    "explanation": (
                        "The description shares words with this speech: "
                        f"{str(asset['description']).rstrip('.')}."
                        " Visual frames were not analysed."
                    ),
                    "evidence": "description",
                    "approval": "approved",
                    "origin": "user",
                    "duration_seconds": float(duration),
                    "clip_duration": float(duration),
                }
            )
    proposals.sort(key=lambda item: (-item["score"], item["speech_start"], item["asset_id"]))
    chosen = []
    used_assets: set[str] = set()
    used_segments: set[str] = set()
    covered_until = -1.0
    for item in proposals:
        segment_id = item["segment_ids"][0]
        if item["asset_id"] in used_assets or segment_id in used_segments:
            continue
        if item["speech_start"] < covered_until:
            continue
        chosen.append(item)
        used_assets.add(item["asset_id"])
        used_segments.add(segment_id)
        covered_until = item["speech_end"]
    chosen.sort(key=lambda item: item["speech_start"])
    return chosen, proposals
