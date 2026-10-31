"""Compare two PCM wavs. Silence detection is not used here."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np


def compare_wavs(reference: Path, rendered: Path, max_lag_seconds: float = 0.5) -> dict:
    """Pearson correlation after a small lag search.

    Codec delay and AAC encoding move the waveform. The lag is the shift, in
    seconds, that best aligns the rendered audio to the source segment. The
    correlation is reported as measured. It is not a claim of a bit-identical file.
    """
    left, left_rate = _samples(reference)
    right, right_rate = _samples(rendered)
    if left_rate != right_rate:
        raise ValueError(f"sample rates differ: {left_rate} vs {right_rate}")
    lag = _best_lag(left, right, left_rate, max_lag_seconds)
    correlation = _corr_at(left, right, lag)
    return {
        "reference_seconds": round(len(left) / left_rate, 3),
        "rendered_seconds": round(len(right) / right_rate, 3),
        "sample_rate": left_rate,
        "lag_seconds": round(lag / left_rate, 4),
        "correlation": round(correlation, 4),
        "method": "pearson correlation of PCM after a lag search; not silence detection",
    }


def _samples(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"{path} is not 16-bit PCM")
    count = len(frames) // 2
    values = struct.unpack("<" + "h" * count, frames)
    if channels == 1:
        return [value / 32768.0 for value in values], rate
    mixed = []
    for index in range(0, len(values), channels):
        mixed.append(sum(values[index : index + channels]) / channels / 32768.0)
    return mixed, rate


def _best_lag(left: list[float], right: list[float], rate: int, max_lag_seconds: float) -> int:
    """Lag in samples. Positive means the rendered signal is delayed."""
    reference = np.asarray(left, dtype=np.float64)
    rendered = np.asarray(right, dtype=np.float64)
    reference = reference - reference.mean()
    rendered = rendered - rendered.mean()
    step = max(1, rate // 4000)
    reference = reference[::step]
    rendered = rendered[::step]
    limit = int(max_lag_seconds * rate / step)
    size = 1 << int(np.ceil(np.log2(reference.size + rendered.size - 1)))
    correlation = np.fft.irfft(
        np.fft.rfft(reference, size) * np.conj(np.fft.rfft(rendered, size)),
        size,
    )
    lags = np.arange(-limit, limit + 1)
    indexes = np.where(lags >= 0, lags, size + lags)
    # irfft(fft(reference) * conj(fft(rendered))) peaks at n-delay when rendered is late.
    chosen = -int(lags[int(np.argmax(correlation[indexes]))]) * step
    best = chosen
    best_score = -2.0
    for lag in range(chosen - step, chosen + step + 1):
        score = _corr_at(left, right, lag)
        if score > best_score:
            best_score = score
            best = lag
    return best


def _corr_at(left: list[float], right: list[float], lag: int) -> float:
    reference = np.asarray(left, dtype=np.float64)
    rendered = np.asarray(right, dtype=np.float64)
    if lag >= 0:
        reference = reference[: max(0, rendered.size - lag)]
        rendered = rendered[lag:]
    else:
        rendered = rendered[: max(0, reference.size + lag)]
        reference = reference[-lag:]
    count = int(min(reference.size, rendered.size))
    if count < 100:
        return 0.0
    reference = reference[:count]
    rendered = rendered[:count]
    if reference.std() <= 0 or rendered.std() <= 0:
        return 0.0
    return float(np.corrcoef(reference, rendered)[0, 1])
