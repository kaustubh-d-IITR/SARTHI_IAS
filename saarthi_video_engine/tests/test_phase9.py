"""Phase 9: centered title, reference logo, no lesson footer, Hindi subject queries."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import coverage_seconds  # noqa: E402
from assets.concepts import query_variants, visual_queries  # noqa: E402
from assets.video_commons import accept_video_candidate  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from hyperframes_adapter.compose import _captions, _title_html, render_html, render_title_scene  # noqa: E402
from render.frame import reference_frame  # noqa: E402
from render.outro import outro_filter  # noqa: E402


def _segment(start, end, text):
    return {
        "id": "s",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "language": "hi",
        "language_source": "configured",
        "timing_precision": "segment",
    }


class LayoutTests(unittest.TestCase):
    def test_title_is_centered_bold_and_yellow_words_stay_marked(self):
        frame = reference_frame()
        self.assertEqual(frame["title_align"], "center")
        self.assertGreater(frame["title_left"], frame["logo_left"])
        self.assertGreaterEqual(frame["title_size"], 32)
        html = _title_html("You Will Make\n[[Mistakes]]")
        self.assertIn('class="accent"', html)
        self.assertIn("Mistakes", html)
        dataset = load_dataset()
        plan = build_phase2_plan(
            dataset,
            transcript={
                "language": "hi",
                "language_source": "configured",
                "method": "test",
                "model": "none",
                "provenance": "unit",
                "segments": [_segment(0, 8, "kitaab")],
            },
            selection={"source_in": 0, "source_out": 8, "duration": 8, "text": "kitaab", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={
                "width": 1280,
                "height": 720,
                "fps": 30,
                "attribution_line": "Dr. Shivin. Local recording. Music: Yuri Megis, CC BY 4.0.",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0",
            },
            assets=[],
        )
        plan["render"]["corner_logo"] = "assets/logo.png"
        for item in plan["decisions"]:
            if item["action"] == "title":
                item["text"] = "You Will Make\n[[Mistakes]]"
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        lesson = render_html(plan, defaults)
        title = render_title_scene(plan, defaults)
        self.assertIn("text-align: center", title)
        self.assertIn("font-weight: 800", title)
        self.assertIn('class="accent"', title)
        self.assertNotIn("Local recording", lesson)
        self.assertNotIn("Yuri Megis", lesson)
        self.assertNotIn('id="credit"', lesson)
        self.assertIn(f"width: {frame['logo_size']}px", lesson)
        self.assertIn(f"height: {frame['logo_size']}px", lesson)
        self.assertNotIn("credit", outro_filter(848, 478, 720, 1280))

    def test_kitaab_searches_books_and_a_brother_is_not_a_random_person(self):
        book = visual_queries("unhonne koi kitaab nahi recommend kari")
        self.assertIn("textbook", [row["query"] for row in book if row["action"] == "search"])
        variants = query_variants("textbook")
        self.assertEqual(variants[0], "textbook")
        self.assertIn("library bookshelf", variants)
        family = visual_queries("mera koi bhai behen nahi hai")
        self.assertTrue(all(row["action"] == "skip" for row in family))
        self.assertEqual(coverage_seconds([(0, 4), (3, 5), (50, 58)]), 13.0)

    def test_visible_caption_stays_uppercase_without_editing_the_line(self):
        decision = {"action": "caption", "id": "c", "start": 0, "end": 1, "text": "ye book padhani hai"}
        html = _captions({"decisions": [decision]}, "bounded_card_with_title", {}, 1280)
        self.assertIn(">YE BOOK PADHANI HAI<", html)
        self.assertEqual(decision["text"], "ye book padhani hai")

    def test_a_lecture_title_is_not_a_library(self):
        row = {
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "download_url": "https://example.invalid/talk.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "Cultural travel, quote from textbook author.webm",
            "description": "library bookshelf",
        }
        self.assertIsNotNone(accept_video_candidate(row, "library bookshelf", "koi kitaab nahi"))


if __name__ == "__main__":
    unittest.main()
