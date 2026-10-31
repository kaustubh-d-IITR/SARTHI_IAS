"""Phase 2 unit tests. No network, no API key, no media files."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assets.commons import accept_candidate  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from dataset_adapter.retrieve import retrieve  # noqa: E402
from dataset_adapter.style_profile import style_profile  # noqa: E402
from editing_plan.phase2 import build_phase2_plan  # noqa: E402
from hyperframes_adapter.compose import render_html  # noqa: E402
from editing_plan.segment import choose_segment, shift_segments  # noqa: E402
from editing_plan.style_choice import choose_exemplar  # noqa: E402
from transcription.align import attach_english  # noqa: E402
from transcription.whisper_cpp import WhisperCppError, transcript_from_payload, transcribe_wav  # noqa: E402
from validation.audio_compare import compare_wavs  # noqa: E402
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


class TranscriptTests(unittest.TestCase):
    def test_parser_keeps_token_times_and_does_not_invent_them(self):
        payload = {
            "result": {"language": "en"},
            "transcription": [
                {
                    "text": " forces on the pulley",
                    "offsets": {"from": 1000, "to": 2500},
                    "tokens": [
                        {"text": " forces", "offsets": {"from": 1000, "to": 1600}, "p": 0.9},
                        {"text": " pulley", "offsets": {"from": 1800, "to": 2500}, "p": 0.4},
                    ],
                }
            ],
        }
        parsed = transcript_from_payload(payload, requested_language="en", model_name="ggml-tiny.bin")
        self.assertEqual(parsed["language_source"], "configured")
        segment = parsed["segments"][0]
        self.assertEqual(segment["timing_precision"], "word")
        self.assertEqual([word["start"] for word in segment["words"]], [1.0, 1.8])
        self.assertEqual(segment["original_text"], "forces on the pulley")

    def test_segment_only_timing_stays_segment_precision(self):
        payload = {"result": {}, "transcription": [{"text": "hello there", "offsets": {"from": 0, "to": 1000}}]}
        parsed = transcript_from_payload(payload, requested_language="auto")
        self.assertEqual(parsed["language_source"], "inferred")
        self.assertEqual(parsed["segments"][0]["timing_precision"], "segment")
        self.assertEqual(parsed["segments"][0]["words"], [])

    def test_missing_binary_is_an_error(self):
        with self.assertRaises(WhisperCppError):
            transcribe_wav(Path("missing.wav"), binary=Path("no-such-whisper.exe"), model=Path("no-such-model.bin"))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset()

    def _plan(self, language="en", english=None, assets=None):
        segments = []
        for index in range(8):
            start = index * 8
            segments.append(_segment(index, start, start + 7.5, f"the pulley has forces and a rope number {index}"))
        transcript = {
            "language": language,
            "language_source": "configured",
            "method": "fixture",
            "model": None,
            "provenance": "unit fixture, not a real transcription",
            "segments": segments,
        }
        if english:
            transcript["segments"][0]["english_text"] = english
        selection = choose_segment(transcript["segments"])
        return build_phase2_plan(
            self.dataset,
            transcript=transcript,
            selection=selection,
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={
                "width": 400,
                "height": 300,
                "fps": 15,
                "attribution_line": "Example / CC BY 3.0",
                "license": "CC BY 3.0",
                "license_url": "https://creativecommons.org/licenses/by/3.0",
            },
            assets=assets or [],
        )

    def test_english_lecture_keeps_original_and_caption_equal(self):
        plan = self._plan()
        for segment in plan["input_media"]["transcript"]["segments"]:
            self.assertEqual(segment["original_text"], segment["english_text"])
        self.assertEqual(validate_plan(plan), [])

    def test_missing_translation_is_not_copied_into_english(self):
        plan = self._plan(language="hi")
        self.assertEqual(plan["input_media"]["transcript"]["segments"][0]["english_text"], "")
        self.assertTrue(plan["input_media"]["transcript"]["segments"][0]["original_text"])
        self.assertTrue(all(item["action"] != "caption" or item["text"] for item in plan["decisions"]))

    def test_narration_covers_the_plan(self):
        plan = self._plan()
        interval = plan["audio"]["narration_interval"]
        self.assertEqual(interval["start"], 0.0)
        self.assertEqual(interval["end"], plan["input_media"]["duration_seconds"])
        self.assertIsNone(plan["audio"]["music"]["path"])

    def test_html_keeps_one_continuous_narration_and_a_muted_picture(self):
        plan = self._plan()
        defaults = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
        html = render_html(plan, defaults)
        self.assertEqual(html.count("<audio"), 1)
        self.assertIn('id="narration"', html)
        self.assertIn("muted", html)
        self.assertNotIn("music-bed", html)

    def test_no_broll_keeps_one_teacher_interval(self):
        plan = self._plan(assets=[])
        pictures = [item for item in plan["decisions"] if item["action"] in {"show_teacher", "replace_with_broll"}]
        self.assertEqual(len(pictures), 1)
        self.assertEqual(pictures[0]["action"], "show_teacher")

    def test_matching_asset_replaces_only_the_matching_speech(self):
        asset = {
            "id": "pulley",
            "path": "assets/pulley.jpg",
            "kind": "image",
            "match_terms": ["pulley"],
            "page_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "license": "CC0",
        }
        plan = self._plan(assets=[asset])
        pictures = [item for item in plan["decisions"] if item["action"] in {"show_teacher", "replace_with_broll"}]
        self.assertTrue(any(item["action"] == "replace_with_broll" for item in pictures))
        used = next(item for item in pictures if item["action"] == "replace_with_broll")
        self.assertEqual(used["asset_path"], "assets/pulley.jpg")
        self.assertEqual(validate_plan(plan), [])

    def test_plan_id_and_category_are_not_fixed_to_the_lewin_example(self):
        plan = self._plan()
        plan_id = "dr-shivin-video-1"
        custom = build_phase2_plan(
            self.dataset,
            transcript={
                "language": "hi",
                "language_source": "provider",
                "method": "fixture",
                "model": "ggml-tiny.bin",
                "provenance": "unit fixture",
                "segments": [
                    _segment(0, 0, 10, "मूल वाक्य"),
                    _segment(1, 10, 40, "दूसरा वाक्य"),
                ],
            },
            selection={"source_in": 0, "source_out": 40, "text": "मूल वाक्य दूसरा वाक्य", "duration": 40},
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={
                "width": 1280,
                "height": 720,
                "fps": 30,
                "attribution_line": "Local recording",
                "license": "not stated in the file",
                "license_url": "not-stated-in-file",
            },
            assets=[],
            plan_id=plan_id,
            primary_category="upsc_lecture",
            secondary_categories=["exam_preparation"],
        )
        custom["input_media"]["transcript"]["segments"][0]["english_text"] = "You will make mistakes."
        self.assertEqual(custom["plan_id"], plan_id)
        self.assertEqual(custom["content_analysis"]["primary_category"], "upsc_lecture")
        self.assertNotEqual(plan["plan_id"], plan_id)
        self.assertEqual(custom["input_media"]["transcript"]["segments"][0]["original_text"], "मूल वाक्य")

    def test_repeated_caption_loop_is_not_copied_three_times(self):
        segments = []
        for index in range(6):
            segments.append(_segment(index, index * 8, index * 8 + 7.5, "same line about the exam"))
        transcript = {
            "language": "en",
            "language_source": "configured",
            "method": "fixture",
            "model": None,
            "provenance": "unit fixture",
            "segments": segments,
        }
        for segment in transcript["segments"]:
            segment["english_text"] = "same line about the exam"
        selection = choose_segment(transcript["segments"])
        plan = build_phase2_plan(
            self.dataset,
            transcript=transcript,
            selection=selection,
            media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
            source={
                "width": 400,
                "height": 300,
                "fps": 15,
                "attribution_line": "Example",
                "license": "CC BY 3.0",
                "license_url": "https://creativecommons.org/licenses/by/3.0",
            },
            assets=[],
        )
        captions = [item["text"] for item in plan["decisions"] if item["action"] == "caption"]
        self.assertLessEqual(captions.count("same line about the exam"), 2)

    def test_partial_records_do_not_seed_the_profile(self):
        with self.assertRaises(ValueError):
            style_profile(self.dataset, "SHORT_30")
        hits = retrieve(self.dataset, trusted_only=True, limit=50)
        ids = {hit["reference_id"] for hit in hits}
        self.assertTrue({"SHORT_30", "SHORT_32", "SHORT_37"}.isdisjoint(ids))

    def test_shift_drops_words_outside_the_window(self):
        segment = _segment(
            0,
            10,
            14,
            "alpha beta",
            [{"text": "alpha", "start": 10.0, "end": 11.0}, {"text": "beta", "start": 13.0, "end": 14.0}],
        )
        shifted = shift_segments([segment], 12, 20)
        self.assertEqual([word["text"] for word in shifted[0]["words"]], ["beta"])
        self.assertGreaterEqual(shifted[0]["words"][0]["start"], 0)


class TranslationTests(unittest.TestCase):
    def test_translation_is_aligned_and_not_copied_from_hindi(self):
        original = {
            "language": "hi",
            "language_source": "provider",
            "provenance": "detected",
            "segments": [_segment(0, 0, 4, "हिंदी वाक्य"), _segment(1, 20, 24, "दूसरा")],
        }
        translated = [_segment(0, 0.2, 3.5, "You will make mistakes.")]
        attached = attach_english(original, translated)
        self.assertEqual(attached["segments"][0]["original_text"], "हिंदी वाक्य")
        self.assertEqual(attached["segments"][0]["english_text"], "You will make mistakes.")
        self.assertEqual(attached["segments"][1]["english_text"], "")

    def test_exemplar_choice_excludes_partial_records(self):
        dataset = load_dataset()
        hit = choose_exemplar(dataset, "exam mistakes guidance", portrait=False)
        self.assertNotIn(hit["reference_id"], {"SHORT_30", "SHORT_32", "SHORT_37"})
        self.assertTrue(hit["trusted_for_rules"])


class AssetTests(unittest.TestCase):
    def test_rejects_nc_and_unrelated_pulley(self):
        speech = "the sum of all forces on the pulley must be zero"
        nc = {"license": "CC BY-NC-SA 4.0", "mime": "image/jpeg", "description": "a pulley diagram", "title": "x", "download_url": "https://example.invalid/a.jpg"}
        self.assertIsNotNone(accept_candidate(nc, "pulley", speech))
        windmill = {
            "license": "CC0",
            "mime": "image/jpeg",
            "description": "A windmill pulley in a village.",
            "title": "File:Windmill.jpg",
            "download_url": "https://example.invalid/b.jpg",
        }
        self.assertIn("different kind", accept_candidate(windmill, "pulley", speech))
        vague = {"license": "Public domain", "mime": "image/jpeg", "description": "A pulley.", "title": "File:Pulley.jpg", "download_url": "https://example.invalid/c.jpg"}
        self.assertIsNotNone(accept_candidate(vague, "pulley", speech))

    def test_accepts_a_described_physics_diagram(self):
        row = {
            "license": "Public domain",
            "mime": "image/png",
            "description": "Physics diagram of forces on a pulley.",
            "title": "File:Pulley.png",
            "download_url": "https://example.invalid/d.png",
        }
        self.assertIsNone(accept_candidate(row, "pulley", "forces on the pulley"))


class AudioTests(unittest.TestCase):
    def test_identical_wavs_correlate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            left = path / "a.wav"
            right = path / "b.wav"
            _tone(left)
            _tone(right)
            result = compare_wavs(left, right)
            self.assertGreater(result["correlation"], 0.99)
            self.assertIn("not silence", result["method"])

    def test_finds_an_encoder_sized_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            rate = 8000
            shift = 168
            frames = [int(12000 * math.sin(0.0015 * index * index)) for index in range(rate * 2)]
            delayed = [0] * shift + frames
            _write_pcm(path / "ref.wav", rate, frames)
            _write_pcm(path / "late.wav", rate, delayed)
            result = compare_wavs(path / "ref.wav", path / "late.wav")
            self.assertGreater(result["correlation"], 0.95)
            self.assertAlmostEqual(result["lag_seconds"], shift / rate, delta=0.004)


def _write_pcm(path: Path, rate: int, frames: list[int]) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True) for value in frames))


def _tone(path: Path) -> None:
    rate = 8000
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = [int(12000 * math.sin(index / 7.0)) for index in range(8000)]
        handle.writeframes(b"".join(value.to_bytes(2, "little", signed=True) for value in frames))


if __name__ == "__main__":
    unittest.main()
