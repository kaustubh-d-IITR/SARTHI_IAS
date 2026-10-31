"""Phase 4: frame sampling, visual providers, evidence matching, review edits, versions, titles.

All model output here is synthetic. A fake worker returns small concept vectors so
the real matcher, cache, versioning, and render code can be checked without
loading CLIP, the text embedder, or calling Gemini.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_phase3 import FakeBridge, media  # noqa: F401  (media is a fixture)

from app.config import Settings
from app.factory import create_app
from app.processing import matching
from app.processing.jobs import Services
from app.processing.titles import apply_title, clean_title, suggest_title
from app.services.visual.analysis import (
    DISTRACTOR_CAPTIONS,
    VOCABULARY,
    AnalysisBatch,
    AssetInput,
    CLIPVisualAnalysisProvider,
    DescriptionOnlyProvider,
    GeminiVisualAnalysisProvider,
    choose_provider,
)
from app.services.visual.sampling import frame_count, sample_clip, timestamps

AXES = {
    "ppe": ("ppe", "gown", "mask", "covid", "protective", "health worker"),
    "pen": ("pen ", "pen.", "writing", "white paper", "on paper", "notes"),
    "parl": ("parliament", "government building"),
}
PPE_DESCRIPTION = "A health worker on COVID duty taking off a protective gown and mask."
PEN_DESCRIPTION = "A fountain pen writing on white paper."
PARLIAMENT_DESCRIPTION = "Indian Parliament building exterior with people entering the premises."


def concept_vector(text: str, concept: str | None = None, image: bool = False) -> list[float]:
    vector = np.zeros(8)
    vector[3] = 0.2 if image else 0.3
    lowered = f" {text.lower()} "
    for index, (name, words) in enumerate(AXES.items()):
        if concept == name or (concept is None and any(word in lowered for word in words)):
            vector[index] = 1.0
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vector[4:] = [byte / 2550.0 for byte in digest[:4]]
    return list(vector / np.linalg.norm(vector))


class FakeWorker:
    """Answers worker requests. Frames take the concept registered for their clip hash."""

    def __init__(self, concepts: dict[str, str] | None = None, fail: set[str] | None = None) -> None:
        self.concepts = concepts or {}
        self.fail = fail or set()
        self.requests: list[dict] = []

    def available(self):
        return True, ""

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        response: dict = {"errors": {}}
        if "clip" in request:
            if "clip" in self.fail:
                response["errors"]["clip"] = "RuntimeError: synthetic CLIP failure"
            else:
                images = []
                for path in request["clip"]["images"]:
                    sha = Path(path).parent.parent.name
                    images.append(concept_vector(path, self.concepts.get(sha, "none"), image=True))
                response["clip"] = {"model": "fake-clip", "image": images, "text": [concept_vector(text) for text in request["clip"]["texts"]], "logit_scale": 100.0}
        if "embed" in request:
            if "embed" in self.fail:
                response["errors"]["embed"] = "RuntimeError: synthetic embed failure"
            else:
                response["embed"] = {"model": "fake-embed", "vectors": [concept_vector(text) for text in request["embed"]["texts"]]}
        if "gemini" in request:
            response["errors"]["gemini"] = "GeminiError: synthetic quota exhausted"
        return response


def _ffmpeg(*args: str) -> None:
    completed = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        pytest.skip("ffmpeg could not write a fixture")


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("phase4_clips")
    out = {}
    for name, source in (("ppe", "testsrc=s=320x240:r=30:d=6"), ("pen", "testsrc2=s=320x240:r=30:d=6"), ("lesson", "testsrc2=s=360x640:r=30:d=14")):
        path = root / f"{name}.mp4"
        if name == "lesson":
            _ffmpeg("-f", "lavfi", "-i", source, "-f", "lavfi", "-i", "anoisesrc=d=14:c=pink:a=0.4:seed=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(path))
        else:
            _ffmpeg("-f", "lavfi", "-i", source, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
        out[name] = path
    black = root / "black.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black))
    out["black"] = black
    flat = root / "flat.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x808080:s=320x240:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(flat))
    out["flat"] = flat
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transcript(include_pen: bool = False) -> dict:
    rows = [
        ("s0", 0.3, 2.6, "Welcome to the lesson on how the examination works."),
        ("s1", 2.8, 5.0, "The exam has three different papers to prepare for."),
        ("s2", 5.2, 8.0, "I was on COVID duty wearing a PPE gown and mask."),
        ("s3", 8.2, 10.6, "Still I came back home and studied late at night."),
        ("s4", 10.8, 13.4, "Write your notes with a pen on paper every day." if include_pen else "Consistency matters more than talent in this exam."),
    ]
    return {
        "language": "en",
        "language_source": "provider",
        "translated_to_english": False,
        "method": "elevenlabs",
        "model": "scribe_v2",
        "source_sha256": "fixture",
        "segments": [{"id": sid, "text": text, "original_text": text, "start": start, "end": end, "confidence": None, "timing_precision": "segment", "words": []} for sid, start, end, text in rows],
    }


# ---------------------------------------------------------------- sampling


def test_frame_plan_is_adaptive_and_never_only_the_first_frame():
    assert frame_count(0.6) == 2
    assert frame_count(4.0) == 4
    assert frame_count(15.0) == 6
    assert frame_count(55.0) == 10
    assert frame_count(3600.0) == 12
    stamps = timestamps(4.0)
    assert len(stamps) == 4 and stamps[0] > 0.0 and stamps[-1] < 4.0
    assert len(set(stamps)) == len(stamps)


def test_black_and_flat_frames_are_rejected_and_a_real_clip_keeps_frames(tmp_path: Path, clips: dict):
    black = sample_clip(clips["black"], 4.0, tmp_path / "black")
    assert black["kept"] == 0
    assert {frame["reason"] for frame in black["frames"]} == {"black"}
    assert black["thumbnail"] is None
    flat = sample_clip(clips["flat"], 4.0, tmp_path / "flat")
    assert flat["kept"] == 0
    assert {frame["reason"] for frame in flat["frames"]} == {"flat"}
    real = sample_clip(clips["pen"], 6.0, tmp_path / "pen")
    assert real["kept"] >= 2
    assert (tmp_path / "pen" / "thumb.jpg").is_file()
    from PIL import Image

    with Image.open(tmp_path / "pen" / "thumb.jpg") as image:
        assert image.width == 320


# ---------------------------------------------------------------- providers and cache


def _inputs(clips: dict, name: str, description: str, asset_id: str | None = None) -> AssetInput:
    path = clips[name]
    return AssetInput(asset_id or name, _sha(path), path, 6.0, description, f"{name}.mp4")


def test_provider_selection(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setenv("STUDIO_VISUAL_PROVIDER", "none")
    assert isinstance(choose_provider(worker), DescriptionOnlyProvider)
    monkeypatch.setenv("STUDIO_VISUAL_PROVIDER", "auto")
    assert isinstance(choose_provider(worker), CLIPVisualAnalysisProvider)
    monkeypatch.setenv("STUDIO_VISUAL_PROVIDER", "gemini")
    assert isinstance(choose_provider(worker), GeminiVisualAnalysisProvider)

    class Missing(FakeWorker):
        def available(self):
            return False, "no interpreter"

    monkeypatch.setenv("STUDIO_VISUAL_PROVIDER", "auto")
    assert isinstance(choose_provider(Missing()), DescriptionOnlyProvider)


def test_clip_analysis_is_stored_cached_by_hash_and_invalidated_when_the_file_changes(tmp_path: Path, clips: dict):
    clip = tmp_path / "ppe.mp4"
    shutil.copyfile(clips["ppe"], clip)
    asset = AssetInput("asset-1", _sha(clip), clip, 6.0, PPE_DESCRIPTION, "ppe.mp4")
    worker = FakeWorker({asset.sha256: "ppe"})
    provider = CLIPVisualAnalysisProvider(worker)
    root = tmp_path / "visual"
    first = provider.analyse(root, [asset], ["some speech"], [])
    record = first.analyses["asset-1"]
    assert record["performed"] is True
    assert record["provider"] == "clip" and record["model"] == "openai/clip-vit-base-patch32"
    assert record["sha256"] == asset.sha256
    assert record["frames"] and all({"timestamp", "frame", "description", "confidence", "subjects", "actions"} <= set(frame) for frame in record["frames"])
    assert record["summary"].startswith("CLIP ranks") or record["summary"].startswith("No label")
    assert "not a caption" in record["method"]
    sent = len(worker.requests[0]["clip"]["images"])
    assert sent >= 2

    second = provider.analyse(root, [asset], ["some speech"], [])
    assert worker.requests[1]["clip"]["images"] == []
    assert second.analyses["asset-1"].get("reused_from_cache") is True
    assert len(second.frame_vectors["asset-1"]) == sent

    clip.write_bytes(clip.read_bytes() + b"changed")
    changed = AssetInput("asset-1", _sha(clip), clip, 6.0, PPE_DESCRIPTION, "ppe.mp4")
    assert changed.sha256 != asset.sha256
    worker.concepts[changed.sha256] = "ppe"
    third = provider.analyse(root, [changed], ["some speech"], [])
    assert len(worker.requests[2]["clip"]["images"]) == sent
    assert third.analyses["asset-1"]["sha256"] == changed.sha256
    assert not third.analyses["asset-1"].get("reused_from_cache")


def test_provider_failure_records_diagnostics_never_fabricates_and_is_retried(tmp_path: Path, clips: dict):
    asset = _inputs(clips, "ppe", PPE_DESCRIPTION)
    root = tmp_path / "visual"
    broken = CLIPVisualAnalysisProvider(FakeWorker({asset.sha256: "ppe"}, fail={"clip"}))
    batch = broken.analyse(root, [asset], ["speech"], ["speech"])
    record = batch.analyses[asset.asset_id]
    assert record["performed"] is False
    assert "synthetic CLIP failure" in record["reason"]
    assert record["frames"] == [] and record["subjects"] == [] and record["summary"] == ""
    assert batch.frame_vectors == {}
    assert "clip" in batch.errors
    working = FakeWorker({asset.sha256: "ppe"})
    retried = CLIPVisualAnalysisProvider(working).analyse(root, [asset], ["speech"], [])
    assert retried.analyses[asset.asset_id]["performed"] is True
    assert working.requests[0]["clip"]["images"]


def test_gemini_failure_is_reported_and_not_cached(tmp_path: Path, clips: dict):
    asset = _inputs(clips, "ppe", PPE_DESCRIPTION)
    worker = FakeWorker()
    batch = GeminiVisualAnalysisProvider(worker).analyse(tmp_path, [asset], [], ["speech"])
    record = batch.analyses[asset.asset_id]
    assert record["performed"] is False
    assert "quota" in record["reason"]
    assert GeminiVisualAnalysisProvider(worker).cached(tmp_path, asset) is None


# ---------------------------------------------------------------- matching


def _batch(transcript: dict, assets: list[tuple[str, str, str | None]], *, visual: bool = True, embed: bool = True) -> AnalysisBatch:
    """Synthetic analysis: (asset_id, description, frame concept or None)."""
    batch = AnalysisBatch(provider="clip" if visual else "description_only", model="fake")
    windows = matching.build_windows(transcript)
    texts = [window.text for window in windows] + [description for _, description, _ in assets]
    for text in texts + [label for label, _ in VOCABULARY] + DISTRACTOR_CAPTIONS:
        batch.clip_text[text] = np.asarray(concept_vector(text), dtype=np.float32)
        if embed:
            batch.embed_text[text] = np.asarray(concept_vector(text), dtype=np.float32)
    for asset_id, _description, concept in assets:
        if visual and concept:
            batch.frame_vectors[asset_id] = [(t, np.asarray(concept_vector(f"{asset_id}{t}", concept, image=True), dtype=np.float32)) for t in (0.75, 2.25, 3.75, 5.25)]
            batch.analyses[asset_id] = {"performed": True, "provider": "clip", "summary": f"synthetic {concept} frames"}
        else:
            batch.analyses[asset_id] = {"performed": False, "provider": "description_only", "summary": ""}
    return batch


def _catalogue(*rows: tuple[str, str, float]) -> list[dict]:
    return [{"asset_id": asset_id, "filename": f"{asset_id}.mp4", "description": description, "duration_seconds": seconds} for asset_id, description, seconds in rows]


def test_strong_match_uses_frames_and_meaning():
    transcript = _transcript()
    batch = _batch(transcript, [("ppe", PPE_DESCRIPTION, "ppe")])
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("ppe", PPE_DESCRIPTION, 6.0)), batch, 14.0)
    assert len(chosen) == 1
    pick = chosen[0]
    assert pick["segment_ids"] == ["s2"], "a pair window must not win a tie and spill over unrelated speech"
    assert pick["evidence"] == "combined"
    assert pick["evidence_sources"] == ["description", "visual_analysis"]
    assert pick["visual_relevance"] >= matching.MIN_VISUAL and pick["description_relevance"] >= matching.MIN_DESCRIPTION
    assert pick["score"] >= matching.ACCEPT
    assert pick["source_out"] - pick["source_in"] == pytest.approx(pick["speech_end"] - pick["speech_start"], abs=1e-3)
    assert 0.0 <= pick["source_in"] and pick["source_out"] <= 6.0
    assert "frames match this speech" in pick["explanation"]
    assert evaluations[0]["status"] == "selected"


def test_weak_match_abstains_and_keeps_the_teacher():
    transcript = _transcript()
    batch = _batch(transcript, [("pen", PEN_DESCRIPTION, "pen")])
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("pen", PEN_DESCRIPTION, 6.0)), batch, 14.0)
    assert chosen == []
    assert evaluations[0]["status"] == "abstained"
    assert evaluations[0]["reason"]
    assert all(not row["accepted"] for row in evaluations[0]["top_windows"])


def test_one_shared_word_is_not_enough_without_models():
    transcript = _transcript()
    lonely = "A doctor examination room with equipment"
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("doc", lonely, 6.0)), None, 14.0)
    assert chosen == []
    assert evaluations[0]["evidence_mode"] == "description_lexical"


def test_description_that_the_frames_contradict_is_rejected():
    transcript = _transcript()
    transcript["segments"][3]["original_text"] = transcript["segments"][3]["text"] = "The Indian Parliament building is where the government meets."
    batch = _batch(transcript, [("mislabelled", PARLIAMENT_DESCRIPTION, "pen")])
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("mislabelled", PARLIAMENT_DESCRIPTION, 6.0)), batch, 14.0)
    assert chosen == []
    assert evaluations[0]["contradicted"] is True
    assert evaluations[0]["consistency"] < 0
    assert "do not resemble" in evaluations[0]["reason"]
    without_frames = _batch(transcript, [("mislabelled", PARLIAMENT_DESCRIPTION, None)], visual=False)
    trusted, _ = matching.match_assets(transcript, _catalogue(("mislabelled", PARLIAMENT_DESCRIPTION, 6.0)), without_frames, 14.0)
    assert len(trusted) == 1 and trusted[0]["evidence"] == "description"


def test_description_only_fallback_is_labelled_and_never_claims_visual_checks():
    transcript = _transcript()
    batch = _batch(transcript, [("ppe", PPE_DESCRIPTION, None)], visual=False)
    chosen, _ = matching.match_assets(transcript, _catalogue(("ppe", PPE_DESCRIPTION, 6.0)), batch, 14.0)
    assert len(chosen) == 1
    pick = chosen[0]
    assert pick["evidence"] == "description" and pick["evidence_sources"] == ["description"]
    assert pick["visual_relevance"] is None
    assert "No frames were analysed" in pick["explanation"]
    assert pick["confidence"] <= 0.6


def test_short_clip_reuse_and_cap_rules():
    transcript = _transcript()
    batch = _batch(transcript, [("short", PPE_DESCRIPTION, "ppe")])
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("short", PPE_DESCRIPTION, 1.5)), batch, 14.0)
    assert chosen == [] and "shorter than 2" in evaluations[0]["reason"]
    batch = _batch(transcript, [("a", PPE_DESCRIPTION, "ppe"), ("b", PPE_DESCRIPTION, "ppe")])
    chosen, evaluations = matching.match_assets(transcript, _catalogue(("a", PPE_DESCRIPTION, 6.0), ("b", PPE_DESCRIPTION, 6.0)), batch, 14.0)
    assert len({item["asset_id"] for item in chosen}) == len(chosen)
    covered = sum(item["speech_end"] - item["speech_start"] for item in chosen)
    assert covered <= matching.BROLL_CAP * 14.0 + 1e-6
    for left, right in zip(chosen, chosen[1:]):
        assert right["speech_start"] >= left["speech_end"]
    tiny_lesson, _ = matching.match_assets(transcript, _catalogue(("a", PPE_DESCRIPTION, 6.0)), batch, 3.0)
    assert tiny_lesson == []


def test_matcher_cap_is_the_engine_cap():
    from app.processing.plan_adapt import engine_broll_cap

    assert matching.BROLL_CAP == engine_broll_cap() == 0.58


# ---------------------------------------------------------------- titles


def test_title_is_kept_exactly_and_suggestions_are_only_suggestions():
    assert clean_title("  भारत की संसद: [[Parliament]] explained  ") == "भारत की संसद: [[Parliament]] explained"
    assert clean_title("Yeh UPSC ka sach hai") == "Yeh UPSC ka sach hai"
    assert clean_title("   ") == ""
    with pytest.raises(Exception):
        clean_title("x" * 121)
    one_word = {"segments": [{"original_text": "blindly blindly blindly"}]}
    assert suggest_title(one_word) is None
    rich = {"segments": [{"original_text": "Read the newspaper and NCERT for UPSC. The newspaper matters."}]}
    suggestion = suggest_title(rich)
    assert suggestion["status"] == "ai_suggestion"
    assert len(suggestion["text"].split()) >= 3
    plan = {"decisions": [{"id": "title", "action": "title", "text": "BLINDLY", "source": "observed", "confidence": 0.7, "reason": "x"}]}
    apply_title(plan, "")
    assert plan["decisions"][0]["text"] == ""
    apply_title(plan, "From [[NEET]] to [[UPSC]]")
    assert plan["decisions"][0]["text"] == "From [[NEET]] to [[UPSC]]"


# ---------------------------------------------------------------- API: processing, review, versions, render


def _app(tmp_path: Path, media: dict, worker: FakeWorker, transcript: dict | None = None, visual=None):
    settings = Settings(data_dir=tmp_path / "studio", max_file_bytes=20_000_000, max_session_bytes=60_000_000, cors_origins=())
    bridge = FakeBridge(media)
    provider = visual or (lambda: CLIPVisualAnalysisProvider(worker))
    services = Services(transcribe=lambda _p, _c: transcript or _transcript(), select_auto=lambda *_: [], visual=provider)
    app = create_app(settings, services, bridge)
    return TestClient(app), app, bridge


def _upload(client: TestClient, owner: str, path: Path, kind: str, description: str = "") -> dict:
    files = {"file": (path.name, path.read_bytes(), "video/mp4")}
    url = "/api/uploads/raw-video" if kind == "raw" else "/api/uploads/broll"
    data = {} if kind == "raw" else {"description": description}
    response = client.post(url, headers={"X-Studio-Client": owner}, data=data, files=files)
    assert response.status_code == 200, response.text
    return response.json()


def _session(client: TestClient, clips: dict, title: str | None = None, extra: bool = True) -> tuple[str, str, dict]:
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, clips["lesson"], "raw")
    ppe = _upload(client, owner, clips["ppe"], "broll", PPE_DESCRIPTION)
    broll = [{"asset_id": ppe["asset_id"], "description": PPE_DESCRIPTION}]
    ids = {"ppe": ppe["asset_id"]}
    if extra:
        pen = _upload(client, owner, clips["pen"], "broll", PEN_DESCRIPTION)
        broll.append({"asset_id": pen["asset_id"], "description": PEN_DESCRIPTION})
        ids["pen"] = pen["asset_id"]
    body = {"mode": "my_broll", "raw_video_id": raw["upload_id"], "broll": broll}
    if title is not None:
        body["title"] = title
    created = client.post("/api/sessions", headers={"X-Studio-Client": owner}, json=body)
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/process", headers={"X-Studio-Client": owner})
    status = client.get(f"/api/sessions/{session_id}/status", headers={"X-Studio-Client": owner}).json()
    assert status["status"] == "plan_ready", status
    return session_id, owner, ids


def _edit(client, session_id, owner, plan, placements, **extra):
    body = {"base_version_id": plan["plan_version"]["version_id"], "placements": placements, **extra}
    return client.post(f"/api/sessions/{session_id}/editing-plan/edits", headers={"X-Studio-Client": owner}, json=body)


def _as_edit(placement: dict, **changes) -> dict:
    row = {key: placement[key] for key in ("asset_id", "speech_start", "speech_end", "source_in", "source_out", "approval")}
    row.update(changes)
    return row


def test_mode_b_pipeline_analyses_frames_matches_and_serves_thumbnails(tmp_path: Path, media: dict, clips: dict):
    worker = FakeWorker({_sha(clips["ppe"]): "ppe", _sha(clips["pen"]): "pen"})
    client, app, _bridge = _app(tmp_path, media, worker)
    session_id, owner, ids = _session(client, clips, title="From [[Duty]] to UPSC")
    headers = {"X-Studio-Client": owner}
    plan = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    assert plan["visual_analysis"]["provider"] == "clip" and plan["visual_analysis"]["performed"] == 2
    assert len(plan["placements"]) == 1
    pick = plan["placements"][0]
    assert pick["asset_id"] == ids["ppe"] and pick["evidence"] == "combined"
    assert pick["description"] == PPE_DESCRIPTION and pick["visual_summary"]
    pen_eval = next(row for row in plan["evaluations"] if row["asset_id"] == ids["pen"])
    assert pen_eval["status"] == "abstained" and pen_eval["reason"]
    catalogue = {row["asset_id"]: row for row in plan["catalogue"]}
    analysis = catalogue[ids["ppe"]]["visual_analysis"]
    assert analysis["performed"] is True and analysis["frames"]
    assert "image_vectors" not in json.dumps(plan)
    assert catalogue[ids["ppe"]]["description"] == PPE_DESCRIPTION
    assert plan["title"]["text"] == "From [[Duty]] to UPSC" and plan["title"]["status"] == "user_confirmed"
    assert plan["title_decision_text"] == "From [[Duty]] to UPSC"
    assert plan["plan_version"]["number"] == 1 and plan["plan_version"]["origin"] == "generated"
    thumb = client.get(f"/api/sessions/{session_id}/assets/{ids['ppe']}/thumbnail", headers=headers)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/sessions/{session_id}/assets/{ids['ppe']}/thumbnail", headers={"X-Studio-Client": str(uuid.uuid4())}).status_code == 404
    assert ":\\" not in json.dumps(plan)
    stored = (app.state.store.root / "visual" / _sha(clips["ppe"]) / "clip.json")
    assert json.loads(stored.read_text(encoding="utf-8"))["performed"] is True


def test_mode_a_does_not_run_visual_analysis(tmp_path: Path, media: dict, clips: dict):
    def forbidden():
        raise AssertionError("Mode A must not use the Mode B visual provider")

    client, _app_, _bridge = _app(tmp_path, media, FakeWorker(), visual=forbidden)
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, clips["lesson"], "raw")
    created = client.post("/api/sessions", headers={"X-Studio-Client": owner}, json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": []}).json()
    client.post(f"/api/sessions/{created['session_id']}/process", headers={"X-Studio-Client": owner})
    status = client.get(f"/api/sessions/{created['session_id']}/status", headers={"X-Studio-Client": owner}).json()
    assert status["status"] == "plan_ready"


def test_review_edits_validate_and_create_versions(tmp_path: Path, media: dict, clips: dict):
    worker = FakeWorker({_sha(clips["ppe"]): "ppe", _sha(clips["pen"]): "pen"})
    client, app, _bridge = _app(tmp_path, media, worker)
    session_id, owner, ids = _session(client, clips)
    headers = {"X-Studio-Client": owner}
    v1 = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    pick = v1["placements"][0]
    length = pick["speech_end"] - pick["speech_start"]

    bad_source = _edit(client, session_id, owner, v1, [_as_edit(pick, source_in=0.0, source_out=round(length - 0.5, 3))])
    assert bad_source.status_code == 422 and "as long as" in bad_source.json()["error"]["message"]
    past_clip = _edit(client, session_id, owner, v1, [_as_edit(pick, source_in=5.0, source_out=5.0 + length)])
    assert past_clip.status_code == 422
    bad_timeline = _edit(client, session_id, owner, v1, [_as_edit(pick, speech_start=13.0, speech_end=13.0 + length)])
    assert bad_timeline.status_code == 422
    too_short = _edit(client, session_id, owner, v1, [_as_edit(pick, speech_end=pick["speech_start"] + 1.0, source_out=pick["source_in"] + 1.0)])
    assert too_short.status_code == 422
    foreign_id, foreign_owner, foreign_ids = _session(client, clips, extra=False)
    stolen = _edit(client, session_id, owner, v1, [_as_edit(pick, asset_id=foreign_ids["ppe"])])
    assert stolen.status_code == 422 and stolen.json()["error"]["code"] == "asset_not_in_session"
    overlap = _edit(
        client,
        session_id,
        owner,
        v1,
        [
            _as_edit(pick),
            {"asset_id": ids["pen"], "speech_start": pick["speech_start"] + 0.5, "speech_end": pick["speech_start"] + 2.5, "source_in": 0.0, "source_out": 2.0, "approval": "approved"},
        ],
    )
    assert overlap.status_code == 422 and overlap.json()["error"]["code"] == "overlapping_cutaways"
    over_cap = _edit(
        client,
        session_id,
        owner,
        v1,
        [
            {"asset_id": ids["ppe"], "speech_start": 0.2, "speech_end": 6.0, "source_in": 0.0, "source_out": 5.8, "approval": "approved"},
            {"asset_id": ids["pen"], "speech_start": 6.5, "speech_end": 9.5, "source_in": 0.0, "source_out": 3.0, "approval": "approved"},
        ],
    )
    assert over_cap.status_code == 422 and over_cap.json()["error"]["code"] == "broll_cap"
    versions = client.get(f"/api/sessions/{session_id}/editing-plan/versions", headers=headers).json()
    assert [row["number"] for row in versions["versions"]] == [1]

    approved = _edit(client, session_id, owner, v1, [_as_edit(pick)])
    assert approved.status_code == 200, approved.text
    v2 = approved.json()
    assert v2["plan_version"]["parent_version_id"] == v1["plan_version"]["version_id"]
    assert v2["placements"][0]["approved_by"] == "user" or v2["placements"][0]["evidence"] == "combined"

    shifted = _edit(client, session_id, owner, v2, [_as_edit(v2["placements"][0], source_in=0.2, source_out=round(0.2 + length, 3))])
    assert shifted.status_code == 200, shifted.text
    v3 = shifted.json()
    assert v3["placements"][0]["source_in"] == pytest.approx(0.2)
    assert v3["placements"][0]["evidence"] == "manual" and v3["placements"][0]["approved_by"] == "user"

    swapped = _edit(
        client,
        session_id,
        owner,
        v3,
        [
            _as_edit(v3["placements"][0], approval="rejected"),
            {"asset_id": ids["pen"], "speech_start": 10.8, "speech_end": 13.4, "source_in": 1.0, "source_out": 3.6, "approval": "approved"},
        ],
    )
    assert swapped.status_code == 200, swapped.text
    v4 = swapped.json()
    assert [row["asset_id"] for row in v4["placements"]] == [ids["pen"]]
    assert v4["rejected_placements"][0]["asset_id"] == ids["ppe"]
    cutaways = [row for row in v4["picture_decisions"] if row["picture_role"] == "show_broll"]
    assert [row["asset_id"] for row in cutaways] == [ids["pen"]]

    teacher_only = _edit(client, session_id, owner, v4, [_as_edit(v4["placements"][0], approval="rejected")])
    assert teacher_only.status_code == 200
    v5 = teacher_only.json()
    assert v5["placements"] == []
    assert all(row["picture_role"] == "show_teacher" for row in v5["picture_decisions"])

    stale = _edit(client, session_id, owner, v1, [])
    assert stale.status_code == 409

    versions = client.get(f"/api/sessions/{session_id}/editing-plan/versions", headers=headers).json()
    assert [row["number"] for row in versions["versions"]] == [1, 2, 3, 4, 5]
    assert versions["current_version_id"] == v5["plan_version"]["version_id"]
    old = client.get(f"/api/sessions/{session_id}/editing-plan/versions/{v1['plan_version']['version_id']}", headers=headers).json()
    assert old["placements"][0]["source_in"] == pick["source_in"]
    work = app.state.store.work_dir(session_id)
    assert len(list((work / "plans").glob("*.json"))) == 5


def test_title_edit_render_uses_the_exact_version_and_keeps_earlier_videos(tmp_path: Path, media: dict, clips: dict):
    worker = FakeWorker({_sha(clips["ppe"]): "ppe", _sha(clips["pen"]): "pen"})
    client, app, _bridge = _app(tmp_path, media, worker)
    session_id, owner, _ids = _session(client, clips)
    headers = {"X-Studio-Client": owner}
    v1 = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    assert v1["title"]["status"] == "none" and v1["title_decision_text"] == ""
    assert v1["title"]["suggestion"] is None or v1["title"]["suggestion"]["status"] == "ai_suggestion"

    assert client.post(f"/api/sessions/{session_id}/render", headers=headers).status_code == 200
    first = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert first["status"] == "completed", first
    assert first["plan_version_id"] == v1["plan_version"]["version_id"]
    assert first["title"]["text"] == ""
    store = app.state.store
    first_file = store.renders / session_id / first["render_id"] / "final.mp4"
    first_hash = _sha(first_file)

    title = "यूपीएससी की सच्चाई: [[Mistakes]] hongi"
    edited = _edit(client, session_id, owner, v1, [_as_edit(row) for row in v1["placements"]], title=title, change_title=True)
    assert edited.status_code == 200, edited.text
    v2 = edited.json()
    assert v2["title"]["text"] == title and v2["title_decision_text"] == title
    assert client.get(f"/api/sessions/{session_id}/status", headers=headers).json()["plan_version"] == v2["plan_version"]["version_id"]

    assert client.post(f"/api/sessions/{session_id}/render", headers=headers).status_code == 200
    second = client.get(f"/api/sessions/{session_id}/render-status", headers=headers).json()
    assert second["status"] == "completed"
    assert second["plan_version_id"] == v2["plan_version"]["version_id"]
    assert second["title"]["text"] == title
    root = store.renders / session_id / second["render_id"]
    html = (root / "composition" / "compositions" / "title-card.html").read_text(encoding="utf-8")
    assert all(word in html for word in ("यूपीएससी", "की", "सच्चाई:", "hongi")) and '<span class="accent">Mistakes</span>' in html
    manifest = json.loads((root / "input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan_version_id"] == v2["plan_version"]["version_id"] and manifest["title"]["text"] == title
    assert _sha(first_file) == first_hash
    versions = client.get(f"/api/sessions/{session_id}/editing-plan/versions", headers=headers).json()
    by_id = {row["version_id"]: row for row in versions["versions"]}
    assert [r["render_id"] for r in by_id[v1["plan_version"]["version_id"]]["renders"]] == [first["render_id"]]
    assert [r["render_id"] for r in by_id[v2["plan_version"]["version_id"]]["renders"]] == [second["render_id"]]


def test_title_is_validated_at_session_creation(tmp_path: Path, media: dict, clips: dict):
    client, _app_, _bridge = _app(tmp_path, media, FakeWorker())
    owner = str(uuid.uuid4())
    raw = _upload(client, owner, clips["lesson"], "raw")
    too_long = client.post("/api/sessions", headers={"X-Studio-Client": owner}, json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": [], "title": "x" * 150})
    assert too_long.status_code == 422
    kept = client.post("/api/sessions", headers={"X-Studio-Client": owner}, json={"mode": "auto_edit", "raw_video_id": raw["upload_id"], "broll": [], "title": "Yeh hai [[UPSC]] ka sach"})
    assert kept.status_code == 200
    assert kept.json()["title"] == {"text": "Yeh hai [[UPSC]] ka sach", "status": "user_confirmed", "source": "user"}


def test_rematch_retries_failed_analysis_as_a_new_version(tmp_path: Path, media: dict, clips: dict):
    worker = FakeWorker({_sha(clips["ppe"]): "ppe", _sha(clips["pen"]): "pen"}, fail={"clip"})
    client, _app_, _bridge = _app(tmp_path, media, worker)
    session_id, owner, ids = _session(client, clips)
    headers = {"X-Studio-Client": owner}
    v1 = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    assert v1["visual_analysis"]["performed"] == 0
    assert all(row["evidence"] != "combined" for row in v1["placements"])
    assert all(row["visual_relevance"] is None for row in v1["placements"])
    worker.fail.clear()
    assert client.post(f"/api/sessions/{session_id}/rematch", headers=headers).status_code == 200
    v2 = client.get(f"/api/sessions/{session_id}/editing-plan", headers=headers).json()
    assert v2["plan_version"]["origin"] == "rematch"
    assert v2["plan_version"]["parent_version_id"] == v1["plan_version"]["version_id"]
    assert v2["visual_analysis"]["performed"] == 2
    assert v2["placements"][0]["asset_id"] == ids["ppe"] and v2["placements"][0]["evidence"] == "combined"
