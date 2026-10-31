"""Coverage from interval unions. Overlaps are not counted twice."""

from __future__ import annotations


def union_seconds(intervals: list[tuple[float, float]], limit: float | None = None) -> float:
    clipped: list[tuple[float, float]] = []
    for start, end in intervals:
        lo = max(0.0, float(start))
        hi = float(end) if limit is None else min(float(end), float(limit))
        if hi > lo:
            clipped.append((lo, hi))
    clipped.sort()
    total = 0.0
    cursor = -1.0
    for start, end in clipped:
        if start > cursor:
            total += end - start
            cursor = end
        elif end > cursor:
            total += end - cursor
            cursor = end
    return round(total, 3)


def coverage_report(
    *,
    lesson_seconds: float,
    eligible_seconds: float,
    planned: list[tuple[float, float]],
    rendered: list[tuple[float, float]],
    verified: list[tuple[float, float]],
) -> dict:
    lesson = max(float(lesson_seconds), 0.0)
    eligible = max(float(eligible_seconds), 0.0)
    planned_s = union_seconds(planned, lesson)
    rendered_s = union_seconds(rendered, lesson)
    verified_s = union_seconds(verified, lesson)
    return {
        "lesson_seconds": round(lesson, 3),
        "eligible_seconds": round(eligible, 3),
        "planned_broll_seconds": planned_s,
        "rendered_broll_seconds": rendered_s,
        "verified_broll_seconds": verified_s,
        "planned_broll_coverage": _ratio(planned_s, lesson),
        "rendered_broll_coverage": _ratio(rendered_s, lesson),
        "verified_broll_coverage": _ratio(verified_s, lesson),
        "eligible_broll_coverage": _ratio(verified_s, eligible),
        "full_lesson_broll_coverage": _ratio(verified_s, lesson),
        "target": 0.5,
        "target_met": _ratio(verified_s, lesson) >= 0.5,
    }


def _ratio(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole, 4)
