"""Pick a mild grade from spoken words and optional on-screen terms.

No filter is returned when the topic is unclear. The mappings are examples
used as rules, not a claim that a model watched the video.
"""

from __future__ import annotations

RULES = (
    (
        "serious",
        ("covid", "hospital", "ppe", "pandemic", "funeral", "massacre", "disaster", "riot"),
        0.72,
        "eq=contrast=1.04:saturation=0.8,vignette=angle=PI/6",
        "Restrained contrast and slightly lower saturation for serious subject matter.",
    ),
    (
        "documentary",
        ("empire", "ancient", "revolt", "dynasty", "inscription", "archaeology", "war"),
        0.66,
        "eq=contrast=1.05:saturation=0.86,vignette=angle=PI/7",
        "A mild documentary grade. Skin tones stay near the source.",
    ),
    (
        "natural",
        ("monsoon", "river", "glacier", "climate", "forest", "mountain", "ocean"),
        0.66,
        "eq=contrast=1.02:saturation=1.05",
        "Slightly clearer colour for geography or environment.",
    ),
    (
        "neutral",
        ("constitution", "parliament", "economy", "inflation", "gdp", "policy", "article"),
        0.66,
        "eq=contrast=1.04:saturation=0.96",
        "A small contrast lift for polity or economy, without a colour cast.",
    ),
)

APPLY_AT = 0.55


def infer_treatment(text: str, visual_terms: list[str] | None = None) -> dict:
    haystack = f"{text} {' '.join(visual_terms or [])}".lower().replace("-", " ")
    hits = []
    for name, words, confidence, ffmpeg_filter, reason in RULES:
        found = [word for word in words if word in haystack]
        if found:
            hits.append((name, found, confidence, ffmpeg_filter, reason))
    if len(hits) != 1:
        return {
            "name": "none",
            "confidence": 0.3 if hits else 0.2,
            "reason": "More than one topic rule matched." if hits else "No topic rule matched the speech.",
            "ffmpeg_video_filter": None,
            "matched_terms": [word for _name, found, *_rest in hits for word in found],
            "applied": False,
        }
    name, found, confidence, ffmpeg_filter, reason = hits[0]
    applied = confidence >= APPLY_AT
    return {
        "name": name if applied else "none",
        "confidence": confidence,
        "reason": reason if applied else "The topic guess was below the confidence needed to grade the picture.",
        "ffmpeg_video_filter": ffmpeg_filter if applied else None,
        "matched_terms": found,
        "applied": applied,
    }
