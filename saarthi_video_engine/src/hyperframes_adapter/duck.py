"""Volume keyframes for a music bed. Speech intervals use the ducked gain."""

from __future__ import annotations


def volume_lane(
    duration: float,
    speech: list[tuple[float, float]],
    *,
    ducked: float = 0.14,
    open_level: float = 0.35,
    attack: float = 0.15,
    release: float = 0.4,
) -> dict:
    """Build a HyperFrames data-automation volume lane.

    Times are composition seconds. The bed stays at `open_level` outside speech
    and at `ducked` while speech is present. This is a gain envelope, not a
    compressor listening to the narration waveform.
    """
    windows = _merge(speech, duration)
    events: list[tuple[float, float]] = [(0.0, open_level)]
    for start, end in windows:
        events.append((max(0.0, start - attack), open_level))
        events.append((start, ducked))
        events.append((end, ducked))
        events.append((min(duration, end + release), open_level))
    events.append((duration, open_level))
    events.sort(key=lambda item: item[0])
    points: list[dict] = []
    for time, value in events:
        time = round(max(0.0, min(duration, time)), 3)
        if points and abs(points[-1]["t"] - time) < 1e-6:
            # A duck and an open key landing together stay ducked.
            points[-1]["v"] = min(points[-1]["v"], value)
            continue
        points.append({"t": time, "v": value})
    return {"version": 1, "lanes": [{"target": "volume", "points": points}]}


def _merge(speech: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    clean = []
    for start, end in speech:
        start = max(0.0, start)
        end = min(duration, end)
        if end > start:
            clean.append((start, end))
    clean.sort()
    merged: list[tuple[float, float]] = []
    for start, end in clean:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
