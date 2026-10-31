"""Phase 12: subject queries, unique clips, halftone, and visual ranking."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.subjects import opportunity_records, subjects_in_text  # noqa: E402
from analysis.uniqueness import enforce_unique, hamming  # noqa: E402
from assets.library import Catalogue  # noqa: E402
from assets.providers import openverse_status, pexels_status  # noqa: E402
from assets.queries import description_block, retrieval_plan  # noqa: E402
from assets.visual_rank import ClipRanker, average_hash, black_fraction, framing_decision  # noqa: E402
from hyperframes_adapter.compose import render_html, render_title_scene  # noqa: E402
from render.frame import fit_title, reference_frame  # noqa: E402
from render.halftone import halftone_background_css, halftone_svg, pattern_is_animated  # noqa: E402
from render.outro import outro_filter  # noqa: E402


class SubjectQueriesTest(unittest.TestCase):
    def test_a_denied_newspaper_is_not_a_person_reading(self):
        plan = retrieval_plan("newspaper", "na hi maine newspaper padha", "newspaper")
        self.assertTrue(plan["negation"])
        self.assertGreaterEqual(len(plan["phrases"]), 2)
        self.assertTrue(any("newspaper" in phrase for phrase in plan["phrases"]))
        self.assertFalse(any("reading" in phrase for phrase in plan["phrases"]))
        self.assertIn("printed newspapers", plan["prompt"])

    def test_an_instruction_to_read_asks_for_a_person(self):
        plan = retrieval_plan("newspaper", "yaar newspaper padh lo", "newspaper")
        self.assertFalse(plan["negation"])
        self.assertTrue(any("reading" in phrase for phrase in plan["phrases"]))
        self.assertIn("person reading", plan["prompt"])

    def test_hospital_and_books_are_scenes_not_single_keywords(self):
        hospital = retrieval_plan("hospital ward", "I am going to the hospital", "hospital")
        self.assertIn("patient", hospital["prompt"])
        self.assertEqual(
            description_block("a doctor briefing on hospital policy", hospital),
            "description is the wrong kind of footage",
        )
        self.assertGreaterEqual(len(hospital["phrases"]), 2)
        books = retrieval_plan("textbook", "kitaab recommend nahi kari", "kitaab")
        self.assertTrue(books["negation"])
        self.assertGreaterEqual(len(books["phrases"]), 2)

    def test_a_news_report_is_blocked_and_a_relative_is_not_a_search(self):
        plan = retrieval_plan("newspaper", "newspaper padh lo", "newspaper")
        blob = "Business Day newspaper. The president must stand down."
        self.assertEqual(description_block(blob, plan), "description is the wrong kind of footage")
        found = subjects_in_text("mera bhai nahi hai")
        self.assertTrue(found)
        self.assertTrue(all(item["action"] == "search" for item in found))
        self.assertTrue(all(item["schedule"] == "log_only" for item in found))

    def test_hinglish_alias_and_one_record_per_sentence(self):
        self.assertEqual(subjects_in_text("yeh kitaab padho")[0]["normalized"], "book")
        records = opportunity_records(
            {"segments": [{"text": "na hi maine newspaper padha", "start": 0.1, "end": 3.0}]}
        )
        self.assertEqual(len(records), 1)
        self.assertIn("newspaper", [item["normalized"] for item in records[0]["subjects"]])


class UniquenessAndLibraryTest(unittest.TestCase):
    def test_same_page_and_near_duplicate_frames_are_rejected(self):
        first = {"kind": "video", "page_url": "https://example.invalid/a", "source_window": [0, 3], "visual_hash": "ffffffffffffffff"}
        second = {"kind": "video", "page_url": "https://example.invalid/a", "source_window": [4, 7], "local_path": "other.mp4"}
        near = {"kind": "video", "page_url": "https://example.invalid/b", "source_window": [8, 11], "visual_hash": "fffffffffffffffe"}
        rows, rejected = enforce_unique([first, second, near])
        self.assertEqual(rejected, 2)
        self.assertTrue(rows[1]["rejected"])
        self.assertIn("already used", rows[1]["rejected"])
        self.assertIn("looks like", rows[2]["rejected"])
        self.assertLessEqual(hamming(first["visual_hash"], near["visual_hash"]), 8)

    def test_catalogue_reuses_a_file_by_hash_without_a_second_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(b"not-a-real-video-but-unique-bytes")
            catalogue = Catalogue(Path(folder) / "catalogue.sqlite")
            catalogue.upsert(
                {
                    "page_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
                    "local_path": str(path),
                    "sha256": "abc123",
                    "license": "CC BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0",
                    "artist": "Example",
                    "description": "a person reading a newspaper",
                    "tags": ["newspaper"],
                    "duration_seconds": 6,
                    "width": 1280,
                    "height": 720,
                }
            )
            again = catalogue.find_by_hash("abc123")
            self.assertEqual(again["local_path"], str(path))
            self.assertEqual(again["license"], "CC BY 4.0")
            self.assertTrue(catalogue.search_tag("newspaper"))
            catalogue.record_use(again["asset_id"], "phase12", "VIDEO_2", 1.0, 4.0)
            self.assertEqual(len(catalogue.uses(again["asset_id"])), 1)
            catalogue.close()


class FramingAndRankTest(unittest.TestCase):
    def test_a_person_on_black_is_rejected_and_a_full_frame_is_kept(self):
        isolated = np.zeros((180, 320, 3), dtype=np.uint8)
        isolated[70:110, 140:180] = 220
        self.assertGreater(black_fraction(isolated), 0.42)
        self.assertEqual(framing_decision(isolated), "reject")
        full = np.full((180, 320, 3), 140, dtype=np.uint8)
        self.assertEqual(framing_decision(full), "keep")
        self.assertEqual(len(average_hash(full)), 16)

    def test_a_fake_ranker_prefers_the_matching_picture(self):
        paper = Image.new("RGB", (32, 32), (240, 240, 240))
        machine = Image.new("RGB", (32, 32), (20, 20, 20))

        def scorer(image, prompt):
            bright = sum(image.resize((1, 1)).getpixel((0, 0))) > 300
            if "newspaper" in prompt:
                return 0.4 if bright else 0.1
            return 0.1

        ranker = ClipRanker(scorer=scorer)
        self.assertGreater(ranker.score(paper, "printed newspapers"), ranker.score(machine, "printed newspapers"))

    def test_clip_ranks_library_shelves_above_a_blank_field(self):
        shelves = ROOT / "outputs" / "dr_shivin_phase11" / "asset_cache" / "file-pressing-screen-to-move-compact-bookshelves-in-east-com.webm"
        if not shelves.is_file():
            self.skipTest("phase 11 library clip is not on disk")
        script = (
            "import os, sys, tempfile\n"
            "os.environ['CUDA_VISIBLE_DEVICES'] = ''\n"
            "from pathlib import Path\n"
            "from PIL import Image\n"
            "from assets.visual_rank import ClipRanker, sample_frames\n"
            f"frames = sample_frames(Path(r'{shelves}'), 2, Path(tempfile.mkdtemp()) / 'frames')\n"
            "ranker = ClipRanker()\n"
            "shelf = ranker.score(Image.open(frames[0]).convert('RGB'), 'bookshelves full of books in a library')\n"
            "blank = ranker.score(Image.new('RGB', (224, 224), (0, 0, 0)), 'bookshelves full of books in a library')\n"
            "print(round(shelf, 4), round(blank, 4))\n"
            "os._exit(0 if shelf > blank and ranker.loaded else 1)\n"
        )
        import os
        import subprocess

        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        env["CUDA_VISIBLE_DEVICES"] = ""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))


class BackgroundAndLayoutTest(unittest.TestCase):
    def test_halftone_is_static_and_has_no_red_stroke(self):
        svg = halftone_svg()
        css = halftone_background_css()
        self.assertFalse(pattern_is_animated())
        self.assertNotIn("#d01212", svg)
        self.assertNotIn("#d01212", css)
        self.assertNotIn("border:", css)
        self.assertIn("<circle ", svg)
        self.assertNotIn("animate", svg)
        self.assertIn("image/svg+xml", css)

    def test_title_stays_one_line_and_the_lesson_card_has_dots_not_a_border(self):
        fitted = fit_title("From [[NEET]] to [[UPSC]]", reference_frame())
        self.assertEqual(fitted["lines"], 1)
        self.assertNotIn("\n", fitted["text"])
        from editing_plan.phase2 import build_phase2_plan
        from dataset_adapter.loader import load_dataset

        dataset = load_dataset()
        transcript = {
            "language": "hi",
            "language_source": "configured",
            "method": "test",
            "model": "none",
            "provenance": "unit",
            "segments": [
                {
                    "id": "a0",
                    "text": "newspaper",
                    "original_text": "newspaper",
                    "start": 0,
                    "end": 4,
                    "confidence": 0.8,
                    "language": "hi",
                    "language_source": "configured",
                    "timing_precision": "segment",
                }
            ],
        }
        plan = build_phase2_plan(
            dataset,
            transcript=transcript,
            selection={"source_in": 0, "source_out": 4, "duration": 4, "text": "newspaper", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={"width": 1080, "height": 1920, "fps": 30, "attribution_line": "test", "license": "not stated", "license_url": "not-stated-in-file"},
            assets=[],
        )
        plan["render"]["corner_logo"] = "assets/logo.png"
        import json

        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertIn("image/svg+xml", html)
        self.assertNotIn("#d01212", html)
        self.assertNotIn("border:", html)
        self.assertIn("object-fit: cover", html)
        title = render_title_scene(plan, defaults)
        self.assertIn("text-align: center", title)
        self.assertNotIn("drawbox", outro_filter(848, 478, 720, 1280))
        self.assertNotIn("#d01212", outro_filter(848, 478, 720, 1280))

    def test_providers_do_not_invent_a_key(self):
        self.assertFalse(openverse_status()["available"])
        status = pexels_status()
        self.assertEqual(status["environment_variable"], "PEXELS_API_KEY")
        self.assertNotIn("sk-", json.dumps(status))
        self.assertTrue(status["reason"] or status["available"])


if __name__ == "__main__":
    unittest.main()
