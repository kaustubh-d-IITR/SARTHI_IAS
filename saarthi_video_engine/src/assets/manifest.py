"""Asset records for the future retrieval step.

A path here is not a claim that footage was discovered. Rights and origin are
stored beside the file so a later picker can refuse an unknown source.
"""

from __future__ import annotations


def build_manifest(media: dict) -> dict:
    return {
        "manifest_version": "1.0.0",
        "retrieval_implemented": False,
        "assets": [
            {
                "id": "teacher",
                "role": "original_camera",
                "path": media["teacher"],
                "origin": "ffmpeg_lavfi",
                "rights_status": "synthetic_test_asset",
                "license_note": "Generated in this workspace for the render test. Not a teacher recording.",
            },
            {
                "id": "narration",
                "role": "original_narration",
                "path": media["narration"],
                "origin": "ffmpeg_lavfi",
                "rights_status": "synthetic_test_asset",
                "license_note": "Continuous tone standing in for narration. Not speech from a reference.",
            },
            {
                "id": "broll_placeholder",
                "role": "supporting_visual",
                "path": media["broll"],
                "origin": "ffmpeg_lavfi",
                "rights_status": "placeholder_no_licensed_asset",
                "license_note": "Explanatory color card. No stock URL was chosen or scraped.",
            },
            {
                "id": "music_placeholder",
                "role": "music_bed",
                "path": media["music"],
                "origin": "ffmpeg_lavfi",
                "rights_status": "placeholder_no_licensed_asset",
                "identity": None,
                "identity_status": "unknown",
                "license_note": "Quiet generated tone. Not a song identified in the dataset.",
            },
            {
                "id": "logo_placeholder",
                "role": "logo",
                "path": media["logo"],
                "origin": "generated_svg",
                "rights_status": "generated_placeholder",
                "license_note": "Project test mark. The reference logo spelling was not confirmed.",
            },
        ],
    }
