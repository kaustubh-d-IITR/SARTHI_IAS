"""Phrase-level B-roll placement on the real test lesson's word timings.

The lesson says, in order:
  0.12 s  "naa hi mainne newspaper padha"        (I did not read the newspaper)
  1.26 s  "aur naa hi mainne NCERT padhi"        (nor NCERT)
  6.08 s  "mainne bhi YouTube khola thaa"        (I also opened YouTube)
 16.14 s  "yaar newspaper padh lo aur NCERT ..."  (advice: read the newspaper and NCERT)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.processing.director import build_phrases, describe, direct, find_phrase
from app.processing.plan_adapt import assets_from_user, build_engine_plan, reconcile

FIXTURE = Path(__file__).parent / "fixtures" / "newspaper_transcript.json"
LESSON = 22.88


@pytest.fixture(scope="module")
def transcript() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _clip(asset_id: str, description: str, seconds: float = 5.0) -> dict:
    return {"asset_id": asset_id, "filename": f"{asset_id}.mp4", "description": description, "duration_seconds": seconds}


THREE = [
    _clip("newspaper", "A newspaper with a big red cross over it"),
    _clip("ncert", "NCERT books with a red cross"),
    _clip("youtube", "A phone opening YouTube"),
]


def _by_id(placements: list[dict]) -> dict:
    return {item["asset_id"]: item for item in placements}


def test_phrases_follow_clauses_not_fixed_segments(transcript):
    phrases = build_phrases(transcript)
    first = phrases[0]
    assert first.latin.split()[:5] == ["naa", "hi", "mainne", "newspaper", "padha"]
    assert first.start == pytest.approx(0.12) and first.negated
    second = phrases[1]
    assert "NCERT" in second.latin and second.negated and second.words[0].latin == "aur"
    advice = next(phrase for phrase in phrases if phrase.start > 15 and "newspaper" in phrase.tokens)
    assert not advice.negated


def test_description_reading():
    assert describe(THREE[0]) | {"keywords": None, "raw_tokens": None} == {"concepts": ["newspaper"], "actions": [], "negative": True, "keywords": None, "raw_tokens": None}
    reading = describe(THREE[2])
    assert reading["concepts"] == ["phone", "youtube"] and reading["actions"] == ["open"] and not reading["negative"]


def test_newspaper_ncert_and_youtube_land_on_their_own_phrases(transcript):
    placements, evaluations = direct(transcript, THREE, None, LESSON)
    placed = _by_id(placements)
    assert set(placed) == {"newspaper", "ncert", "youtube"}

    newspaper = placed["newspaper"]
    assert newspaper["speech_start"] == pytest.approx(0.12)
    assert newspaper["matched_phrase"].startswith("naa hi mainne newspaper padha")
    assert newspaper["speech_end"] == pytest.approx(placed["ncert"]["speech_start"])
    assert newspaper["placement_rule"] == "back_to_back"
    assert newspaper["occurrence"] == {"index": 1, "count": 2, "terms": ["newspaper"]}
    assert newspaper["scores"]["negation"] == 1.0

    ncert = placed["ncert"]
    assert ncert["speech_start"] == pytest.approx(1.26)
    assert "NCERT padhi" in ncert["matched_phrase"]
    assert ncert["speech_end"] - ncert["speech_start"] >= 2.0

    youtube = placed["youtube"]
    assert youtube["speech_start"] == pytest.approx(6.08)
    assert "YouTube khola" in youtube["matched_phrase"]
    assert youtube["scores"]["action"] == 1.0

    for item in placements:
        assert item["speech_start"] < 15.0, "nothing may land on the later 'newspaper padh lo' advice"
        assert item["source_out"] - item["source_in"] == pytest.approx(item["speech_end"] - item["speech_start"], abs=1e-3)
        assert item["source_out"] <= item["clip_duration"] + 1e-6
        assert item["explanation"] and item["approval"] == "approved" and item["approved_by"] == "director"
    starts = sorted((item["speech_start"], item["speech_end"]) for item in placements)
    for (_, left_end), (right_start, _) in zip(starts, starts[1:]):
        assert right_start >= left_end - 1e-6
    assert {row["status"] for row in evaluations} == {"selected"}


def test_crossed_out_clip_never_illustrates_advice_to_do_it(transcript):
    late = {"segments": [segment for segment in transcript["segments"] if segment["start"] > 15]}
    placements, evaluations = direct(late, [THREE[0]], None, LESSON)
    assert placements == []
    assert "does not say 'no' or 'not'" in evaluations[0]["reason"] or "crossed-out" in json.dumps(evaluations[0])


def test_first_mention_wins_when_mentions_are_equally_good(transcript):
    reading = _clip("reader", "Someone reading a newspaper")
    placements, _ = direct(transcript, [reading], None, LESSON)
    assert placements[0]["speech_start"] == pytest.approx(0.12)
    assert placements[0]["occurrence"]["index"] == 1


def test_a_later_mention_wins_only_when_clearly_stronger():
    words = [
        ("newspaper", 0.5, 0.9), ("ka", 1.0, 1.1), ("naam", 1.15, 1.4), ("suna.", 1.45, 1.8),
        ("phir", 4.0, 4.2), ("mainne", 4.25, 4.5), ("newspaper", 4.55, 5.0), ("padha", 5.05, 5.4), ("aur", 5.5, 5.6), ("khola.", 5.65, 6.0),
    ]
    transcript = {"segments": [{"id": "s0", "text": " ".join(w for w, _, _ in words), "start": 0.5, "end": 6.0, "words": [{"text": w, "start": a, "end": b} for w, a, b in words]}]}
    placements, _ = direct(transcript, [_clip("paper", "A person opening and reading a newspaper")], None, 10.0)
    assert placements[0]["speech_start"] == pytest.approx(4.25)
    assert placements[0]["occurrence"]["index"] == 2


def test_unrelated_clip_is_not_used_and_teacher_stays(transcript):
    placements, evaluations = direct(transcript, [_clip("cat", "A cat chasing a red ball in a garden")], None, LESSON)
    assert placements == []
    assert evaluations[0]["status"] == "abstained" and evaluations[0]["reason"]


def test_short_clip_and_cap_are_respected(transcript):
    placements, evaluations = direct(transcript, [_clip("tiny", "A phone opening YouTube", seconds=0.6)], None, LESSON)
    assert placements == [] and "shorter" in evaluations[0]["reason"]
    placements, _ = direct(transcript, THREE, None, 6.0)
    assert sum(item["speech_end"] - item["speech_start"] for item in placements) <= 6.0 * 0.58 + 1e-6


def test_user_named_phrase_is_found_with_hinglish_spellings(transcript):
    found = find_phrase(transcript, "na hi mene newspaper padha")
    assert found["start"] == pytest.approx(0.12) and "newspaper" in found["phrase"]
    ncert = find_phrase(transcript, "NCERT ka mention")
    assert ncert["start"] == pytest.approx(1.26)
    youtube = find_phrase(transcript, "sir ke YouTube open karne wale statement")
    assert youtube["start"] == pytest.approx(6.08)
    assert find_phrase(transcript, "the stock market crashed") is None


def test_engine_builder_keeps_back_to_back_phrase_cuts(transcript):
    placements, _ = direct(transcript, THREE, None, LESSON)
    plan = build_engine_plan(transcript, assets_from_user(placements), LESSON)
    kept, dropped = reconcile(placements, plan)
    assert dropped == [] and not any(item.get("trimmed") for item in kept)
    cutaways = [item for item in plan["decisions"] if item["action"] == "replace_with_broll"]
    assert [(item["asset_id"], item["start"]) for item in cutaways] == [("newspaper", 0.12), ("ncert", 1.26), ("youtube", 6.08)]
