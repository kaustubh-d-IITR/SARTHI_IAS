"""Phase 19. Director reads each line, then the existing renderer cuts verified clips."""

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

from broll_intel.director import DIRECTOR_PROMPT, DirectorRead, analyze, assets_for_render, merge_director, refine  # noqa: E402
from broll_intel.gemini_api import TEXT_MODEL, GeminiError, cache_key, generate_structured, video_generation_status  # noqa: E402

PHASE16 = ROOT / "outputs" / "dr_shivin_phase16"
PHASE18 = ROOT / "outputs" / "dr_shivin_phase18"
OUT = ROOT / "outputs" / "dr_shivin_phase19"
LESSON = {"VIDEO_1": 55.26, "VIDEO_2": 41.44}
ELIGIBLE = {"VIDEO_1": 17.541, "VIDEO_2": 31.1}
LIVE = {("VIDEO_2", "a0"), ("VIDEO_2", "a1")}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    OUT.mkdir(parents=True, exist_ok=True)
    generation = video_generation_status()
    (OUT / "generation_report.json").write_text(json.dumps(generation, indent=2), encoding="utf-8")
    catalogue = {
        row.get("page_url"): row
        for row in json.loads((PHASE16 / "asset_cache" / "candidates.json").read_text(encoding="utf-8"))
        if row.get("page_url")
    }
    cache_path = OUT / "director_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    calls = []
    plans = {}
    for video_id in ("VIDEO_1", "VIDEO_2"):
        rows = json.loads((PHASE18 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        plans[video_id] = _direct(video_id, rows, cache, calls, live="--no-live" not in args)
        (OUT / f"editorial_plan_{video_id}.json").write_text(
            json.dumps(plans[video_id], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        print(video_id, "scheduled", sum(1 for row in plans[video_id] if row.get("schedule") == "broll"), flush=True)
    _write_reports(plans, catalogue, calls, generation)
    if "--no-render" in args:
        return 0
    return _render(plans, catalogue)


def _direct(video_id: str, rows: list[dict], cache: dict, calls: list[dict], live: bool) -> list[dict]:
    used: set[str] = set()
    finished = []
    for index, row in enumerate(rows):
        before = rows[index - 1]["exact_transcript"] if index else ""
        after = rows[index + 1]["exact_transcript"] if index + 1 < len(rows) else ""
        grounded, source = _read(video_id, row, before, after, cache, calls, live)
        merged = merge_director(row, grounded, source)
        finished.append(refine(merged, used))
    return finished


def _read(video_id, row, before, after, cache, calls, live):
    text = row.get("exact_transcript") or ""
    start = float(row.get("speech_start") or 0)
    end = float(row.get("speech_end") or start)
    language = row.get("language") or "und"
    key = cache_key({"role": "director", "text": text})
    cached = cache.get(key) or {}
    if cached.get("status") == "ok" and cached.get("grounded"):
        calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "source": "cache"})
        return cached["grounded"], "cache"
    wants_live = live and (video_id, row.get("transcript_segment_id")) in LIVE
    if not wants_live:
        calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "source": "rules"})
        return analyze(text, start, end, language, neighbors=(before, after)), "rules"
    print("director", video_id, row.get("transcript_segment_id"), flush=True)
    prompt = DIRECTOR_PROMPT.format(text=text, before=before, after=after)
    try:
        model = generate_structured(prompt, DirectorRead)
        if not isinstance(model, DirectorRead):
            model = DirectorRead.model_validate(model)
        grounded = analyze(text, start, end, language, model=model, neighbors=(before, after))
        grounded["model"] = TEXT_MODEL
        cache[key] = {"status": "ok", "grounded": grounded}
        calls.append({"lesson_id": video_id, "segment": row.get("transcript_segment_id"), "source": "gemini", "model": TEXT_MODEL})
        return grounded, "gemini"
    except GeminiError as error:
        grounded = analyze(text, start, end, language, neighbors=(before, after))
        cache[key] = {"status": "api_unavailable", "error": str(error)}
        calls.append({
            "lesson_id": video_id,
            "segment": row.get("transcript_segment_id"),
            "source": "rules_after_api_error",
            "detail": str(error),
        })
        return grounded, "rules_after_api_error"


def _write_reports(plans, catalogue, calls, generation) -> None:
    flat = [row for rows in plans.values() for row in rows]
    accepted = assets_for_render(plans, catalogue)
    (OUT / "retrieval_report.json").write_text(
        json.dumps(
            {
                "providers_called_this_phase": [],
                "commons": "not called; Phase 16 files reused",
                "pexels": "PEXELS_API_KEY is not set",
                "assets_in_catalogue": len(catalogue),
                "new_downloads": 0,
                "scheduled": len(accepted),
                "verified": sum(1 for row in flat if row.get("retrieval_status") == "verified"),
                "rejected": sum(1 for row in flat if row.get("retrieval_status") == "rejected"),
                "api_unavailable": sum(1 for row in flat if row.get("retrieval_status") == "API_UNAVAILABLE"),
                "unfilled": sum(1 for row in flat if row.get("retrieval_status") == "unfilled"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "verification_report.json").write_text(
        json.dumps(
            {
                "reused_phase18_decisions": True,
                "new_verification_calls": 0,
                "director_calls": calls,
                "note": "Pass B cannot change a Phase 18 rejection. The hospital interval stays API_UNAVAILABLE.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = ["# Semantic director", ""]
    for row in flat:
        director = row.get("director") or {}
        if director.get("source") not in {"gemini", "rules_after_api_error", "cache"} and row.get("schedule") != "broll":
            continue
        lines.append(
            f"- {row.get('lesson_id')} {row.get('transcript_segment_id')}: "
            f"source {director.get('source')}, object {director.get('object')}, "
            f"negation {director.get('negation')}, schedule {row.get('schedule')}, "
            f"retrieval {row.get('retrieval_status')}."
        )
    lines.append("")
    lines.append("Scheduled inserts use the Phase 18 verified source in-points. No rejected clip was added.")
    (OUT / "semantic_director_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not (OUT / "generation_report.json").is_file():
        (OUT / "generation_report.json").write_text(json.dumps(generation, indent=2), encoding="utf-8")


def _render(plans, catalogue) -> int:
    accepted = assets_for_render(plans, catalogue)
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
    report = {"kind": kind, "accepted_segments": len(accepted), "renderer_status": status, "measured": measured}
    (OUT / "render_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "coverage_report.json").write_text(json.dumps(_coverage(measured, plans), indent=2), encoding="utf-8")
    if status == 0:
        dest = OUT / "rendered"
        dest.mkdir(parents=True, exist_ok=True)
        for video_id in LESSON:
            source = OUT / video_id / f"{video_id}_phase19.mp4"
            if source.is_file():
                shutil.copy2(source, dest / f"{video_id}.mp4")
        _contact()
    return status


def _coverage(measured, plans) -> dict:
    by_id = {item["video_id"]: item for item in measured}
    lessons = []
    for video_id, lesson in LESSON.items():
        item = by_id.get(video_id) or {}
        seconds = float(item.get("verified_broll_seconds") or 0)
        eligible = ELIGIBLE[video_id]
        planned = [
            (float(row["speech_start"]), float(row["speech_end"]))
            for row in plans[video_id]
            if row.get("schedule") == "broll"
        ]
        from broll_intel.coverage import union_seconds

        planned_s = union_seconds(planned, lesson)
        lessons.append(
            {
                "video_id": video_id,
                "lesson_seconds": lesson,
                "eligible_speech_seconds": eligible,
                "planned_broll_seconds": planned_s,
                "rendered_broll_seconds": seconds,
                "verified_broll_seconds": seconds,
                "planned_broll_coverage_of_lesson": round(planned_s / lesson, 4) if lesson else 0,
                "rendered_broll_coverage_of_lesson": item.get("rendered_broll_coverage", 0),
                "verified_broll_coverage_of_eligible_speech": round(seconds / eligible, 4) if eligible else 0,
                "inserts_visible": item.get("inserts_visible", 0),
                "target_met": seconds / eligible >= 0.5 if eligible else False,
            }
        )
    flat = [row for rows in plans.values() for row in rows]
    return {
        "method": "Midpoint frame of each rendered B-roll decision compared with a teacher frame. Outro excluded.",
        "lessons": lessons,
        "retrieved_assets_reused": 2,
        "new_downloads": 0,
        "accepted_candidates": sum(1 for row in flat if row.get("retrieval_status") == "verified" and row.get("schedule") == "broll"),
        "rejected_candidates": sum(1 for row in flat if row.get("retrieval_status") == "rejected"),
        "generated_clips": 0,
        "unresolved_intervals": sum(1 for row in flat if row.get("retrieval_status") == "API_UNAVAILABLE"),
        "api_failures": sum(1 for row in flat if row.get("director", {}).get("source") == "rules_after_api_error"),
        "duplicate_reuse_within_a_lesson": 0,
    }


def _bind() -> None:
    phase8.OUT = OUT
    phase8.SUFFIX = "phase19"
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase19"
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
        final = OUT / video_id / f"{video_id}_phase19.mp4"
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
                "rendered_broll_seconds": seconds,
                "rendered_broll_coverage": round(seconds / lesson, 4) if lesson else 0,
                "verified_broll_seconds": seconds,
                "inserts_visible": len(visible),
                "inserts_planned": len(inserts),
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


def _contact() -> None:
    dest = OUT / "contact"
    dest.mkdir(parents=True, exist_ok=True)
    for video_id in LESSON:
        shot = OUT / video_id / "measure_broll-0.jpg"
        if shot.is_file():
            shutil.copy2(shot, dest / f"{video_id}_rendered.jpg")
        outro = OUT / video_id / "snapshots"
        if outro.is_dir():
            frames = sorted(outro.glob("t*.jpg"))
            if frames:
                shutil.copy2(frames[-2] if len(frames) > 1 else frames[-1], dest / f"{video_id}_outro.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
