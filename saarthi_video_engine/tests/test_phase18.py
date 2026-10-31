"""Phase 18: pronoun grounding and retry classification. No live API calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.gemini_api import backoff_seconds, failure_class  # noqa: E402
from broll_intel.roles import interpret  # noqa: E402


class RoleTests(unittest.TestCase):
    def test_maine_is_the_speaker_and_newspaper_is_the_object(self):
        found = interpret("ना ही मैंने newspaper पढ़ा", model_subject="maine", model_negation=True)
        self.assertEqual(found["agent"], "speaker")
        self.assertEqual(found["object"], "newspaper")
        self.assertTrue(found["negation"])
        self.assertEqual(found["overlay_type"], "negation_cross")
        self.assertIn("library shelves", " ".join(found["forbidden_visuals"]))

    def test_a_youtube_sentence_keeps_the_phone_action(self):
        found = interpret("Maine YouTube khola", model_subject="maine")
        self.assertEqual(found["object"], "YouTube")
        self.assertNotEqual(found["object"], "maine")

    def test_a_relative_sentence_has_no_stock_object(self):
        found = interpret("मेरा कोई भाई बहन नहीं")
        self.assertEqual(found["object"], "")
        self.assertEqual(found["visual_type"], "none")


class MoreRoleTests(unittest.TestCase):
    def test_ncert_denial_names_the_textbook(self):
        found = interpret("Na hi maine NCERT padha", model_subject="maine", model_negation=True)
        self.assertEqual(found["object"], "NCERT")
        self.assertEqual(found["action"], "reading")
        self.assertTrue(found["negation"])
        self.assertEqual(found["overlay_type"], "negation_cross")

    def test_preparing_without_a_plan_stays_a_study_action(self):
        found = interpret("I started preparing blindly")
        self.assertEqual(found["object"], "studying")
        self.assertEqual(found["visual_type"], "literal")
        self.assertNotEqual(found["visual_type"], "symbolic")

    def test_hospital_arrival_is_a_clinical_scene(self):
        found = interpret("I am going to the hospital")
        self.assertEqual(found["object"], "hospital")
        self.assertEqual(found["visual_type"], "literal")

    def test_a_named_exam_does_not_become_stock_footage(self):
        found = interpret("UPSC is the exam")
        self.assertEqual(found["object"], "")
        self.assertEqual(found["visual_type"], "none")

    def test_an_abstract_aside_stays_on_the_teacher(self):
        found = interpret("that is okay")
        self.assertEqual(found["visual_type"], "none")
        self.assertEqual(found["exact_transcript"], "that is okay")


class RetryTests(unittest.TestCase):
    def test_overload_is_retryable_and_a_missing_model_is_not(self):
        self.assertEqual(failure_class("503 UNAVAILABLE high demand"), "retry")
        self.assertEqual(failure_class("504 DEADLINE_EXCEEDED"), "retry")
        self.assertEqual(failure_class("404 NOT_FOUND"), "fatal")
        self.assertEqual(failure_class("RESOURCE_EXHAUSTED quota"), "quota")

    def test_backoff_is_bounded(self):
        for attempt in range(4):
            delay = backoff_seconds(attempt)
            self.assertGreater(delay, 0)
            self.assertLess(delay, 45)

    def test_client_errors_are_not_retried(self):
        self.assertEqual(failure_class("400 INVALID_ARGUMENT"), "fatal")
        self.assertEqual(failure_class("401 API key not valid"), "fatal")
        self.assertEqual(failure_class("403 PERMISSION_DENIED"), "fatal")


class PipelineSafetyTests(unittest.TestCase):
    def test_a_repeated_rejected_page_is_not_sent_again(self):
        from run_phase18 import _resume

        rows = [
            {
                "retrieval_status": "rejected",
                "transcript_segment_id": "a9",
                "verification_evidence": {"page_url": "https://example.invalid/shelves"},
            },
            {
                "retrieval_status": "verifier_failed",
                "transcript_segment_id": "a10",
                "verification_evidence": {"page_url": "https://example.invalid/shelves"},
                "schedule": "review",
            },
        ]
        calls = []
        finished = _resume("VIDEO_1", rows, calls)
        self.assertEqual(finished[1]["retrieval_status"], "rejected")
        self.assertEqual(calls[0]["status"], "cached_reject")

    def test_redaction_removes_a_key(self):
        import os

        from broll_intel.gemini_api import _redact

        previous = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "TESTKEYVALUE123"
        try:
            text = _redact("request failed TESTKEYVALUE123")
        finally:
            if previous is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous
        self.assertNotIn("TESTKEYVALUE123", text)

    def test_a_missing_license_is_rejected(self):
        from assets.video_commons import accept_video_candidate

        reason = accept_video_candidate(
            {
                "license": "",
                "license_url": "",
                "download_url": "https://example.invalid/a.webm",
                "mime": "video/webm",
                "size": 80_000,
                "title": "a person writing notes",
                "description": "a person writing notes",
            },
            "writing notes",
        )
        self.assertIn("license", reason or "")

    def test_overlapping_intervals_are_counted_once(self):
        from broll_intel.coverage import union_seconds

        self.assertEqual(union_seconds([(0.0, 4.0), (2.0, 5.0)]), 5.0)


if __name__ == "__main__":
    unittest.main()
