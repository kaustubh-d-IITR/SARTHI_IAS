"""The editing assistant on the real test lesson: instructions in English and Hinglish become checked operations."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from test_phase3 import media  # noqa: F401  (fixture)
from test_phase4 import FakeWorker, _app, _session, _sha, clips  # noqa: F401  (fixtures)

from app.processing.assistant import parse, to_edit
from app.processing.director import direct
from app.processing.settings import default_settings, merge

FIXTURE = Path(__file__).parent / "fixtures" / "newspaper_transcript.json"
CLIPS = [
    {"asset_id": "newspaper", "filename": "newspaper.mp4", "description": "A newspaper with a big red cross over it", "duration_seconds": 5.0},
    {"asset_id": "ncert", "filename": "ncert.mp4", "description": "NCERT books with a red cross", "duration_seconds": 5.0},
    {"asset_id": "youtube", "filename": "phone.mp4", "description": "A phone opening YouTube", "duration_seconds": 5.0},
]


@pytest.fixture(scope="module")
def transcript() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plan(transcript) -> dict:
    placements, _ = direct(transcript, CLIPS, None, 22.88)
    return {"placements": placements, "rejected_placements": [], "catalogue": CLIPS}


def _one(result: dict) -> dict:
    assert result["status"] == "proposal", result
    assert len(result["operations"]) == 1, result["operations"]
    return result["operations"][0]


@pytest.mark.parametrize(
    "instruction, asset, start",
    [
        ("Newspaper wala B-roll bilkul us waqt lagao jab sir bolte hain na hi mene newspaper padha.", "newspaper", 0.12),
        ("NCERT books wala clip uske baad lagao jab sir NCERT ka mention karte hain.", "ncert", 1.26),
        ("YouTube wala B-roll sir ke YouTube open karne wale statement par insert karo.", "youtube", 6.08),
    ],
)
def test_hinglish_placement_instructions(transcript, plan, instruction, asset, start):
    op = _one(parse(instruction, plan, transcript))
    assert op["operation"] == "move_broll" and op["asset_id"] == asset
    assert op["target_start"] == pytest.approx(start)


def test_acceptance_sentence_in_english(transcript, plan):
    result = parse("Move the newspaper B-roll to the sentence where sir says na hi mene newspaper padha and keep it for 2 seconds.", plan, transcript)
    assert result["status"] == "proposal"
    op, room = result["operations"]
    assert op == op | {"operation": "move_broll", "asset_id": "newspaper", "duration_seconds": 2.0}
    assert op["target_start"] == pytest.approx(0.12) and "newspaper padha" in op["matched_phrase"]
    assert room == room | {"operation": "shift_broll", "asset_id": "ncert", "seconds": pytest.approx(0.86)}
    assert "0.12 s" in result["understood"] and "2 s" in result["understood"] and "so the two do not overlap" in result["understood"]
    body = to_edit(result["operations"], plan, default_settings(), transcript)
    rows = {row["asset_id"]: row for row in body["placements"]}
    assert rows["ncert"]["speech_start"] == pytest.approx(2.12)


@pytest.mark.parametrize(
    "instruction, expected",
    [
        ("Phone wala B-roll hata do.", {"operation": "remove_broll", "asset_id": "youtube"}),
        ("Background music thoda subtle kar do.", {"operation": "adjust_music_volume", "factor": 0.75}),
        ("Captions ka size thoda bada kar do.", {"operation": "adjust_caption_style", "change": {"size_delta": 4}}),
        ("Glitter effect sirf intro mein lagao.", {"operation": "enable_effect", "effect": "glitter", "scope": "intro"}),
        ("Teacher ko zyada screen space do.", {"operation": "adjust_video_framing", "teacher_height_delta": 0.12}),
    ],
)
def test_other_instructions(transcript, plan, instruction, expected):
    op = _one(parse(instruction, plan, transcript))
    assert op == op | expected


def test_duration_change_makes_room_visibly(transcript, plan):
    result = parse("Newspaper clip ko 2 seconds tak rakho.", plan, transcript)
    duration, room = result["operations"]
    assert duration == duration | {"operation": "change_broll_duration", "asset_id": "newspaper", "duration_seconds": 2.0}
    assert room["operation"] == "shift_broll" and room["asset_id"] == "ncert"


def test_unclear_instructions_get_a_question(transcript, plan):
    vague = parse("Is B-roll ko 5 seconds aage shift kar do.", plan, transcript)
    assert vague["status"] == "clarify" and "Which clip" in vague["question"] and vague["operations"] == []
    nonsense = parse("make it pop", plan, transcript)
    assert nonsense["status"] == "clarify"
    missing = parse("Newspaper wala B-roll lagao jab sir bolte hain stock market crash hua", plan, transcript)
    assert missing["status"] == "clarify" and "could not find" in missing["question"]


def test_shift_with_a_named_clip(transcript, plan):
    op = _one(parse("YouTube wala B-roll 2 seconds aage shift kar do", plan, transcript))
    assert op == op | {"operation": "shift_broll", "asset_id": "youtube", "seconds": 2.0}


def test_operations_become_an_ordinary_validated_edit(transcript, plan):
    ops = [
        parse("Move the newspaper B-roll to the sentence where sir says na hi mene newspaper padha and keep it for 2 seconds.", plan, transcript)["operations"][0],
        _one(parse("Background music thoda subtle kar do.", plan, transcript)),
        _one(parse("Glitter effect sirf intro mein lagao.", plan, transcript)),
        _one(parse("Phone wala B-roll hata do.", plan, transcript)),
    ]
    body = to_edit(ops, plan, default_settings(), transcript)
    rows = {row["asset_id"]: row for row in body["placements"]}
    assert rows["newspaper"]["target_phrase"].startswith("na hi mene newspaper padha") and rows["newspaper"]["duration_seconds"] == 2.0
    assert rows["youtube"]["approval"] == "rejected"
    settings = merge(default_settings(), body["settings"])
    assert settings["music"]["volume"] == 0.75
    assert settings["effects"]["glitter"] == {"enabled": True, "intensity": 0.4, "scope": "intro"}
    with pytest.raises(Exception):
        to_edit([{"operation": "rm -rf", "asset_id": "newspaper"}], plan, None, transcript)
    with pytest.raises(Exception):
        to_edit([{"operation": "remove_broll", "asset_id": "not-in-session"}], plan, None, transcript)


def test_propose_apply_and_undo_through_the_api(tmp_path, media, clips):
    worker = FakeWorker({_sha(clips["ppe"]): "ppe", _sha(clips["pen"]): "pen"})
    client, _app_, _bridge = _app(tmp_path, media, worker)
    session_id, owner, ids = _session(client, clips)
    headers = {"X-Studio-Client": owner}
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    assert plan["settings"]["music"]["volume"] == 1.0 and plan["placements"]

    proposal = client.post(f"/api/sessions/{session_id}/assistant/propose", headers=headers, json={"message": "Background music thoda subtle kar do aur glitter effect sirf intro mein lagao"}).json()
    assert proposal["status"] == "proposal" and {op["operation"] for op in proposal["operations"]} == {"adjust_music_volume", "enable_effect"}
    applied = client.post(f"/api/sessions/{session_id}/assistant/apply", headers=headers, json={"base_version_id": proposal["base_version_id"], "operations": proposal["operations"]})
    assert applied.status_code == 200, applied.text
    v2 = applied.json()
    assert v2["plan_version"]["origin"] == "assistant" and v2["plan_version"]["parent_version_id"] == plan["plan_version"]["version_id"]
    assert v2["settings"]["music"]["volume"] == 0.75 and v2["settings"]["effects"]["glitter"]["enabled"]
    assert v2["assistant_operations"] == proposal["operations"]

    removal = client.post(f"/api/sessions/{session_id}/assistant/propose", headers=headers, json={"message": "PPE wala B-roll hata do"}).json()
    v3 = client.post(f"/api/sessions/{session_id}/assistant/apply", headers=headers, json={"base_version_id": removal["base_version_id"], "operations": removal["operations"]}).json()
    assert v3["placements"] == [] and v3["rejected_placements"][0]["asset_id"] == ids["ppe"]

    stale = client.post(f"/api/sessions/{session_id}/assistant/apply", headers=headers, json={"base_version_id": v2["plan_version"]["version_id"], "operations": removal["operations"]})
    assert stale.status_code == 409

    undone = client.post(f"/api/sessions/{session_id}/assistant/undo", headers=headers, json={"base_version_id": v3["plan_version"]["version_id"]})
    assert undone.status_code == 200, undone.text
    v4 = undone.json()
    assert v4["plan_version"]["origin"] == "undo"
    assert [row["asset_id"] for row in v4["placements"]] == [ids["ppe"]]
    assert v4["settings"] == v2["settings"]
    versions = client.get(f"/api/sessions/{session_id}/editing-plan/versions", headers=headers).json()["versions"]
    assert [row["number"] for row in versions] == [1, 2, 3, 4]
    stranger = client.post(f"/api/sessions/{session_id}/assistant/propose", headers={"X-Studio-Client": str(uuid.uuid4())}, json={"message": "hata do"})
    assert stranger.status_code == 404
