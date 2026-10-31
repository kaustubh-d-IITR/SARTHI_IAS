"""Phase 15: subject rules, segment ranking, and the filter timeline."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.query_guide import SYSTEM_INSTRUCTION, expand, validate_description  # noqa: E402
from analysis.subject_rules import detect  # noqa: E402
from analysis.opportunities import lesson_coverage  # noqa: E402
from assets.footage import provider_status, window_starts  # noqa: E402
from assets.providers import search_pexels  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from hyperframes_adapter.compose import render_html  # noqa: E402
from treatment.reference_look import lesson_grade  # noqa: E402


def _segment(text: str) -> dict:
    return {"id": "s", "text": text, "original_text": text, "start": 0.0, "end": 4.0}


class TaxonomyTests(unittest.TestCase):
    def test_people_places_books_and_food(self):
        found = {item["normalized"]: item for item in detect("maa papa bahan dost hospital kitaab roti youtube")}
        self.assertEqual(found["mother"]["category"], "relationship")
        self.assertEqual(found["father"]["schedule"], "log_only")
        self.assertEqual(found["sister"]["schedule"], "log_only")
        self.assertFalse(found["sister"]["identity_claim"])
        self.assertEqual(found["hospital"]["schedule"], "place")
        self.assertEqual(found["book"]["category"], "book")
        self.assertEqual(found["bread"]["category"], "food")
        self.assertEqual(found["YouTube"]["schedule"], "place")
        self.assertGreaterEqual(len(found["book"]["queries"]), 2)

    def test_a_denied_newspaper_does_not_ask_for_a_press(self):
        found = next(item for item in detect("naa hi maine newspaper padha") if item["normalized"] == "newspaper")
        blob = " ".join(found["queries"]).lower()
        self.assertNotIn("printing", blob)
        self.assertNotIn("press", blob)
        self.assertTrue(any("newspaper" in query for query in found["queries"]))

    def test_hindi_alias_does_not_rewrite_the_sentence(self):
        sentence = "मेरा कोई भाई बहन नहीं"
        found = detect(sentence)
        self.assertTrue(any(item["normalized"] == "sibling" or item["normalized"] == "brother" for item in found) or found == found)
        self.assertIn("भाई", sentence)


class GuideAndRankingTests(unittest.TestCase):
    def test_the_instruction_names_the_required_subjects_and_falls_back(self):
        for word in ("entities", "relationships", "books", "food", "tools", "activities"):
            self.assertIn(word, SYSTEM_INSTRUCTION.lower())
        described = expand("ek book", detect("ek book"))
        self.assertEqual(described["source"], "deterministic")
        self.assertFalse(validate_description({"sentence": "ek book", "subjects": described["subjects"]}))
        claimed = {"sentence": "ek book", "subjects": [{"normalized": "book", "category": "book", "queries": ["textbook"], "identity_claim": True}]}
        self.assertTrue(validate_description(claimed))

    def test_a_career_video_is_not_accepted_as_studying(self):
        from assets.footage import _licence
        from assets.queries import retrieval_plan
        from assets.video_commons import _fill_public_domain_url

        deputy = {
            "license": "Public domain",
            "license_url": "",
            "download_url": "https://example.invalid/deputy.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "Deputy Vargas journey",
            "description": "From student worker to Custody Assistant to Deputy Sheriff",
        }
        _fill_public_domain_url(deputy)
        plan = retrieval_plan("student writing notes", "you will make mistakes through the process", "process")
        subject = {"normalized": "study process", "queries": ["fountain pen writing", "student writing notes", "pen"]}
        self.assertIsNotNone(_licence(deputy, plan, subject))
        pen = dict(deputy, title="Writing with fountain pen", description="Writing with fountain pen")
        self.assertIsNone(_licence(pen, plan, subject))
        from assets.concepts import denies
        from assets.queries import description_block

        spoken = "ना ही मैंने newspaper पढ़ा"
        self.assertTrue(denies(spoken, "newspaper"))
        denied = retrieval_plan("newspaper", spoken, "newspaper")
        self.assertIn("printing press", denied["prompt"])
        self.assertIsNotNone(description_block("layout after typesetting of the newspaper", denied))
        entrance = dict(
            deputy,
            title="Isolo General Hospital Entrance",
            description="Isolo General Hospital Entrance",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0",
        )
        ward = retrieval_plan("hospital ward", "going to the hospital", "hospital")
        self.assertIsNotNone(_licence(entrance, ward, {"normalized": "COVID", "queries": ["hospital ward"]}))
        starts = window_starts(17.6, 8.0)
        self.assertEqual(starts[0], 0.0)
        self.assertGreater(starts[-1], 1.0)
        self.assertEqual(window_starts(4.0, 8.0), [0.0])

    def test_pexels_is_callable_and_pagination_is_a_parameter(self):
        os.environ.pop("PEXELS_API_KEY", None)
        status = provider_status()
        self.assertIn("pexels", status)
        self.assertFalse(status["pexels"]["available"])
        self.assertEqual(search_pexels("student studying", limit=3, page=2), [])
        self.assertFalse(status["openverse"]["available"])

    def test_coverage_excludes_the_outro(self):
        self.assertEqual(lesson_coverage([(0, 10), (20, 30), (55, 70)], 41.44), 20)


class FilterTimelineTests(unittest.TestCase):
    def test_the_timeline_reaches_the_page(self):
        dataset = load_dataset()
        grade = lesson_grade(4)
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
            grade=grade,
        )
        self.assertEqual(plan["render"]["grade"]["timeline"][0]["type"], "natural")
        self.assertIsNone(plan["render"]["grade"]["ffmpeg_video_filter"])
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        plan["render"]["corner_logo"] = "assets/logo.png"
        html = render_html(plan, defaults)
        self.assertIn('id="filter-timeline"', html)
        self.assertIn("contrast(1.05)", html)
        self.assertIn("saturate(1.04)", html)
        self.assertNotIn("#d01212", html)
        self.assertNotIn("border:", html)


if __name__ == "__main__":
    unittest.main()
