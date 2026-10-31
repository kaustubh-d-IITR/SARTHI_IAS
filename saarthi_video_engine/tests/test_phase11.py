"""Phase 11: subject rules, one clip per lesson, a single-line title, no frame stroke."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.subjects import normalize_alias, opportunity_records, subjects_in_text  # noqa: E402
from analysis.uniqueness import asset_identity, enforce_unique, query_phrase_missing  # noqa: E402
from assets.concepts import query_variants  # noqa: E402
from render.frame import fit_title, layout_issues, reference_frame  # noqa: E402
from render.outro import outro_filter  # noqa: E402


class SubjectTests(unittest.TestCase):
    def test_proper_nouns_and_relationships_are_classified(self):
        found = {item["normalized"]: item for item in subjects_in_text("UPSC ne NCERT recommend nahi ki")}
        self.assertEqual(found["UPSC"]["category"], "examination")
        self.assertTrue(found["UPSC"]["proper_noun"])
        self.assertEqual(found["UPSC"]["action"], "search")
        self.assertEqual(found["UPSC"]["schedule"], "log_only")
        self.assertFalse(found["UPSC"]["identity_claim"])
        self.assertEqual(found["NCERT"]["category"], "study_material")
        self.assertEqual(found["NCERT"]["query"], "textbook")

    def test_hinglish_aliases_and_a_personal_relative_stay_off_stock_footage(self):
        self.assertEqual(normalize_alias("bahan"), "behen")
        self.assertEqual(normalize_alias("kitab"), "kitaab")
        found = {item["normalized"]: item for item in subjects_in_text("mera koi bhai behen nahi")}
        self.assertEqual(found["brother"]["category"], "relationship")
        self.assertEqual(found["sister"]["action"], "search")
        self.assertEqual(found["sister"]["schedule"], "log_only")
        self.assertIn("relative", found["brother"]["reason"])

    def test_a_book_and_a_hospital_are_searchable_objects(self):
        found = {item["normalized"]: item for item in subjects_in_text("hospital mein kitaab padhi")}
        self.assertEqual(found["book"]["category"], "book")
        self.assertEqual(found["hospital"]["category"], "place")
        self.assertEqual(found["book"]["action"], "search")
        self.assertGreater(len(query_variants("newspaper")), 1)

    def test_each_sentence_is_one_record(self):
        records = opportunity_records(
            {"segments": [{"text": "I don't have a sibling", "start": 0.4, "end": 3.7}]}
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["teacher_preferred"])
        self.assertEqual(records[0]["subjects"][0]["normalized"], "sibling")


class UniqueClipTests(unittest.TestCase):
    def test_scattered_words_are_not_the_phrase(self):
        news = "Business Day newspaper reports calls for him to stand down"
        self.assertTrue(query_phrase_missing("newspaper stand", news))
        self.assertFalse(query_phrase_missing("newspaper", "he reads a newspaper"))

    def test_the_same_page_cannot_fill_a_second_mention(self):
        first = {"page_url": "https://example.invalid/paper", "source_window": [0.5, 3.7], "kind": "video"}
        second = {"page_url": "https://example.invalid/paper", "source_window": [16.3, 18.9], "kind": "video", "local_path": "other-name.mp4"}
        kept, rejected = enforce_unique([first, second])
        self.assertEqual(rejected, 1)
        self.assertIsNone(kept[0].get("rejected"))
        self.assertIn("already used", kept[1]["rejected"])
        self.assertEqual(asset_identity(first), asset_identity(second))

    def test_another_lesson_may_reuse_the_same_source(self):
        from analysis.uniqueness import enforce_unique as check

        page = "https://example.invalid/shelves"
        rows = [
            {"video_id": "VIDEO_1", "page_url": page, "source_window": [1, 4], "kind": "video"},
            {"video_id": "VIDEO_2", "page_url": page, "source_window": [0, 3], "kind": "video"},
        ]
        kept, rejected = check(rows)
        self.assertEqual(rejected, 0)
        self.assertFalse(any(row.get("rejected") for row in kept))

    def test_a_different_asset_is_kept_for_the_later_mention(self):
        rows = [
            {"page_url": "https://example.invalid/press", "source_window": [0.5, 3.7], "kind": "video"},
            {"page_url": "https://example.invalid/reader", "source_window": [16.3, 18.9], "kind": "video"},
        ]
        kept, rejected = enforce_unique(rows)
        self.assertEqual(rejected, 0)
        self.assertTrue(all(not row.get("rejected") for row in kept))

    def test_identical_bytes_are_one_asset_even_with_two_urls(self):
        payload = b"same-clip-bytes"
        folder = ROOT / "outputs" / "dr_shivin_phase11" / "hash_fixture"
        folder.mkdir(parents=True, exist_ok=True)
        first_path = folder / "a.mp4"
        second_path = folder / "b.mp4"
        first_path.write_bytes(payload)
        second_path.write_bytes(payload)
        rows = [
            {"page_url": "https://example.invalid/a", "local_path": str(first_path), "source_window": [0, 2], "kind": "video"},
            {"page_url": "https://example.invalid/b", "local_path": str(second_path), "source_window": [4, 6], "kind": "video"},
        ]
        kept, rejected = enforce_unique(rows)
        self.assertEqual(rejected, 1)
        self.assertIn("already used", kept[1]["rejected"])


class TitleAndFrameTests(unittest.TestCase):
    def test_from_neet_to_upsc_is_one_centered_line(self):
        frame = reference_frame()
        fitted = fit_title("From [[NEET]]\nto [[UPSC]]", frame)
        self.assertEqual(fitted["lines"], 1)
        self.assertNotIn("\n", fitted["text"])
        self.assertIn("[[NEET]]", fitted["text"])
        self.assertIn("[[UPSC]]", fitted["text"])
        self.assertGreaterEqual(fitted["font_size"], 30)
        self.assertEqual(fitted["align"], "center")
        self.assertLessEqual(fitted["width"], 720 - frame["title_left"] - frame["title_right"])
        self.assertEqual(layout_issues(frame), [])

    def test_the_lesson_card_has_no_stroke_and_the_outro_is_plain(self):
        frame = reference_frame()
        self.assertEqual(frame["border_px"], 0)
        self.assertNotIn("drawbox", outro_filter(848, 478, 720, 1280))
        self.assertNotIn("#d01212", outro_filter(848, 478, 720, 1280))


if __name__ == "__main__":
    unittest.main()
