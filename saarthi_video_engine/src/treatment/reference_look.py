"""Look measured from the NEET-to-UPSC Short, not from a topic keyword.

Teacher frames at 0.5s, 5s, 8s, 14s and 36s stay in natural colour. The board
text stays dark on a white ground. The hospital frame at 20s and the laptop
frame at 28s are full colour as well. The opening second holds a faint second
picture over the whiteboard, then cuts. That blend is an entrance, not a grade
that stays on the teacher. The red stroke and the red X are not part of this look.
"""

from __future__ import annotations

ENTRANCE_SECONDS = 0.4
CONTRAST = 1.05
SATURATION = 1.04


def entrance_seconds() -> float:
    return ENTRANCE_SECONDS


def look_from_plan(plan: dict) -> dict:
    """CSS numbers taken from the plan timeline. Absent timeline keeps the previous constants."""
    timeline = ((plan.get("render") or {}).get("grade") or {}).get("timeline") or []
    look = {
        "contrast": CONTRAST,
        "saturation": SATURATION,
        "from_opacity": 0.42,
        "fade_seconds": ENTRANCE_SECONDS,
        "filter_teacher": True,
        "filter_broll": True,
        "timeline": timeline,
    }
    if not timeline:
        return look
    look["filter_broll"] = False
    for item in timeline:
        target = str(item.get("target") or "")
        if item.get("type") == "natural" and "teacher" in target:
            look["contrast"] = float(item.get("contrast") or look["contrast"])
            look["saturation"] = float(item.get("saturation") or look["saturation"])
            look["filter_broll"] = "b-roll" in target
        if item.get("type") == "crossfade":
            look["from_opacity"] = float(item.get("from_opacity") or look["from_opacity"])
            look["fade_seconds"] = float(item.get("duration") or look["fade_seconds"])
    return look


def lesson_grade(duration: float = 0.0) -> dict:
    """Constant natural grade. No black-and-white interval is supported by the frames."""
    return {
        "name": "natural",
        "confidence": 0.9,
        "applied": False,
        "ffmpeg_video_filter": None,
        "matched_terms": [],
        "reason": (
            "The reference teacher and the reference B-roll stay in colour. "
            f"A constant contrast of {CONTRAST} and saturation of {SATURATION} is applied in the page, "
            "with a {:.1f}s fade as each insert replaces the teacher.".format(ENTRANCE_SECONDS)
        ),
        "timeline": filter_timeline(duration),
    }


def filter_timeline(duration: float) -> list[dict]:
    """One natural interval for the lesson, then the entrance used on each insert."""
    end = round(float(duration), 3) if duration else None
    return [
        {
            "target": "teacher and b-roll",
            "start": 0.0,
            "end": end,
            "type": "natural",
            "contrast": CONTRAST,
            "saturation": SATURATION,
            "desaturate": False,
            "reason": "Sampled reference frames are not monochrome and are not vignetted.",
        },
        {
            "target": "b-roll entrance",
            "duration": ENTRANCE_SECONDS,
            "from_opacity": 0.42,
            "to_opacity": 1.0,
            "type": "crossfade",
            "reason": (
                "The first second of the reference mixes a second picture over the whiteboard. "
                "The red X and the newspaper masthead are not copied."
            ),
        },
    ]
