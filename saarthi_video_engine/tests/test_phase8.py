"""Phase 8: public-domain metadata, query variants, windows, and uppercase captions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import broll_opportunities, coverage_seconds  # noqa: E402
from assets.concepts import query_variants, visual_queries  # noqa: E402
from assets.video_commons import _fill_public_domain_url, accept_video_candidate  # noqa: E402
from hyperframes_adapter.compose import _captions  # noqa: E402


def _word(text, start, end):
    return {"text": text, "start": start, "end": end, "logprob": -0.1}


class OpportunityTests(unittest.TestCase):
    def test_book_query_is_textbook_not_open_book(self):
        rows = visual_queries("unhonne koi kitaab nahi recommend kari")
        searches = [row["query"] for row in rows if row["action"] == "search"]
        self.assertIn("textbook", searches)
        self.assertNotIn("open book", searches)
        self.assertEqual(query_variants("textbook")[0], "textbook")
        self.assertGreater(len(query_variants("medical protective clothing")), 1)

    def test_two_newspaper_mentions_stay_apart_from_the_hospital_line(self):
        transcript = {
            "language": "hin",
            "segments": [
                {
                    "id": "a0",
                    "text": "na hi maine newspaper padha",
                    "original_text": "na hi maine newspaper padha",
                    "start": 0.12,
                    "end": 3.84,
                    "words": [_word("newspaper", 0.58, 0.92)],
                },
                {
                    "id": "a1",
                    "text": "yaar newspaper padh lo I am going to the hospital",
                    "original_text": "yaar newspaper padh lo I am going to the hospital",
                    "start": 15.78,
                    "end": 19.80,
                    "words": [_word("newspaper", 16.46, 16.90), _word("hospital", 19.08, 19.46)],
                },
                {
                    "id": "a2",
                    "text": "a COVID duty I am coming back",
                    "original_text": "a COVID duty I am coming back",
                    "start": 19.86,
                    "end": 22.88,
                    "words": [_word("COVID", 19.96, 20.16)],
                },
                {
                    "id": "a3",
                    "text": "PPE kit and a N95 mask",
                    "original_text": "PPE kit and a N95 mask",
                    "start": 22.94,
                    "end": 27.16,
                    "words": [_word("PPE", 22.94, 23.20)],
                },
            ],
        }
        rows = [row for row in broll_opportunities(transcript) if row["action"] == "search"]
        by_term = {}
        for row in rows:
            by_term.setdefault(row["term"], []).append((row["start"], row["end"]))
        self.assertGreaterEqual(len(by_term["newspaper"]), 2)
        self.assertLessEqual(by_term["newspaper"][1][1], by_term["hospital"][0][0])
        self.assertGreaterEqual(by_term["ppe"][0][0], by_term["hospital"][0][1] - 0.2)
        self.assertEqual(coverage_seconds([(0, 3), (2, 4), (5, 6)]), 5.0)
        self.assertGreater(coverage_seconds([(row["start"], row["end"]) for row in rows]), 8)

    def test_public_domain_without_a_url_is_kept_and_cc_by_without_a_url_is_not(self):
        public = {
            "license": "Public domain",
            "license_url": "",
            "download_url": "https://example.invalid/ppe.webm",
            "mime": "video/webm",
            "size": 36_000_000,
            "title": "Demonstration of Donning Personal Protective Equipment",
            "description": "personal protective equipment",
        }
        _fill_public_domain_url(public)
        self.assertIsNone(accept_video_candidate(public, "personal protective equipment", "wearing a PPE kit"))
        self.assertTrue(public["license_url"].startswith("https://creativecommons.org/publicdomain/"))
        cc = dict(public, license="CC BY 4.0", license_url="")
        self.assertIn("license url", accept_video_candidate(cc, "personal protective equipment", "PPE"))

    def test_elitebook_is_not_a_textbook(self):
        laptop = {
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "download_url": "https://example.invalid/laptop.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "How to open HP EliteBook 830",
            "description": "laptop disassembly",
        }
        self.assertIsNotNone(accept_video_candidate(laptop, "textbook", "koi kitaab nahi"))
        lecture = dict(
            laptop,
            title="Cultural travel, quote from textbook author.webm",
            description="a talk quoting a textbook author",
        )
        self.assertIn("different subject", accept_video_candidate(lecture, "textbook", "koi kitaab nahi recommend"))
        swimming = dict(laptop, title="Mary's textbook-style freestyle underwater view", description="swim lesson")
        self.assertIsNotNone(accept_video_candidate(swimming, "textbook", "ye book padhni hai"))
        india = dict(laptop, license="GODL-India", title="Open textbook pages", description="textbook")
        self.assertIn("not on the allow list", accept_video_candidate(india, "textbook", "kitaab"))
        nd = dict(laptop, license="CC BY-ND 4.0")
        self.assertIn("does not allow", accept_video_candidate(nd, "textbook", "kitaab"))

    def test_book_window_stays_inside_the_book_sentence(self):
        transcript = {
            "language": "en",
            "segments": [
                {
                    "id": "b0",
                    "text": "They did not recommend a book",
                    "original_text": "They did not recommend a book",
                    "start": 41.98,
                    "end": 46.72,
                    "words": [_word("book", 44.84, 45.10)],
                },
                {
                    "id": "b1",
                    "text": "you will make mistakes",
                    "original_text": "you will make mistakes",
                    "start": 46.76,
                    "end": 50.80,
                    "words": [_word("mistakes", 48.10, 48.50)],
                },
            ],
        }
        rows = [row for row in broll_opportunities(transcript) if row["action"] == "search"]
        self.assertEqual(rows[0]["start"], 41.98)
        self.assertEqual(rows[0]["end"], 46.72)

    def test_visible_caption_is_uppercase_and_the_plan_text_is_not(self):
        decision = {"action": "caption", "id": "c1", "start": 0.0, "end": 1.2, "text": "na hi maine newspaper"}
        html = _captions({"decisions": [decision]}, "bounded_card_with_title", {}, 1280)
        self.assertIn(">NA HI MAINE NEWSPAPER<", html)
        self.assertEqual(decision["text"], "na hi maine newspaper")


if __name__ == "__main__":
    unittest.main()
