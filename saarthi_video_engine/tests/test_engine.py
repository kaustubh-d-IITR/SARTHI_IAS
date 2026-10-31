import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset_adapter.loader import load_dataset, partial_ids, trusted_records  # noqa: E402
from dataset_adapter.retrieve import get_record, retrieve, similar  # noqa: E402
from dataset_adapter.style_profile import observations, style_profile  # noqa: E402
from editing_plan.build import build_proof_plan  # noqa: E402
from hyperframes_adapter.compose import render_html  # noqa: E402
from hyperframes_adapter.duck import volume_lane  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

MEDIA = {
    "teacher": "assets/teacher.mp4",
    "broll": "assets/broll.mp4",
    "narration": "assets/narration.wav",
    "music": "assets/music_placeholder.wav",
    "logo": "assets/logo.svg",
}


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()

    def test_counts_match_the_written_dataset(self):
        self.assertEqual(len(self.dataset["shorts"]), 50)
        self.assertEqual(len(trusted_records(self.dataset)), 47)
        self.assertEqual(partial_ids(self.dataset), ["SHORT_30", "SHORT_32", "SHORT_37"])

    def test_partials_load_but_do_not_seed_rules(self):
        record = get_record(self.dataset, "SHORT_30")
        self.assertEqual(record["quality"]["status"], "partial")
        self.assertTrue(observations(record))
        with self.assertRaises(ValueError):
            style_profile(self.dataset, "SHORT_30")

    def test_profile_does_not_invent_music_or_fonts(self):
        profile = style_profile(self.dataset, "SHORT_03")
        self.assertEqual(profile["layout_family"], "bounded_card_with_title")
        self.assertIsNone(profile["music"]["identity"])
        self.assertEqual(profile["music"]["identity_status"], "unknown")
        self.assertIsNone(profile["typography"]["font_file"])
        self.assertFalse(profile["broll"]["source_identified"])
        self.assertTrue(set(profile["excluded_partial_ids"]) >= {"SHORT_30", "SHORT_32", "SHORT_37"})

    def test_retrieval_filters_family_and_skips_partials(self):
        hits = retrieve(
            self.dataset,
            category="study_advice",
            layout_group="bounded_card_with_title",
            query="advice interview seniors",
            trusted_only=True,
        )
        self.assertTrue(hits)
        self.assertTrue(all(hit["trusted_for_rules"] for hit in hits))
        self.assertNotIn("SHORT_37", [hit["reference_id"] for hit in hits])
        neighbors = similar(self.dataset, "SHORT_03")
        self.assertTrue(neighbors)
        self.assertTrue(all(hit["layout_group"] == "bounded_card_with_title" for hit in neighbors))


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()
        cls.plan = build_proof_plan(cls.dataset, "SHORT_03", MEDIA)

    def test_plan_validates(self):
        self.assertEqual(validate_plan(self.plan), [])

    def test_overlap_is_rejected(self):
        broken = json.loads(json.dumps(self.plan))
        broken["decisions"][0]["end"] = 3.0
        errors = validate_plan(broken)
        self.assertTrue(any("overlaps" in error for error in errors))

    def test_music_identity_cannot_be_filled_in(self):
        broken = json.loads(json.dumps(self.plan))
        broken["audio"]["music"]["identity"] = "invented song"
        errors = validate_plan(broken)
        self.assertTrue(errors)


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset = load_dataset()
        cls.plan = build_proof_plan(dataset, "SHORT_03", MEDIA)
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        cls.html = render_html(cls.plan, defaults)

    def test_card_family_keeps_one_narration_track(self):
        self.assertIn('data-layout-family="bounded_card_with_title"', self.html)
        self.assertIn('id="narration"', self.html)
        self.assertEqual(self.html.count("<audio"), 2)
        self.assertNotIn("data-has-audio", self.html)
        self.assertIn("muted", self.html)
        self.assertIn("THE PICTURE CHANGES HERE", self.html)
        self.assertIn("data-automation=", self.html)
        self.assertIn("assets/broll.mp4", self.html)
        self.assertIn('id="card"', self.html)

    def test_other_families_change_the_markup(self):
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        for family, marker in (
            ("full_bleed_board_or_lecture", 'data-layout-family="full_bleed_board_or_lecture"'),
            ("split_teacher_and_graphic", "pane-teacher"),
            ("screen_recording_or_product_ui", "IBM Plex Mono"),
        ):
            plan = json.loads(json.dumps(self.plan))
            plan["style"]["layout_family"] = family
            html = render_html(plan, defaults)
            self.assertIn(marker, html)
            self.assertIn('id="narration"', html)

    def test_ducking_drops_during_speech(self):
        lane = volume_lane(8.0, [(0.3, 7.6)], ducked=0.14, open_level=0.35)
        points = lane["lanes"][0]["points"]
        self.assertEqual(_gain_at(points, 0.0), 0.35)
        self.assertEqual(_gain_at(points, 1.0), 0.14)
        self.assertEqual(_gain_at(points, 7.2), 0.14)


def _gain_at(points, time):
    current = points[0]["v"]
    for point in points:
        if point["t"] <= time:
            current = point["v"]
    return current


if __name__ == "__main__":
    unittest.main()
