"""Phase 20 reference examples. The dataset file is read locally. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.reference_edits import choose_cutaway, examples_from_dataset  # noqa: E402
from dataset_adapter.loader import load_dataset, partial_ids  # noqa: E402


class ReferenceExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()
        cls.examples = examples_from_dataset(cls.dataset)

    def test_partial_records_are_not_examples(self):
        present = {row["reference_id"] for row in self.examples}
        for ref in partial_ids(self.dataset):
            self.assertNotIn(ref, present)
        self.assertEqual(set(partial_ids(self.dataset)), {"SHORT_30", "SHORT_32", "SHORT_37"})

    def test_cutaways_and_teacher_intervals_are_both_kept(self):
        self.assertTrue(any(row["replaced_teacher"] for row in self.examples))
        self.assertTrue(any(not row["replaced_teacher"] for row in self.examples))
        cut = next(row for row in self.examples if row["replaced_teacher"])
        self.assertGreater(cut["duration_seconds"], 0)
        self.assertTrue(cut["spoken_phrase"])
        self.assertEqual(cut["version"], "phase20.1")

    def test_the_spoken_line_is_not_rewritten(self):
        text = "ना ही मैंने newspaper पढ़ा"
        decision = choose_cutaway(text, 0.12, 3.84, self.examples)
        self.assertEqual(decision["spoken_text"], text)
        self.assertEqual(decision["feasibility"]["object"], "newspaper")
        self.assertTrue(decision["feasibility"]["negation"])

    def test_a_relative_line_is_not_a_cutaway(self):
        decision = choose_cutaway("मेरा कोई भाई बहन नहीं", 0.0, 2.0, self.examples)
        self.assertFalse(decision["select_cutaway"])
        self.assertEqual(decision["feasibility"]["visual_type"], "none")

    def test_a_rejected_or_unavailable_clip_is_not_scheduled(self):
        from broll_intel.director import refine

        rejected = refine({"retrieval_status": "rejected", "selected_asset_id": "http://x", "selected_source_in": 1}, set())
        waiting = refine({"retrieval_status": "API_UNAVAILABLE", "selected_asset_id": "http://y"}, set())
        self.assertEqual(rejected["schedule"], "teacher")
        self.assertEqual(waiting["schedule"], "review")
        self.assertNotEqual(waiting["schedule"], "broll")


class RecoveryTests(unittest.TestCase):
    def test_a_cached_accept_is_used_only_when_this_search_returns_it(self):
        from broll_intel.recovery import fill_selected

        page = "https://commons.wikimedia.org/wiki/File:Writing_with_fountain_pen.mpg"
        other = "https://commons.wikimedia.org/wiki/File:Other.mp4"
        local = Path(__file__).parent / "_phase20_pen.txt"
        local.write_text("cached", encoding="utf-8")
        self.addCleanup(local.unlink, missing_ok=True)
        decision = _decision("तो process के through जाओगे", "study process", ["fountain pen writing on paper"])
        prior = {page: {"decision": "ACCEPT", "object": "study process", "source_in": 5.24, "source_out": 9.28, "local_path": str(local)}}

        missed = fill_selected(
            decision,
            search=lambda query: [_file(other, "student writing notes at a desk and paper")],
            download=lambda hit, cache: None,
            rank=lambda *args: (_ for _ in ()).throw(AssertionError("rank")),
            verify=lambda *args: (_ for _ in ()).throw(AssertionError("verify")),
            prior=prior,
            blocked_pages=set(),
            cache=Path("."),
        )
        self.assertNotEqual(missed["selected_asset_id"], page)
        self.assertNotEqual(missed["retrieval_status"], "verified")

        found = fill_selected(
            decision,
            search=lambda query: [_file(page, "Writing with fountain pen")],
            download=lambda hit, cache: (_ for _ in ()).throw(AssertionError("download")),
            rank=lambda *args: (_ for _ in ()).throw(AssertionError("rank")),
            verify=lambda *args: (_ for _ in ()).throw(AssertionError("verify")),
            prior=prior,
            blocked_pages=set(),
            cache=Path("."),
        )
        self.assertEqual(found["retrieval_status"], "verified")
        self.assertEqual(found["selected_asset_id"], page)
        self.assertEqual(found["selected_source_in"], 5.24)
        self.assertEqual(decision["spoken_text"], "तो process के through जाओगे")
        self.assertTrue(found["verification_evidence"]["verification_reused"])

    def test_rejection_retries_and_an_api_failure_is_not_accepted(self):
        from broll_intel.gemini_api import GeminiError
        from broll_intel.recovery import fill_selected
        from broll_intel.schema import VerificationResult

        first = "https://commons.wikimedia.org/wiki/File:One.mp4"
        second = "https://commons.wikimedia.org/wiki/File:Two.mp4"
        decision = _decision("ना ही मैंने newspaper पढ़ा", "newspaper", ["person reading a printed newspaper"])
        calls = {"n": 0}

        def search(query):
            return [
                _file(first, "person reading a printed newspaper"),
                _file(second, "person reading a printed newspaper"),
            ]

        def verify(frames, row):
            calls["n"] += 1
            if calls["n"] == 1:
                return VerificationResult(
                    decision="ACCEPT",
                    subject_match=False,
                    action_match=False,
                    contradiction_detected=True,
                    confidence=1.0,
                    reason="shelves, not a newspaper",
                )
            raise GeminiError("503 unavailable")

        outcome = fill_selected(
            decision,
            search=search,
            download=lambda hit, cache: Path("clip.mp4"),
            rank=lambda path, prompt, take, cache: (0.4, 1.2, ["a.jpg", "b.jpg"]),
            verify=verify,
            prior={},
            blocked_pages=set(),
            cache=Path("."),
            max_downloads=2,
            max_verifies=2,
        )
        self.assertEqual(outcome["retrieval_status"], "rejected")
        self.assertEqual(outcome["selected_asset_id"], "")
        self.assertEqual(decision["spoken_text"], "ना ही मैंने newspaper पढ़ा")
        self.assertIn("API_UNAVAILABLE", {item["status"] for item in outcome["attempts"]})

    def test_mpeg_is_eligible_when_the_search_returns_a_verified_pen(self):
        from broll_intel.recovery import fill_selected

        page = "https://commons.wikimedia.org/wiki/File:Writing_with_fountain_pen.mpg"
        local = Path(__file__).parent / "_phase20_mpeg.txt"
        local.write_text("cached", encoding="utf-8")
        self.addCleanup(local.unlink, missing_ok=True)
        hit = _file(page, "Writing with fountain pen")
        hit["mime"] = "video/mpeg"
        decision = _decision("process ke through jaoge", "study process", ["fountain pen writing"])
        outcome = fill_selected(
            decision,
            search=lambda query: [hit],
            download=lambda *args: (_ for _ in ()).throw(AssertionError("download")),
            rank=lambda *args: (_ for _ in ()).throw(AssertionError("rank")),
            verify=lambda *args: (_ for _ in ()).throw(AssertionError("verify")),
            prior={
                page: {
                    "decision": "ACCEPT",
                    "object": "study process",
                    "source_in": 5.24,
                    "source_out": 9.28,
                    "local_path": str(local),
                }
            },
            blocked_pages=set(),
            cache=Path("."),
        )
        self.assertEqual(outcome["retrieval_status"], "verified")
        self.assertEqual(outcome["selected_source_in"], 5.24)

    def test_an_empty_scene_query_retries_the_shorter_action(self):
        from broll_intel.recovery import fill_selected

        page = "https://commons.wikimedia.org/wiki/File:Writing_with_fountain_pen.mpg"
        local = Path(__file__).parent / "_phase20_pen_short.txt"
        local.write_text("cached", encoding="utf-8")
        self.addCleanup(local.unlink, missing_ok=True)
        asked = []

        def search(query):
            asked.append(query)
            if query == "fountain pen writing":
                return [_file(page, "Writing with fountain pen")]
            return []

        decision = _decision("process ke through jaoge", "study process", ["fountain pen writing on paper"])
        prior = {
            page: {
                "decision": "ACCEPT",
                "object": "study process",
                "source_in": 5.24,
                "source_out": 9.28,
                "local_path": str(local),
            }
        }
        outcome = fill_selected(
            decision,
            search=search,
            download=lambda hit, cache: (_ for _ in ()).throw(AssertionError("download")),
            rank=lambda *args: (_ for _ in ()).throw(AssertionError("rank")),
            verify=lambda *args: (_ for _ in ()).throw(AssertionError("verify")),
            prior=prior,
            blocked_pages=set(),
            cache=Path("."),
        )
        self.assertIn("fountain pen writing", asked)
        self.assertEqual(outcome["retrieval_status"], "verified")
        self.assertEqual(outcome["verification_evidence"]["query"], "fountain pen writing")
        self.assertTrue(outcome["verification_evidence"]["found_by_live_search"])

    def test_a_low_rank_is_not_verified(self):
        from broll_intel.recovery import fill_selected

        decision = _decision("a lecture hall", "lecture", ["person giving a lecture"])

        def verify(*args):
            raise AssertionError("verifier")

        outcome = fill_selected(
            decision,
            search=lambda query: [_file("https://commons.wikimedia.org/wiki/File:Talk.mp4", "person giving a lecture")],
            download=lambda hit, cache: Path("talk.mp4"),
            rank=lambda path, prompt, take, cache: (0.1, 0.0, ["a.jpg", "b.jpg"]),
            verify=verify,
            prior={},
            blocked_pages=set(),
            cache=Path("."),
        )
        self.assertEqual(outcome["retrieval_status"], "unfilled")
        self.assertEqual(outcome["attempts"][0]["status"], "below_rank")


def _decision(text: str, obj: str, queries: list[str]) -> dict:
    return {
        "spoken_text": text,
        "speech_start": 1.0,
        "speech_end": 4.2,
        "lesson_id": "VIDEO_1",
        "blocked_terms": ["mistakes", "upsc"],
        "editorial": {"examples": []},
        "feasibility": {
            "object": obj,
            "visual_brief": queries[0],
            "search_queries": queries,
            "forbidden_visual_elements": [],
            "overlay_instructions": "",
        },
    }


def _file(page: str, description: str) -> dict:
    return {
        "title": description,
        "page_url": page,
        "download_url": "https://example.invalid/clip.mp4",
        "mime": "video/mp4",
        "size": 80_000,
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
        "artist": "Tester",
        "description": description,
    }


if __name__ == "__main__":
    unittest.main()
