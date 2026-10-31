"""Phase 19 director. No live API calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.director import DirectorRead, analyze, assets_for_render, merge_director, refine  # noqa: E402


class DirectorTests(unittest.TestCase):
    def test_newspaper_denial_keeps_the_object_when_the_model_says_maine(self):
        model = DirectorRead(subject="maine", object="maine", negation=True, search_queries=["newspaper"])
        found = analyze("ना ही मैंने newspaper पढ़ा", model=model)
        self.assertEqual(found["agent"], "speaker")
        self.assertEqual(found["object"], "newspaper")
        self.assertTrue(found["negation"])
        self.assertEqual(found["overlay_instructions"], "negation_cross")
        self.assertTrue(all(len(query.split()) >= 3 for query in found["search_queries"]))
        self.assertNotIn("newspaper", found["search_queries"])

    def test_ncert_denial_asks_for_a_textbook(self):
        found = analyze("Na hi maine NCERT padha", model=DirectorRead(object="maine", negation=True))
        self.assertEqual(found["object"], "NCERT")
        self.assertEqual(found["action"], "reading")
        self.assertTrue(any("textbook" in query for query in found["search_queries"]))

    def test_preparing_blindly_is_not_a_shelf(self):
        found = analyze("I started preparing blindly")
        self.assertEqual(found["object"], "studying")
        self.assertNotIn("library shelves", " ".join(found["forbidden_visual_elements"]))
        self.assertTrue(found["search_queries"])

    def test_youtube_queries_name_a_phone(self):
        found = analyze("Maine bhi YouTube khola tha")
        self.assertEqual(found["object"], "YouTube")
        self.assertTrue(any("phone" in query or "mobile" in query for query in found["search_queries"]))

    def test_a_model_shelf_query_is_dropped(self):
        model = DirectorRead(
            object="newspaper",
            negation=True,
            search_queries=["library shelves of books", "printed newspaper lying unopened on a desk"],
        )
        found = analyze("ना ही मैंने newspaper पढ़ा", model=model)
        joined = " ".join(found["search_queries"]).lower()
        self.assertNotIn("library shelves", joined)

    def test_rejection_cannot_be_scheduled(self):
        row = {"retrieval_status": "rejected", "selected_asset_id": "https://example.invalid/navy", "selected_source_in": 1.0}
        chosen = refine(row, set())
        self.assertEqual(chosen["schedule"], "teacher")
        self.assertEqual(chosen["selected_asset_id"], "")

    def test_verified_bounds_are_kept_and_a_repeat_is_dropped(self):
        row = {
            "retrieval_status": "verified",
            "selected_asset_id": "https://example.invalid/pen",
            "selected_source_in": 5.24,
            "selected_source_out": 9.28,
        }
        used: set[str] = set()
        first = refine(row, used)
        second = refine(dict(row), used)
        self.assertEqual(first["schedule"], "broll")
        self.assertEqual(first["selected_source_in"], 5.24)
        self.assertEqual(second["schedule"], "teacher")

    def test_an_api_failure_stays_in_review(self):
        chosen = refine({"retrieval_status": "API_UNAVAILABLE", "selected_asset_id": "https://example.invalid/ward"}, set())
        self.assertEqual(chosen["schedule"], "review")
        self.assertTrue(chosen["review_required"])

    def test_render_assets_use_the_verified_in_point(self):
        plans = {
            "VIDEO_1": [
                {
                    "retrieval_status": "verified",
                    "selected_asset_id": "https://example.invalid/pen",
                    "selected_source_in": 5.24,
                    "overlay_type": "",
                }
            ]
        }
        catalogue = {
            "https://example.invalid/pen": {
                "page_url": "https://example.invalid/pen",
                "license": "CC BY-SA 4.0",
                "local_path": "pen.mpg",
                "clip_in": 0.05,
            }
        }
        assets = assets_for_render(plans, catalogue)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["clip_in"], 5.24)

    def test_merge_does_not_clear_a_verification(self):
        row = {
            "retrieval_status": "verified",
            "selected_asset_id": "https://example.invalid/pen",
            "selected_source_in": 5.24,
            "verification_evidence": {"page_url": "https://example.invalid/pen"},
        }
        merged = merge_director(row, analyze("you will make mistakes जब आप पूरे process के through जाओगे"), "rules")
        self.assertEqual(merged["retrieval_status"], "verified")
        self.assertEqual(merged["selected_source_in"], 5.24)
        self.assertEqual(merged["director"]["source"], "rules")
        self.assertNotIn("AIza", str(merged))


if __name__ == "__main__":
    unittest.main()
