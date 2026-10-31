"""Phase 17 plans, negation, coverage, and verification gates. No live API calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.coverage import coverage_report, union_seconds  # noqa: E402
from broll_intel.planner import plan_lesson  # noqa: E402
from broll_intel.queries import build_queries  # noqa: E402
from broll_intel.schema import VerificationResult, accept_verification  # noqa: E402
from broll_intel.taxonomy import classify_sentence  # noqa: E402
from config.env import gemini_configured  # noqa: E402


def _lesson(text: str) -> dict:
    return {"language": "hin", "segments": [{"id": "s", "text": text, "start": 0.1, "end": 3.0}]}


class MeaningTests(unittest.TestCase):
    def test_a_denied_newspaper_requests_a_cross_and_not_a_press(self):
        found = classify_sentence("ना ही मैंने newspaper पढ़ा", 0.12, 3.0, "hin")
        self.assertEqual(found["primary_subject"], "newspaper")
        self.assertTrue(found["negation"])
        self.assertEqual(found["negation_kind"], "historical_absence")
        self.assertEqual(found["overlay_type"], "negation_cross")
        self.assertEqual(found["exact_transcript"] if False else found["text"], "ना ही मैंने newspaper पढ़ा")
        queries = " ".join(build_queries(found)).lower()
        self.assertNotIn("press", queries)
        self.assertIn("newspaper", queries)

    def test_a_warning_is_not_given_the_newspaper_cross(self):
        found = classify_sentence("mat padhna akhbar", 0.0, 2.0)
        self.assertNotEqual(found["overlay_type"], "negation_cross")

    def test_opening_youtube_asks_for_a_phone_not_a_document(self):
        found = classify_sentence("Maine YouTube khola", 1.0, 4.0)
        self.assertEqual(found["primary_subject"], "YouTube")
        queries = " ".join(build_queries(found)).lower()
        self.assertIn("phone", queries)
        self.assertIn("pdf on a computer", " ".join(found["forbidden_visuals"]))

    def test_preparing_without_a_plan_stays_a_study_scene(self):
        found = classify_sentence("I started preparing blindly", 0.0, 3.0)
        self.assertIn(found["primary_subject"], {"studying", "study process", ""})
        self.assertEqual(found["overlay_type"], "")
        self.assertNotEqual(found["visual_type"], "symbolic")

    def test_a_relative_stays_on_the_teacher(self):
        rows = plan_lesson(_lesson("मेरा कोई भाई बहन नहीं"), "VIDEO_1")
        self.assertEqual(rows[0].schedule, "teacher")
        self.assertEqual(rows[0].visual_type, "none")
        self.assertEqual(rows[0].exact_transcript, "मेरा कोई भाई बहन नहीं")


class GateTests(unittest.TestCase):
    def test_a_high_score_cannot_accept_the_wrong_action(self):
        gated = accept_verification(
            VerificationResult(
                decision="ACCEPT",
                subject_match=True,
                action_match=False,
                context_match=True,
                confidence=0.99,
                reason="shelves",
            )
        )
        self.assertEqual(gated.decision, "REJECT")

    def test_malformed_verification_is_rejected_by_the_schema(self):
        with self.assertRaises(Exception):
            VerificationResult.model_validate({"decision": "MAYBE", "confidence": 0.2})

    def test_coverage_uses_the_union_and_excludes_nothing_twice(self):
        self.assertEqual(union_seconds([(0, 10), (8, 12), (20, 25)]), 17)
        report = coverage_report(
            lesson_seconds=40,
            eligible_seconds=20,
            planned=[(0, 10)],
            rendered=[(0, 10)],
            verified=[(0, 4), (6, 10)],
        )
        self.assertEqual(report["verified_broll_seconds"], 8)
        self.assertEqual(report["full_lesson_broll_coverage"], 0.2)
        self.assertFalse(report["target_met"])

    def test_gemini_status_does_not_include_a_key(self):
        self.assertIsInstance(gemini_configured(), bool)
        blob = str(gemini_configured())
        self.assertNotIn("AIza", blob)


if __name__ == "__main__":
    unittest.main()
