"""Phase 10: reference card, no diagnostic cross, longer honest B-roll, coverage."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import broll_opportunities, lesson_coverage  # noqa: E402
from assets.video_commons import accept_video_candidate, search_videos  # noqa: E402
from hyperframes_adapter.compose import _captions, render_html  # noqa: E402
from render.frame import layout_issues, reference_frame  # noqa: E402
from render.outro import outro_filter  # noqa: E402
from run_phase4 import clip_take_seconds  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402


def _word(text, start, end):
    return {"text": text, "start": start, "end": end, "logprob": -0.1}


def _segment(index, start, end, text, words=None):
    return {
        "id": f"a{index}",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "language": "hi",
        "language_source": "configured",
        "timing_precision": "segment",
        "words": words or [],
    }


class FrameAndCoverageTests(unittest.TestCase):
    def test_logo_sits_left_of_the_title_and_the_card_fits(self):
        frame = reference_frame()
        self.assertEqual(layout_issues(frame), [])
        self.assertLess(frame["logo_left"] + frame["logo_size"], frame["title_left"])
        self.assertGreater(frame["caption_bottom"], frame["card_bottom"])

    def test_coverage_stops_at_the_lesson_and_does_not_count_the_outro(self):
        self.assertEqual(lesson_coverage([(0, 4), (3, 5), (50, 63)], 55), 10.0)
        self.assertEqual(lesson_coverage([(50, 63)], 55), 5.0)

    def test_syllabus_sentence_lengthens_the_book_window_and_stops_before_mistakes(self):
        transcript = {
            "language": "en",
            "segments": [
                _segment(0, 36.98, 41.94, "The exam syllabus is complicated", [_word("syllabus", 38.2, 38.8)]),
                _segment(1, 41.98, 46.72, "They did not recommend a book", [_word("book", 44.84, 45.10)]),
                _segment(2, 46.76, 55.66, "you will make mistakes", [_word("mistakes", 48.0, 48.4)]),
            ],
        }
        rows = [row for row in broll_opportunities(transcript) if row["action"] == "search" and row["term"] == "book"]
        self.assertEqual(rows[0]["start"], 36.98)
        self.assertEqual(rows[0]["end"], 46.72)

    def test_clip_take_follows_the_window_and_does_not_invent_length(self):
        self.assertEqual(
            clip_take_seconds({"clip_in": 7, "duration_seconds": 17.624, "source_window": [36.98, 46.72]}),
            9.89,
        )
        self.assertEqual(clip_take_seconds({"clip_in": 7.5, "duration_seconds": 9.0, "source_window": [0, 20]}), 1.45)
        self.assertIsNone(clip_take_seconds({"duration_seconds": 4.0, "source_window": [0.58, 3.84]}))

    def test_second_search_page_uses_an_offset(self):
        seen = {}

        def capture(params):
            seen.update(params)
            return []

        with patch("assets.video_commons._search", capture):
            search_videos("newspaper printing", offset=8)
        self.assertEqual(seen["gsroffset"], "8")

    def test_production_html_has_no_diagnostic_cross_and_broll_covers_the_card(self):
        dataset = load_dataset()
        plan = build_phase2_plan(
            dataset,
            transcript={
                "language": "hi",
                "language_source": "configured",
                "method": "test",
                "model": "none",
                "provenance": "unit",
                "segments": [_segment(0, 0, 8, "nahi newspaper pada")],
            },
            selection={"source_in": 0, "source_out": 8, "duration": 8, "text": "nahi newspaper pada", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={"width": 1920, "height": 1080, "fps": 30, "attribution_line": "test", "license": "not stated", "license_url": "not-stated-in-file"},
            assets=[
                {
                    "id": "press",
                    "path": "assets/press.mp4",
                    "kind": "video",
                    "match_terms": ["newspaper"],
                    "reason": "Press footage with a negation mark",
                    "page_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
                    "license": "CC BY-SA 4.0",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
                    "duration_seconds": 4,
                }
            ],
        )
        plan["render"]["corner_logo"] = "assets/logo.png"
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertNotIn("negation-mark", html)
        self.assertNotIn("grayscale", html)
        self.assertIn("object-fit: cover", html)
        self.assertIn('class="clip broll"', html)
        self.assertIn('class="clip teacher landscape"', html)
        captions = _captions(
            {"decisions": [{"action": "caption", "id": "c1", "text": "na hi maine", "start": 0, "end": 1.2}]},
            "bounded_card_with_title",
            defaults["bounded_card"],
            1280,
        )
        self.assertIn("NA HI MAINE", captions)
        self.assertNotIn("grayscale", outro_filter(848, 478, 720, 1280))
        self.assertNotIn("logo", outro_filter(848, 478, 720, 1280))

    def test_unknown_license_and_a_laptop_are_rejected(self):
        laptop = {
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "download_url": "https://example.invalid/laptop.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "How to open HP EliteBook 830",
            "description": "laptop disassembly",
        }
        self.assertIsNotNone(accept_video_candidate(laptop, "textbook", "koi kitaab"))
        missing = dict(laptop, title="Open textbook pages", description="textbook pages", license_url="")
        self.assertIn("license url", accept_video_candidate(missing, "textbook", "kitaab"))


if __name__ == "__main__":
    unittest.main()
