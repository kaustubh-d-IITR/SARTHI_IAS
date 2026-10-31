"""Pick a completed reference record for a new recording.

Portrait footage prefers a full-bleed lecture layout so a 9:16 frame is not
shrunk into a card. Landscape footage prefers the highest-scoring completed
record whose layout has a writer, so a wide slide is not cropped.
"""

from __future__ import annotations

from dataset_adapter.retrieve import retrieve
from dataset_adapter.style_profile import LAYOUT_FAMILIES


def choose_exemplar(dataset: dict, query: str, *, portrait: bool) -> dict:
    hits = retrieve(dataset, query=query[:400], trusted_only=True, limit=8)
    supported = [hit for hit in hits if hit.get("layout_group") in LAYOUT_FAMILIES]
    if portrait:
        full = [hit for hit in supported if hit["layout_group"] == "full_bleed_board_or_lecture"]
        if full:
            return full[0]
        fallback = retrieve(
            dataset,
            layout_group="full_bleed_board_or_lecture",
            trusted_only=True,
            limit=1,
        )
        if fallback:
            return fallback[0]
    if supported:
        return supported[0]
    fallback = retrieve(dataset, layout_group="bounded_card_with_title", trusted_only=True, limit=1)
    if not fallback:
        raise ValueError("no completed reference record has a supported layout")
    return fallback[0]
