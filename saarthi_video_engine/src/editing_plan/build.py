"""Build one validated-shape editing plan for the local proof of concept.

The visual family comes from a completed reference. Timing, copy, and the
synthetic media paths belong to this test, and each decision says so.
"""

from __future__ import annotations

from dataset_adapter.style_profile import style_profile

DURATION = 8.0

# These lines are the test transcript. They are not copied from a reference.
SEGMENTS = (
    {"id": "s0", "text": "THIS IS A PIPELINE TEST", "start": 0.3, "end": 2.4},
    {"id": "s1", "text": "THE PICTURE CHANGES HERE", "start": 2.6, "end": 4.4},
    {"id": "s2", "text": "THE VOICE KEEPS GOING", "start": 4.6, "end": 7.6},
)


def build_proof_plan(dataset: dict, exemplar_id: str, media: dict) -> dict:
    profile = style_profile(dataset, exemplar_id)
    family = profile["layout_family"]
    refs = [exemplar_id]
    # The cut pattern is inferred from the family's hard-cut observations.
    # The clock times belong to this 8 second test, not to the reference.
    cut_reason = (
        f"{exemplar_id} is a completed {family} reference. "
        "Its family uses hard cuts and keeps captions up while the picture changes. "
        "These clock times are the test timeline, not timestamps copied from that Short."
    )
    decisions = [
        _decision(
            "vis-teacher-open",
            "show_teacher",
            0.0,
            2.5,
            cut_reason,
            refs,
            asset_id="teacher",
            source="inferred",
            confidence=0.6,
        ),
        _decision(
            "vis-broll",
            "replace_with_broll",
            2.5,
            4.5,
            "The test sentence names the picture change. No licensed stock clip was retrieved, so the asset is a labeled placeholder.",
            refs,
            asset_id="broll_placeholder",
            source="default",
            confidence=0.4,
            transition="hard_cut",
        ),
        _decision(
            "vis-teacher-close",
            "show_teacher",
            4.5,
            DURATION,
            cut_reason,
            refs,
            asset_id="teacher",
            source="inferred",
            confidence=0.6,
        ),
        _decision(
            "title",
            "title",
            0.0,
            DURATION,
            "Card references keep a title above the video region. The words are the test title, not the reference title.",
            refs,
            source="inferred",
            confidence=0.55,
            text="SYNTHETIC PIPELINE TEST",
        ),
        _decision(
            "logo",
            "logo",
            0.0,
            DURATION,
            "A logo sits with the title in this family. The file is a generated mark. The reference spelling was uncertain and is not reused.",
            refs,
            asset_id="logo_placeholder",
            source="default",
            confidence=0.3,
        ),
    ]
    for index, segment in enumerate(SEGMENTS):
        decisions.append(
            _decision(
                f"cap-{index}",
                "caption",
                segment["start"],
                segment["end"],
                "Caption text is the test transcript. Placement follows the exemplar's lower-card caption, which stayed up across picture changes.",
                refs,
                source="inferred",
                confidence=0.55,
                text=segment["text"],
            )
        )
    decisions.append(
        _decision(
            "cut-in",
            "transition",
            2.5,
            2.53,
            "Sampled reference transitions in this family are hard cuts. No frame-accurate transition curve was measured.",
            refs,
            source="observed",
            confidence=0.5,
            transition="hard_cut",
        )
    )
    return {
        "contract_version": "1.0.0",
        "plan_id": f"poc-{exemplar_id.lower()}",
        "form": "short",
        "input_media": {
            "video_path": media["teacher"],
            "duration_seconds": DURATION,
            "width": 720,
            "height": 1280,
            "fps": 30,
            "audio_path": media["narration"],
            "media_origin": "synthetic_test",
            "transcript": {
                "language": "en",
                "translation_language": None,
                "segments": [dict(segment) for segment in SEGMENTS],
            },
        },
        "content_analysis": {
            "primary_category": "pipeline_test",
            "secondary_categories": [],
            "topic": "Render-pipeline proof",
            "subtopics": ["caption timing", "picture change under continuous narration"],
            "main_message": "The original narration continues while the picture cuts to a placeholder.",
            "narrative_structure": "statement, picture change, statement",
            "emotional_tone": "neutral",
            "entities": [],
            "confidence": 1.0,
            "evidence": "Written for the synthetic proof. Not inferred from a teacher transcript.",
            "source": "default",
        },
        "style": {
            "exemplar_reference_id": exemplar_id,
            "layout_family": family,
            "template_family": profile["template_family"] or "unspecified",
            "trusted": True,
        },
        "decisions": decisions,
        "audio": {
            "preserve_original": True,
            "narration_path": media["narration"],
            "music": {
                "path": media.get("music"),
                "identity": None,
                "identity_status": "unknown",
                "source": "default",
                "gain_under_speech": 0.14,
                "gain_open": 0.35,
            },
        },
        "render": {"width": 720, "height": 1280, "fps": 30},
        "limitations": [
            "Music track identity is unknown in the reference dataset and is not invented here.",
            "The B-roll file is a generated placeholder. No stock URL was selected.",
            "Teacher-visibility percentages used by the style profile are 1fps estimates.",
            "No font file was identified. The composition uses the bundled Inter family as a default.",
            "The input picture and tone are synthetic. This plan does not edit a teacher recording.",
            "SHORT_30, SHORT_32, and SHORT_37 are partial and were not used to seed this profile.",
        ],
    }


def _decision(
    decision_id,
    action,
    start,
    end,
    reason,
    reference_ids,
    source,
    confidence,
    text=None,
    asset_id=None,
    transition=None,
):
    item = {
        "id": decision_id,
        "action": action,
        "start": start,
        "end": end,
        "reason": reason,
        "reference_ids": list(reference_ids),
        "reference_timestamps": [],
        "confidence": confidence,
        "source": source,
    }
    if text is not None:
        item["text"] = text
    if asset_id is not None:
        item["asset_id"] = asset_id
    if transition is not None:
        item["transition"] = transition
    return item
