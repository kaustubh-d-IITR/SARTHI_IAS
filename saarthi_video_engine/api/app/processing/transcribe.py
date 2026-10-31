"""Hash-cached transcription. The provider is called only when the file hash misses."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import ENGINE_ROOT


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_error(exc: BaseException) -> str:
    text = str(exc)
    if "ELEVENLABS_API_KEY" in text:
        return "ELEVENLABS_API_KEY is not configured."
    lowered = text.lower()
    if "://" in text or ":\\" in text or "\\\\" in text or "/users/" in lowered or "/desktop/" in lowered:
        return "Transcription failed."
    return text[:300] or "Transcription failed."


def transcribe_upload(path: Path, cache_dir: Path, provider) -> dict:
    """Reuse a cached transcript only when the source bytes still match."""
    digest = file_sha256(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{digest}.json"
    if cached.is_file():
        saved = json.loads(cached.read_text(encoding="utf-8"))
        if saved.get("source_sha256") == digest and saved.get("segments"):
            saved["reused"] = True
            return saved
    try:
        transcript = provider(path)
    except Exception as exc:
        raise RuntimeError(_public_error(exc)) from exc
    if not isinstance(transcript, dict) or not transcript.get("segments"):
        raise RuntimeError("Transcription returned no speech segments.")
    transcript["source_sha256"] = digest
    transcript["reused"] = False
    transcript["processed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    transcript.pop("source_file", None)
    cached.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    return transcript


def elevenlabs_provider(path: Path) -> dict:
    """Call the existing Scribe client. Not used at import or startup."""
    import sys

    src = ENGINE_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from config.env import require_elevenlabs_key
    from transcription.elevenlabs import transcribe_file

    return transcribe_file(path, api_key=require_elevenlabs_key(), cache_dir=None)
