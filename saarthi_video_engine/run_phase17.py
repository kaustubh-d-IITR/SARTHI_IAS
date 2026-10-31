"""Phase 17. Editorial plans, Gemini verification, and a render of accepted segments only."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import run_phase5 as phase5
import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.gemini_api import GeminiError, TEXT_MODEL, generate_structured, video_generation_status  # noqa: E402
from broll_intel.pipeline import build, load_json, summarize  # noqa: E402
from pydantic import BaseModel  # noqa: E402

PHASE7 = ROOT / "outputs" / "dr_shivin_phase7"
PHASE16 = ROOT / "outputs" / "dr_shivin_phase16"
OUT = ROOT / "outputs" / "dr_shivin_phase17"


class _Probe(BaseModel):
    subject: str
    negation: bool


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    OUT.mkdir(parents=True, exist_ok=True)
    status = video_generation_status()
    (OUT / "generation_report.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print("smoke", TEXT_MODEL, flush=True)
    smoke = _smoke()
    print("smoke", smoke.get("status"), smoke.get("subject"), smoke.get("negation"), smoke.get("detail", ""), flush=True)
    (OUT / "verification_report.json").write_text(json.dumps({"smoke": smoke, "calls": []}, indent=2), encoding="utf-8")
    if "--smoke" in args:
        print(json.dumps({"text": smoke.get("status"), "generation": status.get("available")}), flush=True)
        return 0 if smoke.get("status") == "ok" else 1
    candidates = _candidates()
    calls = []
    plans = {}
    coverage = []
    for video_id, lesson_seconds in (("VIDEO_1", 55.26), ("VIDEO_2", 41.44)):
        transcript = load_json(PHASE7 / video_id / "transcript.json")
        if transcript.get("method") != "elevenlabs":
            raise SystemExit(f"{video_id} has no reusable ElevenLabs transcript")
        rows, used = build(transcript, video_id, candidates, verify="--no-verify" not in args)
        plans[video_id] = rows
        calls.extend(used)
        (OUT / f"editorial_plan_{video_id}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        report = summarize(rows, lesson_seconds)
        report["video_id"] = video_id
        coverage.append(report)
        print(video_id, report["verified_broll_coverage"], "verified plan", flush=True)
    (OUT / "coverage_report.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    manifest = [row for rows in plans.values() for row in rows]
    (OUT / "candidate_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "retrieval_report.json").write_text(
        json.dumps(_retrieval(manifest, candidates), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report = {"smoke": smoke, "calls": calls, "model": TEXT_MODEL}
    (OUT / "verification_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _contact(manifest)
    if "--no-render" in args:
        return 0
    return _render(plans, candidates)


def _smoke() -> dict:
    try:
        parsed = generate_structured(
            'The sentence is "Na hi maine newspaper padha." The subject must be the physical object being discussed, not the speaker. negation is true when the speaker says that action did not happen.',
            _Probe,
        )
        return {"status": "ok", "model": TEXT_MODEL, "subject": parsed.subject, "negation": parsed.negation}
    except GeminiError as error:
        return {"status": "error", "model": TEXT_MODEL, "detail": str(error)}


def _candidates() -> list[dict]:
    path = PHASE16 / "asset_cache" / "candidates.json"
    if not path.is_file():
        return []
    return [row for row in load_json(path) if row.get("kind") == "video" and not row.get("rejected")]


def _retrieval(decisions: list[dict], candidates: list[dict]) -> dict:
    return {
        "providers": ["commons"],
        "pexels": "PEXELS_API_KEY is not set, so Pexels is not called.",
        "openverse": "The video route returns HTTP 404 and is not used.",
        "candidate_pool": len(candidates),
        "scheduled": sum(1 for row in decisions if row.get("schedule") == "broll"),
        "rejected": sum(1 for row in decisions if row.get("retrieval_status") == "rejected"),
        "unfilled": sum(1 for row in decisions if row.get("retrieval_status") == "unfilled"),
        "teacher": sum(1 for row in decisions if row.get("schedule") == "teacher"),
    }


def _contact(decisions: list[dict]) -> None:
    folder = OUT / "contact"
    folder.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(decisions):
        frames = ((row.get("verification_evidence") or {}).get("frames") or [])[:1]
        if not frames:
            continue
        source = Path(frames[0])
        if source.is_file():
            shutil.copyfile(source, folder / f"{row['lesson_id']}_{index}.jpg")


def _render(plans: dict, candidates: list[dict]) -> int:
    accepted = []
    by_page = {row.get("page_url"): row for row in candidates}
    for video_id, rows in plans.items():
        for decision in rows:
            if decision.get("retrieval_status") != "verified":
                continue
            source = by_page.get(decision.get("selected_asset_id"))
            if not source:
                continue
            copied = dict(source)
            if decision.get("overlay_type") == "negation_cross":
                copied["graphic"] = "negation_cross"
            accepted.append(copied)
    if not accepted:
        print("no verified segment to render", flush=True)
        return 0
    _bind()
    phase5.CACHE.mkdir(parents=True, exist_ok=True)
    rows = accepted + phase5._music_record()
    (phase5.CACHE / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [row["page_url"] for row in rows if row.get("page_url")]
    (OUT / "accepted.json").write_text(json.dumps({"page_urls": pages}, indent=2), encoding="utf-8")
    phase5.MATCH_REFERENCE_LOOK = True
    return phase8.render_lessons()


def _bind() -> None:
    phase8.OUT = OUT
    phase8.SUFFIX = "phase17"
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase17"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.TITLE_OVERRIDES = phase5.TITLE_OVERRIDES


if __name__ == "__main__":
    raise SystemExit(main())
