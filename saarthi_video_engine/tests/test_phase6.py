"""Phase 6 frame geometry, title accents, and licensed cutaway rules."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assets.concepts import denies  # noqa: E402
from assets.video_commons import accept_video_candidate  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from hyperframes_adapter.compose import _title_html, render_html, render_title_scene  # noqa: E402
from render.frame import layout_issues, reference_frame  # noqa: E402
from render.outro import fitted_size, outro_filter  # noqa: E402


def _segment(index, start, end, text):
    return {
        "id": f"a{index}",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "language": "hi",
        "language_source": "configured",
        "timing_precision": "segment",
    }


class FrameTests(unittest.TestCase):
    def test_reference_frame_fits_the_canvas(self):
        frame = reference_frame(720, 1280)
        self.assertEqual(layout_issues(frame, 720, 1280), [])
        self.assertEqual(frame["logo_size"], round(720 * 0.112))
        self.assertEqual(frame["title_align"], "center")
        self.assertGreater(frame["title_left"], frame["logo_left"] + frame["logo_size"])
        self.assertGreater(frame["card_width"] * frame["card_height"], 0.50 * 720 * 1280)
        self.assertLess(frame["title_top"], frame["card_top"])
        self.assertEqual(frame["border_px"], 0)

    def test_oversized_logo_is_rejected(self):
        frame = reference_frame()
        frame["logo_size"] = 120
        self.assertIn("logo is oversized", layout_issues(frame))

    def test_title_accent_and_red_frame_are_in_the_html(self):
        self.assertIn('class="accent"', _title_html("From [[NEET]] to [[UPSC]]"))
        dataset = load_dataset()
        transcript = {
            "language": "hi",
            "language_source": "configured",
            "method": "test",
            "model": "none",
            "provenance": "unit",
            "segments": [_segment(0, 0, 8, "nahi menin newspaper pada")],
        }
        asset = {
            "id": "press",
            "path": "assets/press.mp4",
            "kind": "video",
            "match_terms": ["newspaper"],
            "reason": "Press footage with a negation mark",
            "page_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
            "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
            "duration_seconds": 4,
        }
        plan = build_phase2_plan(
            dataset,
            transcript=transcript,
            selection={"source_in": 0, "source_out": 8, "duration": 8, "text": "newspaper", "reason": "test"},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "attribution_line": "test",
                "license": "not stated",
                "license_url": "not-stated-in-file",
            },
            assets=[asset],
        )
        plan["render"]["corner_logo"] = "assets/logo.png"
        plan["style"]["layout_family"] = "full_bleed_board_or_lecture"
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertNotIn("#d01212", html)
        self.assertNotIn("border:", html)
        self.assertIn(f'width: {reference_frame()["logo_size"]}px', html)
        self.assertIn("text-align: center", render_title_scene(plan, defaults))
        self.assertNotIn("Local recording", html)
        self.assertNotIn("Yuri Megis", html)
        self.assertLess(html.find('id="card"'), html.find('class="clip broll"'))
        self.assertNotIn("negation-mark", html)
        self.assertIn("object-fit: cover", html)
        self.assertNotIn("corner-logo", outro_filter(848, 478, 720, 1280))
        fitted_size(848, 478, 720, 1280)

    def test_denial_and_stop_motion_are_not_treated_as_a_reading_clip(self):
        self.assertTrue(denies("nahi menin newspaper pada", "newspaper"))
        self.assertFalse(denies("the river is wide", "river"))
        row = {
            "license": "CC BY 3.0",
            "license_url": "https://creativecommons.org/licenses/by/3.0",
            "mime": "video/webm",
            "size": 800_000,
            "download_url": "https://example.invalid/a.webm",
            "title": "File:Stop Motion Newspaper Ad.webm",
            "description": "stop motion newspaper advertisement",
        }
        self.assertIsNotNone(accept_video_candidate(row, "newspaper", speech="nahi newspaper pada"))


if __name__ == "__main__":
    unittest.main()
