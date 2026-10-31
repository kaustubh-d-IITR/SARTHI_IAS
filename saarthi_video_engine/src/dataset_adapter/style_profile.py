"""Aggregate a layout family from completed records only."""

from __future__ import annotations

from collections import Counter
from statistics import median

from .loader import is_trusted, partial_ids
from .retrieve import get_record

LAYOUT_FAMILIES = (
    "bounded_card_with_title",
    "full_bleed_board_or_lecture",
    "split_teacher_and_graphic",
    "screen_recording_or_product_ui",
)


def _caption_fields(record: dict) -> dict:
    """The dataset stores captions as an object, a note, or a list of notes."""
    raw = record.get("captions")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"style": raw, "examples": [], "remains_during_broll": None}
    if isinstance(raw, list):
        return {
            "style": " ".join(str(item) for item in raw),
            "examples": [],
            "remains_during_broll": None,
        }
    return {"examples": [], "remains_during_broll": None}


def observations(record: dict) -> list[dict]:
    """Pull timed observations that the dataset actually stores.

    Confidence stays at the dataset's own level. Music identity is never filled in.
    """
    found = []
    captions = _caption_fields(record)
    for example in captions.get("examples") or []:
        found.append(
            {
                "kind": "caption",
                "t": example.get("t"),
                "text": example.get("text"),
                "position": captions.get("position"),
                "style": captions.get("style"),
                "remains_during_broll": captions.get("remains_during_broll"),
                "source": "observed",
                "evidence": "captions.examples in shorts_dataset.json",
                "confidence": "frame_reading",
            }
        )
    for segment in record.get("broll_segments") or []:
        if not isinstance(segment, dict):
            continue
        found.append(
            {
                "kind": "broll",
                "start": segment.get("start"),
                "end": segment.get("end"),
                "description": segment.get("description") or segment.get("what"),
                "source": "observed",
                "evidence": "broll_segments; source footage was not identified",
                "confidence": (record.get("quality") or {}).get("broll_confidence"),
            }
        )
    for effect in record.get("effects_transitions") or []:
        found.append(
            {
                "kind": "transition",
                "t": effect.get("t"),
                "text": effect.get("type"),
                "source": "observed",
                "evidence": effect.get("evidence"),
                "confidence": "sampled_frames",
            }
        )
    visibility = record.get("teacher_visibility") or {}
    found.append(
        {
            "kind": "teacher_visibility",
            "teacher_visible_any_percent_of_samples": visibility.get(
                "teacher_visible_any_percent_of_samples"
            ),
            "broll_replaces_teacher_percent_of_samples": visibility.get(
                "broll_replaces_teacher_percent_of_samples"
            ),
            "source": "observed",
            "evidence": visibility.get("method"),
            "confidence": (record.get("quality") or {}).get("teacher_visibility_confidence"),
        }
    )
    audio = record.get("audio_music") or {}
    found.append(
        {
            "kind": "music",
            "identity": None,
            "identity_status": audio.get("music_track_status") or "unknown",
            "source": "observed",
            "evidence": "audio was measured, not listened to",
            "confidence": (record.get("quality") or {}).get("audio_analysis_completeness"),
        }
    )
    return found


def _median(values: list[float]) -> float | None:
    clean = [value for value in values if isinstance(value, (int, float))]
    if not clean:
        return None
    return float(median(clean))


def style_profile(dataset: dict, exemplar_id: str) -> dict:
    """Build a profile for the exemplar's layout family.

    Partial records are listed and excluded. A partial exemplar cannot seed rules.
    """
    exemplar = get_record(dataset, exemplar_id)
    if not is_trusted(exemplar):
        raise ValueError(
            f"{exemplar_id} is {exemplar['quality']['status']} and cannot seed a style profile"
        )
    layout = exemplar.get("layout_group")
    if layout not in LAYOUT_FAMILIES:
        raise ValueError(
            f"{exemplar_id} layout_group {layout!r} has no renderer family yet"
        )
    peers = [
        record
        for record in dataset["shorts"]
        if is_trusted(record) and record.get("layout_group") == layout
    ]
    families = Counter(
        (record.get("visual_style") or {}).get("template_family") or "unspecified"
        for record in peers
    )
    caption_holds = 0
    transitions: Counter[str] = Counter()
    visibility_values = []
    for record in peers:
        captions = _caption_fields(record)
        if captions.get("remains_during_broll") is True:
            caption_holds += 1
        for effect in record.get("effects_transitions") or []:
            label = (effect.get("type") or "").split(" to ")[0].strip().lower()
            if label:
                transitions[label] += 1
        visibility = record.get("teacher_visibility") or {}
        visibility_values.append(visibility.get("teacher_visible_any_percent_of_samples"))
    style = exemplar.get("visual_style") or {}
    return {
        "exemplar_reference_id": exemplar_id,
        "trusted": True,
        "layout_family": layout,
        "template_family": style.get("template_family"),
        "peer_count": len(peers),
        "excluded_partial_ids": partial_ids(dataset),
        "template_families": dict(families),
        "caption": {
            "position": _caption_fields(exemplar).get("position"),
            "style": _caption_fields(exemplar).get("style"),
            "typical_chunk": _caption_fields(exemplar).get("typical_chunk"),
            "remains_during_broll_count": caption_holds,
            "remains_during_broll_peer_count": len(peers),
            "source": "observed",
        },
        "teacher_visibility": {
            "median_visible_any_percent": _median(visibility_values),
            "confidence": "estimated_from_1fps_samples",
            "source": "observed",
        },
        "transitions": {
            "top": transitions.most_common(5),
            "source": "observed",
            "note": "Types are labels from sampled frames, not frame-accurate effect parameters.",
        },
        "music": {
            "identity": None,
            "identity_status": "unknown",
            "source": "observed",
            "note": "No reference was listened to. Do not invent a track title.",
        },
        "broll": {
            "source_identified": False,
            "note": "Descriptions exist. Licensing sources and original URLs do not.",
        },
        "typography": {
            "font_file": None,
            "note": "On-screen lettering was read from frames. No font file was identified.",
        },
        "observed_canvas": style.get("canvas"),
        "observed_title_position": style.get("title_position"),
        "observed_video_region": style.get("video_region"),
        "observed_logo_position": style.get("logo_position"),
        "logo_text_as_read": style.get("logo_text_as_read"),
        "logo_spelling_needs_confirmation": True,
    }
