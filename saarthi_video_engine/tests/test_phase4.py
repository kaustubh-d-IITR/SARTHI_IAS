"""Phase 4 unit tests. No network and no claim that a model watched a video."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assets.music_commons import accept_music_candidate  # noqa: E402
from assets.video_commons import accept_video_candidate  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from editing_plan.phase2 import build_phase2_plan, picture_fraction  # noqa: E402
from hyperframes_adapter.compose import render_html  # noqa: E402
from hyperframes_adapter.duck import volume_lane  # noqa: E402
from transcription.captions import caption_pieces, prepare_segment  # noqa: E402
from transcription.romanize import transliterate  # noqa: E402
from treatment.grade import infer_treatment  # noqa: E402
from validation.plan import validate_plan  # noqa: E402


def _segment(index, start, end, text, words=None):
    return {
        "id": f"a{index}",
        "text": text,
        "original_text": text,
        "start": start,
        "end": end,
        "confidence": 0.8,
        "timing_precision": "word" if words else "segment",
        "words": words or [],
    }


def _source():
    return {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "attribution_line": "Local recording",
        "license": "not stated in the file",
        "license_url": "not-stated-in-file",
    }


class RomanizeTests(unittest.TestCase):
    def test_devanagari_is_transliterated_not_translated(self):
        text = transliterate("मैं अस्पताल जा रहा हूँ")
        self.assertIn("aspataal", text)
        self.assertNotIn("hospital", text)
        self.assertNotIn("going", text.lower())

    def test_english_loanword_keeps_english_spelling(self):
        self.assertEqual(transliterate("कोविड"), "COVID")
        mixed = transliterate("मैं NEET की तैयारी कर रहा हूँ")
        self.assertIn("NEET", mixed)
        self.assertNotIn("exam", mixed.lower())

    def test_urdu_script_is_romanized_and_english_words_stay(self):
        self.assertIn("bhai", transliterate("بھائی"))
        mixed = transliterate("میں NEET ki")
        self.assertIn("main", mixed)
        self.assertIn("NEET", mixed)
        self.assertNotIn("exam", mixed.lower())

    def test_latin_text_is_unchanged(self):
        self.assertEqual(transliterate("forces on the pulley"), "forces on the pulley")


class CaptionTests(unittest.TestCase):
    def test_english_speech_uses_the_original_words(self):
        prepared = prepare_segment(_segment(0, 0, 2, "forces on the pulley"), "en")
        self.assertEqual(prepared["caption_text"], "forces on the pulley")
        self.assertEqual(prepared["english_text"], prepared["original_text"])

    def test_hindi_translation_is_not_the_caption(self):
        segment = _segment(0, 0, 4, "मैं अस्पताल जा रहा हूँ")
        segment["english_text"] = "I am going to the hospital."
        prepared = prepare_segment(segment, "hi")
        self.assertIn("aspataal", prepared["caption_text"])
        self.assertNotIn("going", prepared["caption_text"].lower())
        self.assertEqual(prepared["english_text"], "I am going to the hospital.")

    def test_letter_tokens_do_not_replace_the_romanized_line(self):
        words = [
            {"text": "ک", "start": index * 0.4, "end": index * 0.4 + 0.3, "probability": 0.5}
            for index in range(12)
        ]
        segment = _segment(0, 0, 8, "میں بھائی", words)
        prepared = prepare_segment(segment, "hi")
        pieces = caption_pieces(segment, prepared)
        self.assertEqual(len(pieces), 1)
        self.assertIn("bhai", pieces[0]["text"])
        self.assertNotIn("ک", pieces[0]["text"])

    def test_short_english_words_are_split_into_caption_lines(self):
        text = "a COVID duty, I am coming back, I am tired, I am exhausted. I have been wearing a"
        words = [
            {"text": token, "start": index * 0.2, "end": index * 0.2 + 0.15}
            for index, token in enumerate(text.split())
        ]
        segment = _segment(0, 0, 4, text, words)
        prepared = prepare_segment(segment, "en")
        pieces = caption_pieces(segment, prepared, 3)
        self.assertGreater(len(pieces), 4)
        self.assertTrue(all(len(piece["text"].split()) <= 3 for piece in pieces))

    def test_repeated_token_loop_is_not_captioned(self):
        segment = _segment(0, 0, 20, "cha " * 40)
        prepared = prepare_segment(segment, "hi")
        self.assertEqual(prepared["caption_text"], "")
        self.assertIn("repeated_recognition", prepared["flags"])
        self.assertTrue(prepared["original_text"])

    def test_english_clause_in_hindi_is_kept_and_not_rewritten(self):
        segment = _segment(0, 0, 4, "I am going to the hospital today")
        segment["english_text"] = "A different invented sentence."
        prepared = prepare_segment(segment, "hi")
        self.assertEqual(prepared["caption_text"], "I am going to the hospital today")
        self.assertIn("english_tokens_for_non_english_speech", prepared["flags"])
        self.assertNotEqual(prepared["caption_text"], prepared["english_text"])


class PlanBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset()

    def _plan(self, segments, assets=None, music=None, grade=None, language="hi"):
        return build_phase2_plan(
            self.dataset,
            transcript={
                "language": language,
                "language_source": "provider",
                "method": "fixture",
                "model": "ggml-tiny.bin",
                "provenance": "unit fixture",
                "segments": segments,
            },
            selection={"source_in": 0, "source_out": 40, "text": " ".join(item["text"] for item in segments), "duration": 40},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source=_source(),
            assets=assets or [],
            plan_id="phase4-fixture",
            primary_category="lesson",
            secondary_categories=["upsc"],
            music=music,
            grade=grade,
            exemplar_id="SHORT_07",
        )

    def test_visible_caption_is_romanized_hindi(self):
        segments = [
            _segment(0, 0, 8, "मैं अस्पताल जा रहा हूँ"),
            _segment(1, 8, 40, "तैयारी जारी है"),
        ]
        segments[0]["english_text"] = "I am going to the hospital."
        plan = self._plan(segments)
        captions = [item["text"] for item in plan["decisions"] if item["action"] == "caption"]
        self.assertTrue(any("aspataal" in text for text in captions))
        self.assertFalse(any("going to the hospital" in text.lower() for text in captions))
        self.assertEqual(validate_plan(plan), [])

    def test_broll_stays_within_the_cap(self):
        segments = [_segment(index, index * 5, index * 5 + 4.8, f"hospital ward note {index}") for index in range(8)]
        asset = {
            "id": "ward",
            "path": "assets/ward.mp4",
            "kind": "video",
            "match_terms": ["hospital"],
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "page_url": "https://commons.wikimedia.org/wiki/File:Example.webm",
            "artist": "Example",
            "reason": "The sentence names a hospital.",
            "duration_seconds": 30,
        }
        plan = self._plan(segments, assets=[asset], language="en")
        self.assertLessEqual(picture_fraction(plan, "replace_with_broll"), 0.58 + 1e-6)
        used = [item for item in plan["decisions"] if item["action"] == "replace_with_broll"]
        self.assertTrue(used)
        self.assertEqual(used[0]["license"], "CC BY 4.0")
        self.assertEqual(validate_plan(plan), [])

    def test_music_without_a_license_is_rejected(self):
        segments = [_segment(0, 0, 40, "forces on the pulley stay in balance here")]
        plan = self._plan(segments, language="en", music={
            "path": "assets/bed.wav",
            "identity": "invented song",
            "identity_status": "unknown",
            "source": "default",
            "gain_under_speech": 0.12,
            "gain_open": 0.28,
        })
        self.assertTrue(validate_plan(plan))

    def test_licensed_music_is_ducked_under_speech(self):
        segments = [_segment(0, 0, 40, "forces on the pulley stay in balance here")]
        music = {
            "path": "assets/bed.wav",
            "identity": "Example piano",
            "identity_status": "identified",
            "source": "licensed",
            "gain_under_speech": 0.12,
            "gain_open": 0.28,
            "license": "CC0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "page_url": "https://commons.wikimedia.org/wiki/File:Example.ogg",
            "artist": "Example",
        }
        plan = self._plan(segments, language="en", music=music)
        self.assertEqual(validate_plan(plan), [])
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertIn('id="narration"', html)
        self.assertIn('id="music-bed"', html)
        self.assertEqual(html.count("<audio"), 2)
        lane = volume_lane(40, [(0, 40)], ducked=0.12, open_level=0.28)
        points = lane["lanes"][0]["points"]
        self.assertTrue(points)
        self.assertTrue(all(point["v"] <= 0.12 for point in points))


class TreatmentTests(unittest.TestCase):
    def test_unclear_topic_is_not_graded(self):
        grade = infer_treatment("the class continues with another example")
        self.assertEqual(grade["name"], "none")
        self.assertFalse(grade["applied"])

    def test_hospital_speech_gets_a_restrained_grade(self):
        grade = infer_treatment("he returned from hospital covid duty")
        self.assertEqual(grade["name"], "serious")
        self.assertTrue(grade["ffmpeg_video_filter"])
        self.assertGreaterEqual(grade["confidence"], 0.55)


class AssetGateTests(unittest.TestCase):
    def test_video_license_and_subject_are_checked(self):
        ok = {
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "download_url": "https://example.invalid/ward.webm",
            "mime": "video/webm",
            "size": 2_000_000,
            "title": "Hospital ward",
            "description": "A hospital ward during treatment",
        }
        self.assertIsNone(accept_video_candidate(ok, "hospital", "hospital duty"))
        nc = dict(ok, license="CC BY-NC 4.0")
        self.assertIsNotNone(accept_video_candidate(nc, "hospital", "hospital duty"))
        cleaning = dict(ok, title="High pressure cleaning", description="cleaning equipment")
        self.assertIsNotNone(accept_video_candidate(cleaning, "ppe", "ppe kit"))

    def test_music_must_be_instrumental_and_licensed(self):
        ok = {
            "license": "CC0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "download_url": "https://example.invalid/piano.ogg",
            "mime": "audio/ogg",
            "size": 800_000,
            "title": "Ambient piano instrumental",
            "description": "solo piano",
        }
        self.assertIsNone(accept_music_candidate(ok))
        vocal = dict(ok, title="Song with lyrics", description="singing")
        self.assertIsNotNone(accept_music_candidate(vocal))
        missing = dict(ok)
        missing["license_url"] = ""
        self.assertIsNotNone(accept_music_candidate(missing))


if __name__ == "__main__":
    unittest.main()
