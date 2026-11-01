"""Keep every cutaway inside the footage its clip actually has.

A cutaway shows a clip in real time, so its time on screen (speech_start..speech_end)
and its source window (source_in..source_out) have the same length, and the source
window must lie inside 0..clip duration. fit_cutaway enforces that in one place:

1. If the window runs past the end of the clip but the clip is long enough, the
   window moves earlier (source_in decreases); the timeline is unchanged. For a
   source start a reviewer typed (keep_source), the end is clamped instead and the
   cutaway shortened, or rejected if too little footage remains after that start.
2. If the clip is shorter than the time on screen, the cutaway is shortened to the
   footage that exists (speech_end moves earlier).
3. If what remains is shorter than the minimum, the cutaway is rejected with a
   reason. Footage is never borrowed from another clip.

Times are kept to the millisecond and rounded towards the inside of the clip, so
float noise or a frame-rounded duration never pushes source_out past the end.
"""

from __future__ import annotations

import math

EPS = 1e-3


def _down(value: float) -> float:
    return math.floor(value * 1000 + 1e-6) / 1000


def usable_seconds(item: dict) -> float | None:
    """Footage a clip really has: the shorter of the container and the video stream."""
    values = [float(item[key]) for key in ("duration_seconds", "video_duration_seconds", "clip_duration") if isinstance(item.get(key), (int, float)) and float(item[key]) > 0]
    return min(values) if values else None


def fit_cutaway(speech_start: float, speech_end: float, source_in: float, clip_seconds: float | None, min_seconds: float, keep_source: bool = False) -> dict:
    """The nearest valid placement, or {"ok": False, "reason": ...}. Never moves speech_start.

    keep_source is for a source start a person typed: the end is clamped to the clip
    instead of the window moving to footage they did not choose.
    """
    start = round(float(speech_start), 3)
    end = round(float(speech_end), 3)
    source_in = max(0.0, round(float(source_in or 0.0), 3))
    notes = []
    if end - start <= EPS:
        return {"ok": False, "reason": "The cutaway has no length on the timeline."}
    if clip_seconds is None or clip_seconds <= 0:
        length = round(end - start, 3)
        return {"ok": True, "speech_start": start, "speech_end": end, "source_in": source_in, "source_out": round(source_in + length, 3), "adjusted": ""}
    clip = _down(float(clip_seconds))
    if clip < min_seconds - EPS:
        return {"ok": False, "reason": f"The clip has {clip:.2f} s of footage, shorter than the {min_seconds:.1f} s a cutaway needs."}
    length = round(end - start, 3)
    if length > clip + EPS:
        end = round(start + clip, 3)
        length = clip
        notes.append(f"shortened to the clip's {clip:.2f} s of footage")
    if length < min_seconds - EPS:
        return {"ok": False, "reason": f"Only {length:.2f} s fits, shorter than the {min_seconds:.1f} s a cutaway needs."}
    if source_in + length > clip + EPS and keep_source:
        remaining = _down(clip - source_in)
        if remaining < min_seconds - EPS:
            return {
                "ok": False,
                "reason": f"The source starts at {source_in:.2f} s, leaving {max(0.0, remaining):.2f} s of the clip's {clip:.2f} s; a cutaway needs {min_seconds:.1f} s. Start the source earlier.",
            }
        end = round(start + remaining, 3)
        length = remaining
        notes.append(f"shortened to end at the clip's last frame ({clip:.2f} s)")
    if source_in + length > clip + EPS:
        source_in = max(0.0, _down(clip - length))
        notes.append(f"source moved to {source_in:.2f}–{source_in + length:.2f} s to stay inside the clip")
    source_out = round(source_in + length, 3)
    if source_out > clip:
        source_out = clip
        source_in = round(clip - length, 3)
    return {"ok": True, "speech_start": start, "speech_end": end, "source_in": source_in, "source_out": source_out, "adjusted": "; ".join(notes)}


def interval_errors(asset_id: str, speech_start: float, speech_end: float, source_in: float, source_out: float, clip_seconds: float | None) -> list[str]:
    """Diagnostics a person can act on. Empty when the cutaway can be rendered."""
    errors = []
    if source_in < -EPS:
        errors.append(f"{asset_id}: source starts before the clip (source_in {source_in:.3f} s)")
    if source_out <= source_in + EPS:
        errors.append(f"{asset_id}: source interval is empty ({source_in:.3f}–{source_out:.3f} s)")
    if clip_seconds is not None and source_out > clip_seconds + EPS:
        errors.append(f"{asset_id}: source interval {source_in:.3f}–{source_out:.3f} s is past the clip's {clip_seconds:.3f} s of footage")
    if speech_end <= speech_start + EPS:
        errors.append(f"{asset_id}: timeline interval is empty ({speech_start:.3f}–{speech_end:.3f} s)")
    elif abs((source_out - source_in) - (speech_end - speech_start)) > 0.05:
        errors.append(f"{asset_id}: {source_out - source_in:.3f} s of footage cannot fill {speech_end - speech_start:.3f} s on screen")
    return errors
