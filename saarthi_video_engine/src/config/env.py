"""Load a local .env file without printing secret values."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def load_project_env(path: Path | None = None) -> None:
    """Set variables that are not already in the process environment.

    An existing environment variable wins, so a shell export is not overwritten
    by the file. Blank lines and comments are ignored.
    """
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_elevenlabs_key() -> str:
    """Return the key or raise a message that tells the operator where to put it."""
    load_project_env()
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise ConfigError(
            "ELEVENLABS_API_KEY is empty. Copy .env.example to .env in saarthi_video_engine "
            "and paste your ElevenLabs key there. Do not commit .env and do not paste the key into chat."
        )
    return key


def gemini_configured() -> bool:
    """True when a non-empty Gemini key is loaded. The value is not returned."""
    load_project_env()
    return bool((os.environ.get("GEMINI_API_KEY") or "").strip())
