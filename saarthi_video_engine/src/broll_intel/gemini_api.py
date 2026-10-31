"""Gemini text and image checks. Video generation is reported only if a model exists.

The API key is read from the environment and is never written to logs or return values.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError

from config.env import gemini_configured, load_project_env

TEXT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash")
_CLIENT = None


def failure_class(message: str) -> str:
    """retry, quota, or fatal. A 503 is not a semantic rejection."""
    text = message.lower()
    if "resource_exhausted" in text or "quota" in text:
        return "quota"
    if any(token in text for token in ("503", "504", "unavailable", "deadline", "429", "high demand")):
        return "retry"
    if any(token in text for token in ("401", "403", "400", "404", "api key")):
        return "fatal"
    return "fatal"


def backoff_seconds(attempt: int) -> float:
    base = min(30.0, 2.0 * (2**attempt))
    return base + random.uniform(0, 0.4 * base)


class GeminiError(RuntimeError):
    pass


def _key() -> str:
    load_project_env()
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise GeminiError(
            "GEMINI_API_KEY is empty. Add it to saarthi_video_engine/.env. Do not paste it into chat."
        )
    return key


def _redact(message: str) -> str:
    secret = (os.environ.get("GEMINI_API_KEY") or "").strip()
    text = str(message)
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:500]


def _client():
    global _CLIENT
    from google import genai

    if _CLIENT is None:
        from google.genai import types

        _CLIENT = genai.Client(
            api_key=_key(),
            http_options=types.HttpOptions(timeout=300_000),
        )
    return _CLIENT


def list_model_names() -> list[str]:
    if not gemini_configured():
        return []
    return [getattr(model, "name", "") or "" for model in _client().models.list()]


def video_generation_status() -> dict:
    """This account's model list is the source of truth. A text model is not a video model."""
    if not gemini_configured():
        return {
            "available": False,
            "models": [],
            "reason": "GEMINI_API_KEY is not set.",
        }
    try:
        names = list_model_names()
    except Exception as error:  # noqa: BLE001
        return {"available": False, "models": [], "reason": _redact(error)}
    video = [name for name in names if "veo" in name.lower() or "video-generation" in name.lower()]
    return {
        "available": bool(video),
        "models": video,
        "text_model": TEXT_MODEL,
        "reason": "" if video else "models.list returned no Veo or video-generation model for this key.",
    }


def generate_structured(prompt: str, model: type[BaseModel], *, images: list[Path] | None = None) -> BaseModel:
    """One structured call, one repair attempt, then the validation error."""
    last = "no response"
    for attempt in range(2):
        text = prompt if attempt == 0 else f"{prompt}\n\nThe previous reply failed validation: {last}. Reply with the schema only."
        try:
            raw = _generate(text, model, images or [])
        except GeminiError as error:
            raise GeminiError(_redact(error)) from None
        try:
            if isinstance(raw, model):
                return raw
            if isinstance(raw, BaseModel):
                return model.model_validate(raw.model_dump())
            if isinstance(raw, dict):
                return model.model_validate(raw)
            return model.model_validate_json(str(raw))
        except ValidationError as error:
            last = error.error_count()
    raise GeminiError("The model reply did not match the schema after one repair attempt.")


def _generate(prompt: str, model: type[BaseModel], images: list[Path]):
    from google.genai import types

    parts: list = [prompt]
    for path in images[:3]:
        data = Path(path).read_bytes()
        if len(data) > 8_000_000:
            raise GeminiError("A verification frame is over 8 MB.")
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=model,
    )
    try:
        response = _client().models.generate_content(
            model=TEXT_MODEL,
            contents=parts,
            config=config,
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed
        return getattr(response, "text", "") or ""
    except Exception as error:  # noqa: BLE001
        kind = failure_class(str(error))
        if kind != "retry":
            raise GeminiError(_redact(error)) from None
        last = error
        for attempt, model_name in enumerate(FALLBACK_MODELS[1:], start=1):
            time.sleep(backoff_seconds(attempt))
            try:
                response = _client().models.generate_content(
                    model=model_name,
                    contents=parts,
                    config=config,
                )
                parsed = getattr(response, "parsed", None)
                if parsed is not None:
                    return parsed
                return getattr(response, "text", "") or ""
            except Exception as again:  # noqa: BLE001
                last = again
                if failure_class(str(again)) != "retry":
                    raise GeminiError(_redact(again)) from None
        raise GeminiError(_redact(last)) from None


def controlled_http_options():
    """One SDK attempt. The default client is left unchanged."""
    from google.genai import types

    return types.HttpOptions(
        timeout=300_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    )


def generate_once(prompt: str, model: type[BaseModel], *, images: list[Path] | None = None, http_budget, client=None):
    """One verification request. Schema repair and the fallback model are not used."""
    http_budget.acquire()
    try:
        raw = _generate_once(prompt, model, images or [], client=client)
    except GeminiError:
        raise
    except Exception as error:  # noqa: BLE001
        raise GeminiError(_redact(error)) from None
    try:
        if isinstance(raw, model):
            return raw
        if isinstance(raw, BaseModel):
            return model.model_validate(raw.model_dump())
        if isinstance(raw, dict):
            return model.model_validate(raw)
        return model.model_validate_json(str(raw))
    except ValidationError:
        raise GeminiError("The model reply did not match the schema. No repair request was sent.") from None


def _generate_once(prompt: str, model: type[BaseModel], images: list[Path], client=None):
    """A single generate_content call on the primary model."""
    from google.genai import types

    if client is None:
        from google import genai

        client = genai.Client(api_key=_key(), http_options=controlled_http_options())
    parts: list = [prompt]
    for path in images[:3]:
        data = Path(path).read_bytes()
        if len(data) > 8_000_000:
            raise GeminiError("A verification frame is over 8 MB.")
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=model,
    )
    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=parts,
        config=config,
    )
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed
    return getattr(response, "text", "") or ""


def cache_key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def usage_note(response_text: str = "") -> dict:
    """The SDK path used here does not expose a price. Do not invent one."""
    return {
        "model": TEXT_MODEL,
        "text_characters": len(response_text),
        "estimated_cost": None,
        "cost_note": "The generate_content response used by this client does not include a billed amount.",
    }
