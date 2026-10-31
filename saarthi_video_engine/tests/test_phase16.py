"""Phase 16: sentence scene, action match, and the negation cross."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.subject_rules import records  # noqa: E402
from assets.footage import _scene_conflict  # noqa: E402
from assets.queries import description_block, retrieval_plan  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from hyperframes_adapter.compose import render_html  # noqa: E402
from treatment.reference_look import lesson_grade  # noqa: E402


class SceneRecordTests(unittest.TestCase):
    def test_a_denied_newspaper_asks_for_the_paper_and_a_cross(self):
        rows = records(
            {"segments": [{"text": "ना ही मैंने newspaper पढ़ा", "start": 0.12, "end": 3.84}]}
        )
        scene = rows[0]["scene"]
        self.assertEqual(rows[0]["text"], "ना ही मैंने newspaper पढ़ा")
        self.assertEqual(scene["action"], "reading")
        self.assertEqual(scene["framing"], "negative")
        self.assertIn("newspaper", scene["negated"])
        self.assertEqual(scene["overlay"], "negation_cross")

    def test_shelves_are_not_a_newspaper(self):
        plan = retrieval_plan("newspaper", "naa hi maine newspaper padha", "newspaper")
        self.assertIsNotNone(description_block("compact bookshelves moving in a library", plan))

    def test_a_document_on_a_computer_is_not_opening_youtube(self):
        plan = retrieval_plan("youtube on a phone", "opened youtube", "youtube")
        reason = _scene_conflict(
            "using two computer screens in a laptop for tamil wikisource",
            {"normalized": "YouTube"},
            plan,
        )
        self.assertIn("not opening YouTube", reason or "")

    def test_a_youtube_explainer_is_not_opening_the_app(self):
        plan = retrieval_plan("youtube on a phone", "opened youtube", "youtube")
        reason = _scene_conflict(
            "why can't you download videos on youtube how a law stops youtube-dl users",
            {"normalized": "YouTube"},
            plan,
        )
        self.assertIn("not opening YouTube", reason or "")
        plan = retrieval_plan("youtube on a phone", "opened youtube", "youtube")
        reason = _scene_conflict(
            "us govt issues a phone to each migrant. the phone has one app",
            {"normalized": "YouTube"},
            plan,
        )
        self.assertIsNotNone(reason)

    def test_ppe_training_is_not_a_hospital_visit(self):
        plan = retrieval_plan("hospital ward", "going to the hospital", "hospital")
        reason = _scene_conflict(
            "medics learn how to wear equipment in case they treat a patient with ebola",
            {"normalized": "hospital"},
            plan,
        )
        self.assertIn("hospital visit", reason or "")


class NegationCrossTests(unittest.TestCase):
    def test_the_cross_is_only_on_the_negated_insert(self):
        dataset = load_dataset()
        plan = build_phase2_plan(
            dataset,
            transcript={
                "language": "hi",
                "language_source": "configured",
                "method": "test",
                "model": "none",
                "provenance": "unit",
                "segments": [{"id": "s", "text": "book", "original_text": "book", "start": 0.0, "end": 4.0}],
            },
            selection={"source_in": 0, "source_out": 4, "duration": 4, "text": "book", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={"width": 1280, "height": 720, "fps": 30, "attribution_line": "test", "license": "not stated", "license_url": "not-stated-in-file"},
            assets=[],
            grade=lesson_grade(4),
        )
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        plan["render"]["corner_logo"] = "assets/logo.png"
        plain = render_html(plan, defaults)
        self.assertNotIn("negation-cross", plain)
        self.assertNotIn("#d01212", plain)
        plan["decisions"].append(
            {
                "id": "broll-9",
                "action": "replace_with_broll",
                "start": 0.2,
                "end": 2.0,
                "reason": "newspaper denial",
                "reference_ids": [],
                "reference_timestamps": [],
                "confidence": 0.5,
                "source": "observed",
                "asset_id": "paper",
                "asset_path": "assets/paper.mp4",
                "asset_kind": "video",
                "graphic": "negation_cross",
            }
        )
        marked = render_html(plan, defaults)
        self.assertIn("negation-cross", marked)
        self.assertIn("#d01212", marked)
        self.assertNotIn("border:", marked)


if __name__ == "__main__":
    unittest.main()
