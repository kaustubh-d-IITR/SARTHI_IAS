"""Phase 20 recovery. Prior acceptances are local records. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.retain import retain_verified  # noqa: E402


def _current(schedule: str = "teacher", status: str = "teacher", page: str = "") -> dict:
    return {
        "lesson_id": "VIDEO_2",
        "transcript_segment_id": "a6",
        "speech_start": 22.94,
        "speech_end": 27.16,
        "exact_transcript": "PPE kit and a N95 mask for the last 12 hours. Still I am coming back and trying",
        "schedule": schedule,
        "retrieval_status": status,
        "selected_asset_id": page,
        "selected_source_in": 40.0 if page else None,
        "selected_source_out": 44.22 if page else None,
        "verification_evidence": {"page_url": page} if page else {},
    }


def _prior(path: Path, status: str = "verified", decision: str = "ACCEPT") -> dict:
    return {
        "retrieval_status": status,
        "selected_asset_id": "https://commons.wikimedia.org/wiki/File:ppe.webm",
        "selected_source_in": 40.0,
        "selected_source_out": 44.22,
        "speech_start": 22.94,
        "speech_end": 27.16,
        "exact_transcript": "must not replace the current line",
        "decision_reason": "The gown and mask are visible.",
        "verification_evidence": {
            "local_path": str(path),
            "gemini": {"decision": decision},
            "page_url": "https://commons.wikimedia.org/wiki/File:ppe.webm",
        },
    }


class RetainTests(unittest.TestCase):
    def setUp(self):
        self.asset = Path(__file__).parent / "_phase20_recovery_asset.txt"
        self.asset.write_text("asset", encoding="utf-8")
        self.addCleanup(self.asset.unlink, missing_ok=True)

    def test_a_selector_with_no_cutaway_keeps_a_valid_acceptance(self):
        row = retain_verified(_current(), _prior(self.asset))
        self.assertEqual(row["schedule"], "broll")
        self.assertEqual(row["retrieval_status"], "verified")
        self.assertEqual(row["selected_source_in"], 40.0)
        self.assertEqual(row["selected_source_out"], 44.22)
        self.assertEqual(row["speech_start"], 22.94)
        self.assertEqual(row["speech_end"], 27.16)
        self.assertEqual(row["exact_transcript"], "PPE kit and a N95 mask for the last 12 hours. Still I am coming back and trying")
        self.assertEqual(row["retain"]["action"], "restored")

    def test_an_api_failure_does_not_accept_or_erase(self):
        failed = _current(schedule="review", status="API_UNAVAILABLE")
        kept = retain_verified(failed, _prior(self.asset))
        self.assertEqual(kept["retrieval_status"], "verified")
        self.assertNotEqual(kept["retain"]["action"], "not_reused")
        bare = retain_verified(failed, None)
        self.assertEqual(bare["retrieval_status"], "API_UNAVAILABLE")
        self.assertNotEqual(bare["schedule"], "broll")
        unavailable = _prior(self.asset, status="API_UNAVAILABLE", decision="")
        left = retain_verified(_current(), unavailable)
        self.assertEqual(left["schedule"], "teacher")
        self.assertIn("not an acceptance", left["retain"]["reason"])

    def test_a_rejected_visual_is_not_inserted(self):
        rejected = _prior(self.asset, status="rejected", decision="REJECT")
        row = retain_verified(_current(), rejected)
        self.assertEqual(row["schedule"], "teacher")
        self.assertNotEqual(row["retrieval_status"], "verified")
        same = _current(schedule="teacher", status="rejected", page="https://commons.wikimedia.org/wiki/File:ppe.webm")
        blocked = retain_verified(same, _prior(self.asset))
        self.assertNotEqual(blocked["schedule"], "broll")

    def test_a_missing_file_is_not_reused(self):
        prior = _prior(self.asset)
        prior["verification_evidence"]["local_path"] = str(self.asset.with_name("missing-clip.webm"))
        row = retain_verified(_current(), prior)
        self.assertEqual(row["schedule"], "teacher")
        self.assertIn("no longer on disk", row["retain"]["reason"])

    def test_the_other_lesson_is_left_alone(self):
        video1 = _current()
        video1["lesson_id"] = "VIDEO_1"
        video1["transcript_segment_id"] = "a11"
        video1["exact_transcript"] = "तो you will make mistakes जब आप पूरे process के through जाओगे"
        video1["schedule"] = "broll"
        video1["retrieval_status"] = "verified"
        video1["selected_asset_id"] = "https://commons.wikimedia.org/wiki/File:pen.mpg"
        video1["selected_source_in"] = 5.24
        kept = retain_verified(video1, _prior(self.asset))
        self.assertEqual(kept["selected_asset_id"], "https://commons.wikimedia.org/wiki/File:pen.mpg")
        self.assertEqual(kept["selected_source_in"], 5.24)
        self.assertEqual(kept["retain"]["action"], "kept_current")


if __name__ == "__main__":
    unittest.main()
