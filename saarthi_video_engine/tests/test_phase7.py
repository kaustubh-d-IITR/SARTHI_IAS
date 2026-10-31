"""ElevenLabs transcription, lesson-analysis validation, and Commons retries."""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analysis.lesson import (  # noqa: E402
    acquire_broll,
    build_lesson_analysis,
    mark_approved,
    mark_downloaded,
    mark_rendered_from_plan,
    quality_warnings,
    record_candidate,
    validate_analysis,
)
from assets.video_commons import accept_video_candidate, download_video  # noqa: E402
from config.env import ConfigError, require_elevenlabs_key  # noqa: E402
from transcription.elevenlabs import MODEL_ID, transcribe_file  # noqa: E402


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _word(text, start, end, logprob=-0.1):
    return {"text": text, "start": start, "end": end, "type": "word", "logprob": logprob}


class TranscriptionTests(unittest.TestCase):
    def test_missing_key_names_the_env_file(self):
        import os

        import config.env as env_module

        previous = os.environ.pop("ELEVENLABS_API_KEY", None)
        original = env_module.load_project_env
        env_module.load_project_env = lambda path=None: None
        try:
            with self.assertRaises(ConfigError) as caught:
                env_module.require_elevenlabs_key()
        finally:
            env_module.load_project_env = original
            if previous is not None:
                os.environ["ELEVENLABS_API_KEY"] = previous
        message = str(caught.exception)
        self.assertIn(".env", message)
        self.assertNotIn("sk_", message)

    def test_word_times_and_mixed_language_are_preserved(self):
        seen = {}

        def opener(request, timeout):
            seen["header"] = request.get_header("Xi-api-key")
            seen["body"] = request.data
            return _Body(
                {
                    "language_code": "hin",
                    "language_probability": 0.91,
                    "text": "nahi newspaper pada",
                    "words": [
                        _word("nahi", 0.0, 0.4),
                        _word("newspaper", 0.4, 1.1, logprob=-1.4),
                        {"text": " ", "start": 1.1, "end": 1.12, "type": "spacing", "logprob": -0.01},
                        _word("pada", 1.12, 1.5),
                    ],
                }
            )

        path = ROOT / "tests" / "_tiny_audio.bin"
        path.write_bytes(b"audio-bytes")
        try:
            transcript = transcribe_file(path, api_key="test-key", opener=opener, cache_dir=None)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(seen["header"], "test-key")
        self.assertIn(b"scribe_v2", seen["body"])
        self.assertIn(b"timestamps_granularity", seen["body"])
        self.assertNotIn(b"test-key", seen["body"])
        segment = transcript["segments"][0]
        self.assertEqual(segment["text"], "nahi newspaper pada")
        self.assertEqual([word["start"] for word in segment["words"]], [0.0, 0.4, 1.12])
        self.assertIsNone(segment["confidence"])
        self.assertIn("low_logprob_or_missing_time", segment["review_flags"])
        self.assertEqual(transcript["model"], MODEL_ID)
        self.assertFalse(transcript["translated_to_english"])

    def test_auth_failure_does_not_echo_the_key(self):
        path = ROOT / "tests" / "_tiny_audio.bin"
        path.write_bytes(b"audio-bytes")
        secret = "unit-test-key-value"

        def opener(request, timeout):
            raise urllib.error.HTTPError(
                "https://api.elevenlabs.io/v1/speech-to-text",
                401,
                "Unauthorized",
                {},
                io.BytesIO(f"invalid {secret}".encode("utf-8")),
            )

        try:
            with self.assertRaises(Exception) as caught:
                transcribe_file(path, api_key=secret, opener=opener, cache_dir=None, attempts=1)
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

    def test_malformed_response_is_rejected(self):
        path = ROOT / "tests" / "_tiny_audio.bin"
        path.write_bytes(b"audio-bytes")

        def opener(request, timeout):
            return _Body({"text": "only text"})

        try:
            with self.assertRaises(Exception) as caught:
                transcribe_file(path, api_key="test-key", opener=opener, cache_dir=None)
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("without text and words", str(caught.exception))

    def test_cache_skips_a_second_request(self):
        calls = {"n": 0}
        path = ROOT / "tests" / "_tiny_audio.bin"
        path.write_bytes(b"same-bytes")
        cache = ROOT / "tests" / "_cache"

        def opener(request, timeout):
            calls["n"] += 1
            return _Body(
                {
                    "language_code": "eng",
                    "language_probability": 0.5,
                    "text": "hello",
                    "words": [_word("hello", 0.0, 0.5)],
                }
            )

        try:
            transcribe_file(path, api_key="test-key", opener=opener, cache_dir=cache)
            transcribe_file(path, api_key="test-key", opener=opener, cache_dir=cache)
        finally:
            path.unlink(missing_ok=True)
            if cache.exists():
                for child in cache.iterdir():
                    child.unlink()
                cache.rmdir()
        self.assertEqual(calls["n"], 1)

    def test_rate_limit_is_retried_and_then_succeeds(self):
        calls = {"n": 0}
        waits = []
        payload = b"x" * 60_000

        def opener(request, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError("https://example.invalid/a", 429, "Too many requests", {}, io.BytesIO(b""))
            return _Body(payload)

        destination = ROOT / "tests" / "_clip.mp4"
        try:
            download_video(
                "https://example.invalid/a.mp4",
                destination,
                opener=opener,
                sleep=lambda delay: waits.append(delay),
            )
        finally:
            destination.unlink(missing_ok=True)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(waits, [1.0])


class AnalysisTests(unittest.TestCase):
    def _transcript(self):
        return {
            "language": "hin",
            "language_source": "provider",
            "method": "elevenlabs",
            "model": MODEL_ID,
            "endpoint": "https://api.elevenlabs.io/v1/speech-to-text",
            "language_probability": 0.9,
            "confidence_note": "logprob only",
            "segments": [
                {
                    "id": "a0",
                    "text": "nahi menin newspaper pada",
                    "original_text": "nahi menin newspaper pada",
                    "start": 0.0,
                    "end": 2.3,
                    "words": [{"text": "newspaper", "start": 0.4, "end": 1.1, "logprob": -0.2}],
                    "review_flags": [],
                }
            ],
        }

    def test_schema_and_negation_slot(self):
        analysis = build_lesson_analysis(
            video_id="VIDEO_2",
            source={"path": "VIDEO_2.mp4", "duration_seconds": 41.5, "width": 1080, "height": 1920},
            transcript=self._transcript(),
        )
        self.assertEqual(validate_analysis(analysis), [])
        slot = analysis["timeline"][0]["broll"]
        self.assertTrue(slot["slot_planned"])
        self.assertTrue(slot["negation"])
        self.assertFalse(slot["rendered"])
        self.assertEqual(slot["proposed_start"], 0.0)
        self.assertEqual(slot["proposed_end"], 2.3)

    def test_approved_asset_that_is_not_in_the_plan_is_reported(self):
        analysis = build_lesson_analysis(
            video_id="VIDEO_2",
            source={"path": "VIDEO_2.mp4", "duration_seconds": 10.0, "width": 1080, "height": 1920},
            transcript=self._transcript(),
        )
        slot = analysis["timeline"][0]["broll"]
        record_candidate(slot, {"title": "press", "local_path": "assets/press.mp4", "status": "found"})
        mark_downloaded(slot, "assets/press.mp4")
        mark_approved(slot, "assets/press.mp4")
        plan = {"decisions": [{"action": "show_teacher", "start": 0, "end": 10, "asset_path": "assets/teacher.mp4"}]}
        mark_rendered_from_plan(analysis, plan)
        warnings = quality_warnings(analysis)
        self.assertTrue(any("none of it was rendered" in warning for warning in warnings))
        self.assertFalse(slot["rendered"])
        self.assertLess(analysis["coverage"]["rendered_fraction"], 0.01)

    def test_rendered_fraction_uses_the_main_lesson_only(self):
        analysis = build_lesson_analysis(
            video_id="VIDEO_2",
            source={"path": "VIDEO_2.mp4", "duration_seconds": 10.0, "width": 1080, "height": 1920},
            transcript=self._transcript(),
        )
        slot = analysis["timeline"][0]["broll"]
        record_candidate(slot, {"title": "press", "local_path": "assets/press.mp4"})
        mark_downloaded(slot, "assets/press.mp4")
        mark_approved(slot, "assets/press.mp4")
        plan = {
            "decisions": [
                {
                    "action": "replace_with_broll",
                    "start": 0.0,
                    "end": 2.3,
                    "asset_path": "assets/press.mp4",
                }
            ]
        }
        mark_rendered_from_plan(analysis, plan)
        self.assertEqual(analysis["coverage"]["rendered_seconds"], 2.3)
        self.assertEqual(analysis["coverage"]["rendered_fraction"], 0.23)
        self.assertEqual(quality_warnings(analysis), [])

    def test_stop_motion_is_rejected_and_a_relevant_file_is_kept(self):
        analysis = build_lesson_analysis(
            video_id="VIDEO_2",
            source={"path": "VIDEO_2.mp4", "duration_seconds": 10.0, "width": 1080, "height": 1920},
            transcript=self._transcript(),
        )
        saved = ROOT / "tests" / "_acquired"

        def search(query):
            return [
                {
                    "title": "File:Stop motion origami newspaper.webm",
                    "page_url": "https://commons.wikimedia.org/wiki/File:Stop_motion_origami_newspaper.webm",
                    "download_url": "https://example.invalid/origami.webm",
                    "mime": "video/webm",
                    "size": 80_000,
                    "license": "CC BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0",
                    "description": "stop motion origami newspaper",
                },
                {
                    "title": "File:Newspaper printing press.webm",
                    "page_url": "https://commons.wikimedia.org/wiki/File:Newspaper_printing_press.webm",
                    "download_url": "https://example.invalid/press.webm",
                    "mime": "video/webm",
                    "size": 80_000,
                    "license": "CC BY-SA 4.0",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
                    "description": "newspaper printing press",
                },
            ]

        def download(url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"v" * 60_000)
            return destination

        try:
            acquire_broll(analysis, saved, search, download, accept_video_candidate)
        finally:
            if saved.exists():
                for child in saved.iterdir():
                    child.unlink()
                saved.rmdir()
        slot = analysis["timeline"][0]["broll"]
        self.assertEqual(slot["candidates"][0]["status"], "rejected")
        self.assertIn("different subject", slot["candidates"][0]["rejected"])
        self.assertTrue(slot["downloaded"])
        self.assertTrue(slot["approved"])
        self.assertFalse(slot["rendered"])
        self.assertIsNone(slot["failure_reason"])

    def test_naa_hi_is_a_denial_and_reading_footage_is_rejected(self):
        from assets.concepts import denies

        speech = "naa hi mainne newspaper padha aur naa hi mainne NCERT padhi"
        self.assertTrue(denies(speech, "newspaper"))
        self.assertTrue(denies(speech, "ncert"))
        reading = {
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
            "mime": "video/webm",
            "size": 800_000,
            "download_url": "https://example.invalid/read.webm",
            "title": "File:Man reading a newspaper.webm",
            "description": "a man reading a newspaper at a table",
        }
        reason = accept_video_candidate(reading, "newspaper", speech=speech)
        self.assertIsNotNone(reason)
        self.assertIn("denied", reason)


if __name__ == "__main__":
    unittest.main()
