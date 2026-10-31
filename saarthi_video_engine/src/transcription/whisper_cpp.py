"""Local transcription through whisper.cpp.

The binary and ggml model are optional files under tools/whisper. They are not
downloaded by import. A missing binary or a missing API key is an error the
caller can catch. This module never invents text.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


class WhisperCppError(RuntimeError):
    pass


def default_binary() -> Path | None:
    env = os.environ.get("SAARTHI_WHISPER_CLI")
    if env and Path(env).is_file():
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    candidate = root / "tools" / "whisper" / "bin" / "Release" / "whisper-cli.exe"
    if candidate.is_file():
        return candidate
    found = shutil.which("whisper-cli")
    return Path(found) if found else None


def default_model() -> Path | None:
    env = os.environ.get("SAARTHI_WHISPER_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    candidate = root / "tools" / "whisper" / "ggml-tiny.bin"
    return candidate if candidate.is_file() else None


def transcribe_wav(
    wav_path: Path,
    *,
    language: str = "auto",
    translate: bool = False,
    binary: Path | None = None,
    model: Path | None = None,
    threads: int = 2,
    output_stem: Path | None = None,
    max_context: int | None = None,
    prompt: str | None = None,
) -> dict:
    """Run whisper.cpp and return segment records.

    `language="auto"` asks the program to detect the language. A specific code
    is recorded as configured, not as a detection. Token probabilities are kept
    when the JSON includes them. Word times are kept only when each token has
    its own offsets. They are never interpolated.
    """
    exe = binary or default_binary()
    weights = model or default_model()
    if exe is None or not exe.is_file():
        raise WhisperCppError(
            "whisper-cli was not found. Set SAARTHI_WHISPER_CLI to the executable, or place "
            "whisper-cli.exe in tools/whisper/bin/Release. This module does not call a cloud API."
        )
    if weights is None or not weights.is_file():
        raise WhisperCppError(
            "No ggml model file was found. Set SAARTHI_WHISPER_MODEL. "
            "ggml-tiny.bin is the intended local model because it is small."
        )
    wav_path = Path(wav_path)
    out_base = Path(output_stem) if output_stem else wav_path.with_suffix("")
    json_path = Path(str(out_base) + ".json")
    command = [
        str(exe),
        "-m", str(weights),
        "-f", str(wav_path),
        "-l", language,
        "-ojf",
        "-of", str(out_base),
        "-t", str(threads),
        "-ng",
        "-np",
    ]
    if translate:
        command.append("-tr")
    if max_context is not None:
        command.extend(["-mc", str(int(max_context))])
    if prompt:
        command.extend(["--prompt", prompt])
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0 or not json_path.is_file():
        detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace")[-800:]
        raise WhisperCppError(f"whisper-cli failed ({completed.returncode}): {detail}")
    # A split multibyte token can leave one invalid byte inside an otherwise UTF-8 file.
    payload = json.loads(json_path.read_bytes().decode("utf-8", "replace"))
    return transcript_from_payload(payload, requested_language=language, translate=translate, model_name=weights.name, provenance=f"local whisper-cli {exe.name}, model {weights.name}, translate={translate}")


def transcript_from_payload(
    payload: dict,
    *,
    requested_language: str,
    translate: bool = False,
    model_name: str | None = None,
    provenance: str = "",
) -> dict:
    """Parse whisper.cpp JSON without treating a forced -l code as a detection."""
    segments = _segments(payload)
    detected = _detected_language(payload)
    if requested_language == "auto" and detected:
        language = detected
        language_source = "provider"
    elif requested_language == "auto":
        language = "und"
        language_source = "inferred"
    else:
        language = requested_language
        language_source = "configured"
    return {
        "language": language,
        "language_source": language_source,
        "translated_to_english": translate,
        "method": "whisper.cpp",
        "model": model_name,
        "provenance": provenance,
        "segments": segments,
    }


def _detected_language(payload: dict) -> str | None:
    result = payload.get("result") or {}
    value = result.get("language")
    if isinstance(value, str) and value and value != "auto":
        return value
    return None


def _segments(payload: dict) -> list[dict]:
    rows = payload.get("transcription") or []
    parsed = []
    for index, row in enumerate(rows):
        offsets = row.get("offsets") or {}
        start = _millis(offsets.get("from"))
        end = _millis(offsets.get("to"))
        text = (row.get("text") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        words = _words(row.get("tokens") or [])
        probabilities = [word["probability"] for word in words if word.get("probability") is not None]
        parsed.append(
            {
                "id": f"a{index}",
                "text": text,
                "original_text": text,
                "start": start,
                "end": end,
                "confidence": round(sum(probabilities) / len(probabilities), 4) if probabilities else None,
                "timing_precision": "word" if words else "segment",
                "words": words,
            }
        )
    return parsed


def _words(tokens: list) -> list[dict]:
    words = []
    for token in tokens:
        if not isinstance(token, dict):
            return []
        offsets = token.get("offsets") or {}
        start = _millis(offsets.get("from"))
        end = _millis(offsets.get("to"))
        text = (token.get("text") or "").strip()
        if start is None or end is None or end < start or not text:
            continue
        if text.startswith("[") or text.startswith("<"):
            continue
        probability = token.get("p")
        words.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "probability": probability if isinstance(probability, (int, float)) else None,
            }
        )
    return words


def _millis(value) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1000.0, 3)
