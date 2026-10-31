"""Display names only. User text never becomes a filesystem path."""

from __future__ import annotations

from pathlib import Path

from app.config import ALLOWED_EXTENSIONS
from app.errors import StudioError


def display_filename(raw: str | None) -> str:
    name = Path(str(raw or "")).name.replace("\\", " ").replace("/", " ")
    cleaned = "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in name)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return (cleaned[:180] or "video")


def require_extension(raw: str | None) -> str:
    suffix = Path(str(raw or "")).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(ext.lstrip(".") for ext in ALLOWED_EXTENSIONS)
        raise StudioError(
            415,
            "unsupported_type",
            f"Use a video file ending in {allowed}.",
        )
    return suffix


def clean_description(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise StudioError(
            422,
            "description_required",
            "Each B-roll clip needs a one-line description.",
        )
    if len(text) > 500:
        raise StudioError(
            422,
            "description_too_long",
            "Keep the description to 500 characters or fewer.",
        )
    return text
