"""Phase 5: Roman Hindi, logo, outro fit, and video-query rules."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assets.concepts import visual_queries  # noqa: E402
from assets.video_commons import accept_video_candidate  # noqa: E402
from editing_plan.phase2 import build_phase2_plan, picture_fraction  # noqa: E402
from hyperframes_adapter.compose import render_html, render_title_scene  # noqa: E402
from render.frame import reference_frame  # noqa: E402
from render.outro import fitted_size, outro_filter  # noqa: E402
from transcription.captions import is_recognition_loop, prepare_segment  # noqa: E402
from transcription.hinglish import clause_for_segment, merge_passes, splice_unknown_run  # noqa: E402
from transcription.normalize import normalize_roman  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402


def _segment(index, start, end, text, words=None):
    return {
        "id": f"a{index}",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "timing_precision": "segment",
        "words": words or [],
    }


class RomanTests(unittest.TestCase):
    def test_common_hinglish_spellings_are_normalized(self):
        text, reasons = normalize_roman("main kaii baar yeh baat kahata hoon, mera koi bhai bahan nahin he")
        self.assertIn("Main kai baar", text)
        self.assertIn("kehta hoon", text)
        self.assertIn("bhai-behen", text)
        self.assertIn("nahi hai", text)
        self.assertTrue(reasons)
        self.assertNotIn("hospital", text)

    def test_english_clause_is_not_rewritten(self):
        text, _reasons = normalize_roman("I don't have a sibling and he went to the river")
        self.assertIn("I don't have a sibling", text)
        self.assertIn("he went", text)

    def test_phonetic_english_run_uses_the_other_pass(self):
        caption, note = splice_unknown_run(
            "Main kai baar ye baat kehta hoon ke ai duud haan sibalin, mere koi bhai-behen nahi hai.",
            "I don't have a sibling",
        )
        self.assertIn("I don't have a sibling", caption)
        self.assertNotIn("sibalin", caption)
        self.assertIn("bhai-behen", caption)
        self.assertTrue(note)

    def test_english_clause_comes_from_the_overlapping_pass(self):
        segment = {"start": 0.0, "end": 4.0, "original_text": "hindi line"}
        others = [
            {"start": 0.0, "end": 2.7, "original_text": "I don't have a sibling"},
            {"start": 2.7, "end": 8.0, "original_text": "of course I don't have a blood sibling today"},
        ]
        clause = clause_for_segment(segment, others)
        self.assertEqual(clause, "I don't have a sibling")

    def test_repeated_sentence_is_withheld(self):
        repeated = "नाही मैंने नुस्वेपर पडा " * 8
        self.assertTrue(is_recognition_loop(repeated))
        prepared = prepare_segment(_segment(0, 0, 30, repeated), "hi")
        self.assertEqual(prepared["caption_text"], "")
        self.assertIn("repeated_recognition", prepared["flags"])

    def test_looping_pass_is_replaced(self):
        primary = [_segment(0, 0, 30, "p " * 40)]
        secondary = [_segment(0, 0, 2.3, "nahi menin newspaper pada aur nahi menin book padi")]
        chosen = merge_passes(primary, secondary, is_recognition_loop)
        self.assertEqual(len(chosen), 1)
        self.assertIn("newspaper", chosen[0]["original_text"])


class BrollQueryTests(unittest.TestCase):
    def test_queries_follow_the_sentence_and_skip_personal_lines(self):
        rows = visual_queries("I did not read the newspaper or the NCERT book on YouTube")
        searches = {row["query"] for row in rows if row["action"] == "search"}
        self.assertIn("newspaper", searches)
        self.assertIn("textbook", searches)
        skipped = {row["term"] for row in rows if row["action"] == "skip"}
        self.assertIn("youtube", skipped)
        family = visual_queries("Mera koi bhai-behen nahi hai")
        self.assertFalse(any(row["action"] == "search" for row in family))

    def test_a_river_sentence_is_not_a_hospital_query(self):
        rows = visual_queries("the river flooded the valley after the rain")
        queries = [row["query"] for row in rows if row["action"] == "search"]
        self.assertIn("river", queries)
        self.assertNotIn("hospital ward", queries)

    def test_unclear_video_license_is_rejected(self):
        candidate = {
            "license": "CC BY 4.0",
            "license_url": "",
            "download_url": "https://example.invalid/paper.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "Newspaper",
            "description": "a person reading a newspaper",
        }
        self.assertIsNotNone(accept_video_candidate(candidate, "newspaper", "newspaper"))
        cartoon = dict(candidate, license_url="https://creativecommons.org/licenses/by-sa/4.0", title="Baby Globe reading newspaper", description="cartoon mascot reading a newspaper")
        self.assertIsNotNone(accept_video_candidate(cartoon, "newspaper", "I did not read the newspaper"))


class LogoAndOutroTests(unittest.TestCase):
    def test_outro_fit_does_not_stretch(self):
        width, height = fitted_size(848, 478, 720, 1280)
        self.assertLessEqual(width, 720)
        self.assertLessEqual(height, 1280)
        self.assertAlmostEqual((width / height), (848 / 478), delta=0.03)
        graph = outro_filter(848, 478, 720, 1280)
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertNotIn("crop", graph)

    def test_corner_logo_is_top_right_and_absent_by_default(self):
        dataset = load_dataset()
        transcript = {
            "language": "en",
            "language_source": "provider",
            "translated_to_english": False,
            "method": "test",
            "model": "none",
            "provenance": "unit",
            "segments": [_segment(0, 0, 8, "forces on the pulley stay in balance")],
        }
        plan = build_phase2_plan(
            dataset,
            transcript=transcript,
            selection={"source_in": 0, "source_out": 8, "duration": 8, "text": "forces on the pulley", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={"width": 1280, "height": 720, "fps": 30, "attribution_line": "test", "license": "not stated", "license_url": "not-stated-in-file"},
            assets=[],
        )
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        plain = render_html(plan, defaults)
        self.assertNotIn("corner-logo", plain)
        plan["render"]["corner_logo"] = "assets/logo.png"
        html = render_html(plan, defaults)
        self.assertIn('id="corner-logo"', html)
        frame = reference_frame()
        self.assertIn(f"left: {frame['logo_left']}px", html)
        self.assertIn(f"width: {frame['logo_size']}px", html)
        self.assertIn(f"height: {frame['logo_size']}px", html)
        self.assertIn("object-fit: contain", html)
        self.assertIn("object-fit: cover", html)
        self.assertIn("text-align: center", render_title_scene(plan, defaults))
        self.assertNotIn("id=\"credit\"", html)
        self.assertNotIn("#d01212", html)
        self.assertLessEqual(picture_fraction(plan, "replace_with_broll"), 0.58)


if __name__ == "__main__":
    unittest.main()
