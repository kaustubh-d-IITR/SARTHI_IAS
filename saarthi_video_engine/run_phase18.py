"""Phase 18. Resume Phase 17, retry only API failures, render accepted segments."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import run_phase5 as phase5
import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.gemini_api import GeminiError, generate_structured, video_generation_status  # noqa: E402
from broll_intel.pipeline import VERIFY_PROMPT  # noqa: E402
from broll_intel.roles import interpret  # noqa: E402
from broll_intel.schema import VerificationResult  # noqa: E402
from broll_intel.selection import apply_verification  # noqa: E402

PHASE16 = ROOT / "outputs" / "dr_shivin_phase16"
PHASE17 = ROOT / "outputs" / "dr_shivin_phase17"
OUT = ROOT / "outputs" / "dr_shivin_phase18"
LESSON = {"VIDEO_1": 55.26, "VIDEO_2": 41.44}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    OUT.mkdir(parents=True, exist_ok=True)
    generation = video_generation_status()
    (OUT / "generation_report.json").write_text(json.dumps(generation, indent=2), encoding="utf-8")
    (OUT / "provider_status.json").write_text(
        json.dumps(
            {
                "commons": "reused Phase 16 downloads; no new scrape",
                "pexels": "PEXELS_API_KEY is not set",
                "gemini_text": "gemini-3.8-flash, fallback gemini-3.5-flash on 503/504",
                "video_generation": generation,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    catalogue = {
        row.get("page_url"): row
        for row in json.loads((PHASE16 / "asset_cache" / "candidates.json").read_text(encoding="utf-8"))
        if row.get("page_url")
    }
    plans = {}
    calls = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        rows = json.loads((PHASE17 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        plans[video_id] = _resume(video_id, rows, calls)
        (OUT / f"editorial_plan_{video_id}.json").write_text(
            json.dumps(plans[video_id], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(video_id, "verified", sum(1 for row in plans[video_id] if row.get("retrieval_status") == "verified"), flush=True)
    (OUT / "verification_report.json").write_text(json.dumps({"calls": calls}, indent=2), encoding="utf-8")
    _reports(plans)
    if "--no-render" in args:
        return 0
    return _render(plans, catalogue)


def _resume(video_id: str, rows: list[dict], calls: list[dict]) -> list[dict]:
    rejected_pages = {
        (row.get("verification_evidence") or {}).get("page_url")
        for row in rows
        if row.get("retrieval_status") == "rejected"
    }
    finished = []
    for row in rows:
        page = (row.get("verification_evidence") or {}).get("page_url") or row.get("selected_asset_id")
        if row.get("retrieval_status") == "verifier_failed" and page in rejected_pages:
            row = dict(row)
            row["retrieval_status"] = "rejected"
            row["schedule"] = "teacher"
            row["decision_reason"] = "This source was already rejected for another sentence in the lesson."
            row["selected_asset_id"] = ""
            calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "status": "cached_reject"})
            finished.append(row)
            continue
        if row.get("retrieval_status") != "verifier_failed":
            finished.append(row)
            continue
        frames = [Path(path) for path in ((row.get("verification_evidence") or {}).get("frames") or []) if Path(path).is_file()]
        if not frames:
            row = dict(row)
            row["retrieval_status"] = "API_UNAVAILABLE"
            row["schedule"] = "review"
            finished.append(row)
            continue
        print("retry", video_id, row.get("transcript_segment_id"), row.get("primary_subject"), flush=True)
        try:
            result = generate_structured(_prompt(row), VerificationResult, images=frames)
            if not isinstance(result, VerificationResult):
                result = VerificationResult.model_validate(result)
            from broll_intel.schema import EditorialDecision

            decision = EditorialDecision.model_validate(row)
            updated = apply_verification(decision, result)
            calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "status": updated.retrieval_status})
            finished.append(updated.model_dump())
        except GeminiError as error:
            row = dict(row)
            row["retrieval_status"] = "API_UNAVAILABLE"
            row["schedule"] = "review"
            row["decision_reason"] = str(error)
            calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "status": "API_UNAVAILABLE", "detail": str(error)})
            finished.append(row)
    return finished


def _prompt(row: dict) -> str:
    grounded = interpret(row.get("exact_transcript") or "", float(row.get("speech_start") or 0), float(row.get("speech_end") or 0))
    return VERIFY_PROMPT.format(
        transcript=row.get("exact_transcript") or "",
        brief=grounded["visual_brief"],
        objects=", ".join(grounded["required_objects"]),
        actions=", ".join(row.get("required_actions") or []),
        forbidden=", ".join(grounded["forbidden_visuals"]),
        negation=grounded["negation_kind"],
        overlay=grounded["overlay_type"] or "none",
    )


def _reports(plans: dict) -> None:
    flat = [row for rows in plans.values() for row in rows]
    (OUT / "retrieval_report.json").write_text(
        json.dumps(
            {
                "reused_phase16_pool": True,
                "new_downloads": 0,
                "verified": sum(1 for row in flat if row.get("retrieval_status") == "verified"),
                "rejected": sum(1 for row in flat if row.get("retrieval_status") == "rejected"),
                "api_unavailable": sum(1 for row in flat if row.get("retrieval_status") == "API_UNAVAILABLE"),
                "unfilled": sum(1 for row in flat if row.get("retrieval_status") == "unfilled"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _render(plans: dict, catalogue: dict) -> int:
    accepted = []
    for rows in plans.values():
        for decision in rows:
            if decision.get("retrieval_status") != "verified":
                continue
            source = catalogue.get(decision.get("selected_asset_id")) or catalogue.get(
                (decision.get("verification_evidence") or {}).get("page_url")
            )
            if not source:
                continue
            copied = dict(source)
            if decision.get("overlay_type") == "negation_cross":
                copied["graphic"] = "negation_cross"
            accepted.append(copied)
    _bind()
    phase5.CACHE.mkdir(parents=True, exist_ok=True)
    observations = PHASE16 / "visual_observations.json"
    if observations.is_file():
        shutil.copyfile(observations, OUT / "visual_observations.json")
    rows = accepted + phase5._music_record()
    (phase5.CACHE / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [row["page_url"] for row in rows if row.get("page_url")]
    (OUT / "accepted.json").write_text(json.dumps({"page_urls": pages}, indent=2), encoding="utf-8")
    phase5.MATCH_REFERENCE_LOOK = True
    kind = "verified_broll" if accepted else "renderer_integration_no_broll"
    status = phase8.render_lessons()
    measured = _measure() if status == 0 else []
    (OUT / "render_report.json").write_text(
        json.dumps({"kind": kind, "accepted_segments": len(accepted), "renderer_status": status, "measured": measured}, indent=2),
        encoding="utf-8",
    )
    (OUT / "coverage_report.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
    return status


def _bind() -> None:
    phase8.OUT = OUT
    phase8.SUFFIX = "phase18"
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase18"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.TITLE_OVERRIDES = phase5.TITLE_OVERRIDES


def _measure() -> list[dict]:
    from analysis.uniqueness import hamming
    from assets.visual_rank import average_hash, load_rgb

    notes = []
    for video_id, lesson in LESSON.items():
        plan_path = OUT / video_id / "editing_plan.json"
        final = OUT / video_id / f"{video_id}_phase18.mp4"
        if not plan_path.is_file() or not final.is_file():
            notes.append({"video_id": video_id, "rendered_broll_seconds": 0, "reason": "render missing"})
            continue
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        inserts = [item for item in plan["decisions"] if item.get("action") == "replace_with_broll"]
        teacher = next(item for item in plan["decisions"] if item.get("action") == "show_teacher")
        teacher_frame = OUT / video_id / "measure_teacher.jpg"
        _frame(final, float(teacher["start"]) + 0.4, teacher_frame)
        teacher_hash = average_hash(load_rgb(teacher_frame))
        visible = []
        for item in inserts:
            shot = OUT / video_id / f"measure_{item['id']}.jpg"
            midpoint = (float(item["start"]) + float(item["end"])) / 2
            _frame(final, midpoint, shot)
            if hamming(teacher_hash, average_hash(load_rgb(shot))) >= 6:
                visible.append((float(item["start"]), float(item["end"])))
        from broll_intel.coverage import union_seconds

        seconds = union_seconds(visible, lesson)
        notes.append(
            {
                "video_id": video_id,
                "lesson_seconds": lesson,
                "rendered_broll_seconds": seconds,
                "rendered_broll_coverage": round(seconds / lesson, 4) if lesson else 0,
                "verified_broll_seconds": seconds,
                "inserts_visible": len(visible),
                "inserts_planned": len(inserts),
                "target_met": seconds / lesson >= 0.5 if lesson else False,
            }
        )
        print(video_id, "rendered", notes[-1]["rendered_broll_coverage"], flush=True)
    return notes


def _frame(video: Path, second: float, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-ss", f"{second:.3f}", "-frames:v", "1", "-update", "1", str(dest)],
        check=False,
        capture_output=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
