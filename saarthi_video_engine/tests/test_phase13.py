"""Phase 13: full-canvas dots, the reference look, and VIDEO_1 subject coverage."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import coverage_seconds, lesson_coverage  # noqa: E402
from analysis.subjects import opportunity_records, subjects_in_text  # noqa: E402
from analysis.uniqueness import enforce_unique  # noqa: E402
from hyperframes_adapter.compose import _broll_fades, render_html  # noqa: E402
from render.frame import reference_frame  # noqa: E402
from render.halftone import coverage_bands, halftone_svg, pattern_is_animated  # noqa: E402
from render.outro import outro_filter  # noqa: E402
from treatment.reference_look import filter_timeline, lesson_grade  # noqa: E402


def _segment(text: str, start: float = 0.0, end: float = 4.0) -> dict:
    return {
        "id": "s",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "timing_precision": "segment",
    }


class SubjectCoverageTest(unittest.TestCase):
    def test_family_prepare_stays_on_the_teacher(self):
        found = subjects_in_text("main usko guide kar raha hota chhoti behen ko ki aapko aise prepare karna hai")
        prepare = next(item for item in found if item["term"] == "prepare")
        self.assertEqual(prepare["action"], "search")
        self.assertEqual(prepare["schedule"], "log_only")
        self.assertIn("sister", prepare["reason"])

    def test_audience_process_is_a_study_search(self):
        found = subjects_in_text("you will make mistakes jab aap poore process ke through jaoge")
        process = next(item for item in found if item["normalized"] == "study process")
        self.assertEqual(process["action"], "search")
        self.assertEqual(process["query"], "student writing notes")
        self.assertEqual(process["category"], "activity")

    def test_proper_nouns_and_books_are_classified(self):
        found = subjects_in_text("UPSC ne syllabus likha aur koi book recommend nahi ki")
        by_name = {item["normalized"]: item for item in found}
        self.assertEqual(by_name["UPSC"]["action"], "search")
        self.assertEqual(by_name["UPSC"]["schedule"], "log_only")
        self.assertTrue(by_name["UPSC"]["proper_noun"])
        self.assertEqual(by_name["book"]["category"], "book")
        self.assertEqual(by_name["book"]["action"], "search")

    def test_one_record_per_sentence(self):
        records = opportunity_records({"segments": [_segment("I don't have a sibling. mera koi bhai behen nahi")]})
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["teacher_preferred"])

    def test_same_page_is_not_reused(self):
        page = "https://commons.wikimedia.org/wiki/File:Example.webm"
        rows = [
            {"page_url": page, "sha256": "a" * 64, "rejected": False, "source_window": [1, 4]},
            {"page_url": page, "sha256": "b" * 64, "rejected": False, "source_window": [5, 8]},
        ]
        kept, rejected = enforce_unique(rows)
        self.assertEqual(rejected, 1)
        self.assertTrue(kept[1]["rejected"])

    def test_coverage_drops_the_outro(self):
        self.assertEqual(lesson_coverage([(0, 10), (50, 60)], 41.44), 10)
        self.assertEqual(coverage_seconds([(0, 5), (4, 8)]), 8)


class BackgroundAndFilterTest(unittest.TestCase):
    def test_dots_cover_every_vertical_third(self):
        bands = coverage_bands()
        self.assertTrue(bands["top"] and bands["middle"] and bands["bottom"])
        self.assertFalse(pattern_is_animated())
        svg = halftone_svg()
        self.assertNotIn("#d01212", svg)
        self.assertNotIn("animate", svg)

    def test_card_does_not_paint_a_black_patch(self):
        from dataset_adapter.loader import load_dataset
        from editing_plan.phase2 import build_phase2_plan

        dataset = load_dataset()
        plan = build_phase2_plan(
            dataset,
            transcript={
                "language": "hi",
                "language_source": "configured",
                "method": "test",
                "model": "none",
                "provenance": "unit",
                "segments": [_segment("book")],
            },
            selection={"source_in": 0, "source_out": 4, "duration": 4, "text": "book", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={"width": 1280, "height": 720, "fps": 30, "attribution_line": "test", "license": "not stated", "license_url": "not-stated-in-file"},
            assets=[],
        )
        plan["render"]["corner_logo"] = "assets/logo.png"
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertIn("image/svg+xml", html)
        self.assertIn("background: transparent", html)
        self.assertNotIn("background: #000;", html)
        self.assertNotIn("#d01212", html)
        self.assertNotIn("border:", html)
        self.assertIn("object-fit: contain", html)
        self.assertIn("#canvas-bg { position: absolute; inset: 0;", html)

    def test_filter_timeline_stays_in_colour_and_fades_inserts(self):
        grade = lesson_grade(41.44)
        self.assertIsNone(grade["ffmpeg_video_filter"])
        timeline = filter_timeline(41.44)
        self.assertFalse(any(item.get("desaturate") for item in timeline))
        self.assertEqual(timeline[0]["type"], "natural")
        self.assertEqual(timeline[1]["type"], "crossfade")
        self.assertLessEqual(timeline[1]["duration"], 0.5)
        fades = _broll_fades(
            {"decisions": [{"action": "replace_with_broll", "id": "b1", "start": 2.0, "end": 6.0}]}
        )
        self.assertIn("opacity: 0.42", fades)
        self.assertIn("#b1", fades)
        self.assertNotIn("drawbox", outro_filter(848, 478, 720, 1280))
        self.assertNotIn("#d01212", outro_filter(848, 478, 720, 1280))


if __name__ == "__main__":
    unittest.main()
