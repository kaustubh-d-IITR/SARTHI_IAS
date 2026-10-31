"""ElevenLabs Scribe transcription.

The endpoint and model id are the ones documented at
https://elevenlabs.io/docs/api-reference/speech-to-text/convert
POST https://api.elevenlabs.io/v1/speech-to-text
model_id scribe_v2
header xi-api-key

Word times in the response are seconds. This module stores them as seconds,
which is the timeline unit the renderer already uses. logprob is kept as the
provider sent it. A confidence score is not invented from it.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL_ID = "scribe_v2"
# Local review mark only. The API does not define this cutoff.
REVIEW_LOGPROB = -1.0
PAUSE_SECONDS = 0.7
MAX_WORDS = 18
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class ElevenLabsError(RuntimeError):
    pass


def transcribe_file(
    path: Path,
    *,
    api_key: str,
    model_id: str = MODEL_ID,
    language_code: str = "",
    cache_dir: Path | None = None,
    raw_path: Path | None = None,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
    attempts: int = 4,
    timeout: float = 300,
) -> dict:
    """Transcribe one audio or video file. Unchanged inputs are read from cache."""
    key = (api_key or "").strip()
    if not key:
        raise ElevenLabsError(
            "ELEVENLABS_API_KEY is empty. Copy .env.example to .env and paste the key locally."
        )
    source = Path(path)
    if not source.is_file():
        raise ElevenLabsError(f"transcription input was not found: {source}")
    settings = {
        "model_id": model_id,
        "language_code": language_code or "",
        "timestamps_granularity": "word",
        "no_verbatim": "false",
        "tag_audio_events": "false",
    }
    cached = _cache_path(cache_dir, source, settings)
    if cached is not None and cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    payload = _request(source, key, settings, opener=opener, sleep=sleep, attempts=attempts, timeout=timeout)
    _write_raw(raw_path, payload)
    transcript = transcript_from_response(payload, model_id=model_id)
    transcript["source_file"] = source.name
    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    return transcript


def transcript_from_response(payload: dict, *, model_id: str = MODEL_ID) -> dict:
    """Turn a SpeechToTextChunkResponseModel into the engine's segment list."""
    if not isinstance(payload, dict) or "text" not in payload or not isinstance(payload.get("words"), list):
        raise ElevenLabsError("ElevenLabs returned a transcript response without text and words.")
    spoken = [word for word in payload["words"] if isinstance(word, dict) and word.get("type") == "word"]
    events = [word for word in payload["words"] if isinstance(word, dict) and word.get("type") == "audio_event"]
    segments = _group(spoken)
    language = payload.get("language_code") if isinstance(payload.get("language_code"), str) else "und"
    probability = payload.get("language_probability")
    return {
        "language": language or "und",
        "language_source": "provider",
        "language_probability": probability if isinstance(probability, (int, float)) else None,
        "translated_to_english": False,
        "method": "elevenlabs",
        "model": model_id,
        "endpoint": ENDPOINT,
        "provenance": (
            f"ElevenLabs {model_id} via POST {ENDPOINT}. "
            "Word text is the provider string. logprob is stored as sent. "
            "No confidence score was derived."
        ),
        "confidence_note": "The provider supplies logprob per word, not a confidence score. Segment confidence is null.",
        "audio_events": [_event(item) for item in events],
        "segments": segments,
    }


def compare_transcripts(left: dict, right: dict) -> dict:
    """Pair segments by order so a new transcript can be read next to an older one."""
    old = left.get("segments") or []
    new = right.get("segments") or []
    pairs = []
    for index in range(max(len(old), len(new))):
        before = old[index] if index < len(old) else None
        after = new[index] if index < len(new) else None
        pairs.append(
            {
                "index": index,
                "previous": None if before is None else before.get("text"),
                "current": None if after is None else after.get("text"),
                "same_text": bool(before and after and before.get("text") == after.get("text")),
            }
        )
    return {"previous_segments": len(old), "current_segments": len(new), "pairs": pairs}


def _request(source: Path, api_key: str, settings: dict, *, opener, sleep, attempts: int, timeout: float) -> dict:
    body, content_type = _multipart(settings, source)
    delay = 1.0
    last_detail = "no response"
    for attempt in range(attempts):
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "xi-api-key": api_key,
                "Content-Type": content_type,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
            break
        except urllib.error.HTTPError as error:
            detail = _safe_body(error, api_key)
            last_detail = f"HTTP {error.code}"
            if error.code not in TRANSIENT_STATUS or attempt == attempts - 1:
                raise ElevenLabsError(f"ElevenLabs transcription failed: HTTP {error.code}. {detail}") from None
            sleep(delay)
            delay = min(delay * 2, 8.0)
        except urllib.error.URLError as error:
            last_detail = "connection error"
            if attempt == attempts - 1:
                raise ElevenLabsError("ElevenLabs transcription failed: the connection did not complete.") from None
            sleep(delay)
            delay = min(delay * 2, 8.0)
    else:
        raise ElevenLabsError(f"ElevenLabs transcription failed: {last_detail}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ElevenLabsError("ElevenLabs returned a response that was not JSON.") from None
    if isinstance(payload, dict) and payload.get("transcripts"):
        raise ElevenLabsError(
            "ElevenLabs returned a multichannel transcript. This pipeline sends one mixed track."
        )
    return payload


def _multipart(fields: dict, source: Path) -> tuple[bytes, str]:
    boundary = "----SaarthiElevenLabs"
    parts: list[bytes] = []
    for key, value in fields.items():
        if value == "":
            continue
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    filename = source.name.replace('"', "")
    header = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    )
    parts.append(header.encode("utf-8"))
    parts.append(source.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _group(words: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        start = _seconds(word.get("start"))
        end = _seconds(word.get("end"))
        item = {
            "text": text,
            "start": start,
            "end": end,
            "logprob": word.get("logprob") if isinstance(word.get("logprob"), (int, float)) else None,
            "review": bool(isinstance(word.get("logprob"), (int, float)) and word["logprob"] < REVIEW_LOGPROB),
        }
        if item["start"] is None or item["end"] is None:
            item["review"] = True
        if current and start is not None and current[-1]["end"] is not None and start - current[-1]["end"] > PAUSE_SECONDS:
            groups.append(current)
            current = []
        current.append(item)
        if len(current) >= MAX_WORDS:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    segments = []
    for index, group in enumerate(groups):
        timed = [item for item in group if item["start"] is not None and item["end"] is not None]
        if not timed:
            continue
        flags = []
        if any(item["review"] for item in group):
            flags.append("low_logprob_or_missing_time")
        segments.append(
            {
                "id": f"a{index}",
                "text": " ".join(item["text"] for item in group),
                "original_text": " ".join(item["text"] for item in group),
                "start": timed[0]["start"],
                "end": timed[-1]["end"],
                "confidence": None,
                "timing_precision": "word" if len(timed) == len(group) else "segment",
                "words": [
                    {
                        "text": item["text"],
                        "start": item["start"],
                        "end": item["end"],
                        "logprob": item["logprob"],
                    }
                    for item in group
                ],
                "review_flags": flags,
            }
        )
    return segments


def _event(word: dict) -> dict:
    return {
        "text": word.get("text"),
        "start": _seconds(word.get("start")),
        "end": _seconds(word.get("end")),
        "type": "audio_event",
    }


def _seconds(value) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value), 3)


def _cache_path(cache_dir: Path | None, source: Path, settings: dict) -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8"))
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return Path(cache_dir) / f"{digest.hexdigest()}.json"


def _write_raw(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _safe_body(error: urllib.error.HTTPError, api_key: str) -> str:
    try:
        text = error.read().decode("utf-8", "replace")
    except Exception:
        text = ""
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return text[:400]
