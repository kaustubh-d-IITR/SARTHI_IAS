"""Find one instrumental Commons audio file, or report that none is available."""

from __future__ import annotations

from pathlib import Path

from .commons import AssetRejected
from .video_commons import accept_video_candidate, download_video

AUDIO_MIMES = {
    "audio/ogg",
    "audio/vorbis",
    "application/ogg",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/mp4",
}
INSTRUMENTAL_MARKS = ("instrumental", "piano", "ambient", "orchestra", "strings", "no vocal", "no vocals")
VOCAL_MARKS = ("vocal", "lyrics", "interview", "singing", "podcast")


def search_instrumentals(query: str, limit: int = 8) -> list[dict]:
    """Reuse the Commons search client with an audio query instead of video."""
    from .video_commons import _search

    return _search(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:audio",
            "gsrnamespace": "6",
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        }
    )


def accept_music_candidate(candidate: dict) -> str | None:
    reason = _license_and_url(candidate)
    if reason:
        return reason
    mime = (candidate.get("mime") or "").lower()
    if mime not in AUDIO_MIMES:
        return f"mime {candidate.get('mime')} is not audio"
    size = candidate.get("size") or 0
    if not isinstance(size, int) or size < 20_000:
        return "audio size is missing or too small"
    if size > 15_000_000:
        return "audio is over 15 MB"
    haystack = f"{candidate.get('title') or ''} {candidate.get('description') or ''}".lower()
    if any(mark in haystack for mark in VOCAL_MARKS) and "instrumental" not in haystack and "no vocal" not in haystack:
        return "title or description indicates vocals or speech"
    if not any(mark in haystack for mark in INSTRUMENTAL_MARKS):
        return "title does not say the track is instrumental"
    return None


def download_audio(url: str, destination: Path, max_bytes: int = 15_000_000) -> Path:
    try:
        return download_video(url, destination, max_bytes=max_bytes)
    except AssetRejected as error:
        raise AssetRejected(str(error).replace("video", "audio")) from error


def _license_and_url(candidate: dict) -> str | None:
    """Same license gate as video, without the video mime check."""
    probe = dict(candidate)
    probe["mime"] = "video/mp4"
    probe["size"] = 100_000
    reason = accept_video_candidate(probe, query="instrumental", speech="instrumental")
    if reason and reason.startswith("description"):
        return None
    if reason and "video" in reason:
        return None
    return reason

