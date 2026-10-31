"""Phase 20. Reference cutaways choose the phrases. Commons is searched again. Only a verified span is rendered."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import run_phase5 as phase5
import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from analysis.uniqueness import file_sha256  # noqa: E402
from assets.footage import _download, _score_candidate  # noqa: E402
from assets.video_commons import search_videos  # noqa: E402
from broll_intel.director import assets_for_render, merge_director, refine  # noqa: E402
from broll_intel.gemini_api import GeminiError, generate_structured, video_generation_status  # noqa: E402
from broll_intel.pipeline import VERIFY_PROMPT  # noqa: E402
from broll_intel.recovery import fill_selected  # noqa: E402
from broll_intel.retain import retain_verified  # noqa: E402
from broll_intel.reference_edits import choose_cutaway, examples_from_dataset, holdout_counts  # noqa: E402
from broll_intel.schema import VerificationResult  # noqa: E402
from config.env import load_project_env  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402

PHASE7 = ROOT / "outputs" / "dr_shivin_phase7"
PHASE16 = ROOT / "outputs" / "dr_shivin_phase16"
PHASE18 = ROOT / "outputs" / "dr_shivin_phase18"
OUT = ROOT / "outputs" / "dr_shivin_phase20"
LESSON = {"VIDEO_1": 55.26, "VIDEO_2": 41.44}
ELIGIBLE = {"VIDEO_1": 17.541, "VIDEO_2": 31.1}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if "--render-only" in args:
        plans = {
            video_id: json.loads((OUT / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
            for video_id in LESSON
        }
        return _render(plans, _catalogue())
    dataset = load_dataset()
    examples = examples_from_dataset(dataset)
    holdout = holdout_counts(dataset)
    (OUT / "reference_holdout.json").write_text(json.dumps(holdout, indent=2), encoding="utf-8")
    print("examples", len(examples), "holdout precision", holdout["precision"], "recall", holdout["recall"], flush=True)
    catalogue = _catalogue()
    prior = _prior_accepts(catalogue)
    phase18_rows = _phase18_by_segment()
    blocked_pages = _rejected_pages()
    load_project_env()
    pexels_on = bool((os.environ.get("PEXELS_API_KEY") or "").strip())
    generation = video_generation_status() if "--no-live" not in args else {"available": False, "reason": "skipped"}
    (OUT / "generation_report.json").write_text(json.dumps(generation, indent=2), encoding="utf-8")
    ranker_box: dict = {"ranker": None}
    plans = {}
    retrieval = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = json.loads((PHASE7 / video_id / "transcript.json").read_text(encoding="utf-8"))
        observations = json.loads((PHASE16 / "visual_observations.json").read_text(encoding="utf-8"))
        blocked_terms = list((observations.get(video_id) or {}).get("terms") or [])
        rows = []
        used: set[str] = set()
        for segment in transcript.get("segments") or []:
            text = str(segment.get("original_text") or segment.get("text") or "")
            decision = choose_cutaway(text, float(segment.get("start") or 0), float(segment.get("end") or 0), examples)
            decision["lesson_id"] = video_id
            decision["transcript_segment_id"] = segment.get("id")
            decision["blocked_terms"] = blocked_terms
            if decision["select_cutaway"] and "--no-search" not in args:
                outcome = fill_selected(
                    decision,
                    search=_search_live,
                    download=_download_one,
                    rank=lambda path, prompt, take, cache: _rank(path, prompt, take, cache, ranker_box),
                    verify=_verify,
                    prior=prior,
                    blocked_pages=blocked_pages,
                    cache=OUT / "asset_cache",
                    used_pages=used,
                )
            else:
                outcome = {
                    "search": [],
                    "attempts": [],
                    "retrieval_status": "not_searched",
                    "selected_asset_id": "",
                    "selected_source_in": None,
                    "selected_source_out": None,
                    "verification_evidence": {},
                    "catalogue_row": None,
                    "reason": "",
                }
            saved = outcome.get("catalogue_row")
            if saved and saved.get("page_url"):
                if not saved.get("sha256"):
                    saved["sha256"] = file_sha256(saved.get("local_path"))
                catalogue[saved["page_url"]] = saved
            prior_row = phase18_rows.get((video_id, segment.get("id")))
            rows.append(_to_plan_row(decision, outcome, used, prior_row))
            retrieval.append(
                {
                    "lesson_id": video_id,
                    "segment": segment.get("id"),
                    "selected": decision["select_cutaway"],
                    "object": decision["feasibility"]["object"],
                    "hits": len(outcome["search"]),
                    "attempts": outcome["attempts"],
                    "retrieval_status": rows[-1].get("retrieval_status"),
                    "schedule": rows[-1].get("schedule"),
                    "reason": rows[-1].get("decision_reason"),
                }
            )
            print(
                video_id,
                segment.get("id"),
                rows[-1].get("schedule"),
                decision["feasibility"]["object"],
                rows[-1].get("retrieval_status"),
                flush=True,
            )
        plans[video_id] = rows
        (OUT / f"editorial_plan_{video_id}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "retrieval_report.json").write_text(
        json.dumps({"pexels_configured": pexels_on, "queries": retrieval}, indent=2),
        encoding="utf-8",
    )
    _summary(plans, holdout, examples, pexels_on)
    if "--no-render" in args:
        return 0
    return _render(plans, catalogue)


def _search_live(query: str) -> list[dict]:
    import time

    time.sleep(0.8)
    return search_videos(query, limit=4)


def _download_one(hit: dict, cache: Path):
    import time

    return _download(hit, cache, [], time.sleep)


def _rank(path: str, prompt: str, take: float, cache: Path, box: dict):
    if box.get("ranker") is None:
        from assets.visual_rank import ClipRanker

        box["ranker"] = ClipRanker()
    return _score_candidate({"local_path": path}, take, prompt, box["ranker"], cache)


def _verify(frames: list, decision: dict) -> VerificationResult:
    feasibility = decision["feasibility"]
    prompt = VERIFY_PROMPT.format(
        transcript=decision["spoken_text"],
        brief=feasibility.get("visual_brief") or "",
        objects=", ".join(feasibility.get("required_visual_elements") or []),
        actions=feasibility.get("action") or "",
        forbidden=", ".join(feasibility.get("forbidden_visual_elements") or []),
        negation=str(bool(feasibility.get("negation"))),
        overlay=feasibility.get("overlay_instructions") or "none",
    )
    try:
        result = generate_structured(prompt, VerificationResult, images=[Path(frame) for frame in frames])
    except GeminiError:
        raise
    if not isinstance(result, VerificationResult):
        result = VerificationResult.model_validate(result)
    return result


def _phase18_by_segment() -> dict[tuple[str, str], dict]:
    found = {}
    for video_id in LESSON:
        rows = json.loads((PHASE18 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        for row in rows:
            found[(video_id, str(row.get("transcript_segment_id") or ""))] = row
    return found


def _to_plan_row(decision: dict, outcome: dict, used: set[str], prior_row: dict | None = None) -> dict:
    grounded = {
        "object": decision["feasibility"]["object"],
        "action": decision["feasibility"]["action"],
        "negation": decision["feasibility"]["negation"],
        "visual_type": decision["feasibility"]["visual_type"],
        "visual_brief": decision["feasibility"]["visual_brief"],
        "required_visual_elements": decision["feasibility"]["required_visual_elements"],
        "forbidden_visual_elements": decision["feasibility"]["forbidden_visual_elements"],
        "search_queries": decision["feasibility"]["search_queries"],
        "overlay_instructions": decision["feasibility"]["overlay_instructions"],
    }
    base = {
        "lesson_id": decision["lesson_id"],
        "transcript_segment_id": decision["transcript_segment_id"],
        "speech_start": decision["speech_start"],
        "speech_end": decision["speech_end"],
        "exact_transcript": decision["spoken_text"],
        "retrieval_status": "not_searched",
        "selected_asset_id": "",
        "selected_source_in": None,
        "selected_source_out": None,
        "verification_evidence": {"references": decision["editorial"]["examples"]},
        "decision_reason": decision["decision_reason"],
        "review_required": decision["uncertainty"] == "high",
    }
    merged = merge_director(base, _ground_view(grounded), "reference")
    merged["search_hits"] = [_public_hit(hit) for hit in outcome.get("search") or []]
    merged["attempts"] = outcome.get("attempts") or []
    if not decision["select_cutaway"]:
        merged["retrieval_status"] = "teacher"
        merged["schedule"] = "teacher"
        return refine(retain_verified(merged, prior_row), used)
    status = outcome.get("retrieval_status") or "unfilled"
    if status != "verified":
        merged["retrieval_status"] = status
        merged["decision_reason"] = (decision["decision_reason"] + " " + (outcome.get("reason") or "")).strip()
        return refine(retain_verified(merged, prior_row), used)
    merged["retrieval_status"] = "verified"
    merged["selected_asset_id"] = outcome.get("selected_asset_id") or ""
    merged["selected_source_in"] = outcome.get("selected_source_in")
    merged["selected_source_out"] = outcome.get("selected_source_out")
    merged["verification_evidence"] = outcome.get("verification_evidence") or {}
    merged["decision_reason"] = outcome.get("reason") or decision["decision_reason"]
    return refine(retain_verified(merged, prior_row), used)


def _public_hit(hit: dict) -> dict:
    return {
        "query": hit.get("query"),
        "page_url": hit.get("page_url"),
        "title": hit.get("title"),
        "license": hit.get("license"),
        "creator": hit.get("creator"),
        "metadata_ok": hit.get("metadata_ok"),
        "license_ok": hit.get("license_ok"),
        "reject_reason": hit.get("reject_reason"),
        "error": hit.get("error"),
    }


def _ground_view(grounded: dict) -> dict:
    return {
        "object": grounded["object"],
        "action": grounded["action"],
        "negation": grounded["negation"],
        "visual_type": grounded["visual_type"],
        "visual_brief": grounded["visual_brief"],
        "required_visual_elements": grounded["required_visual_elements"],
        "forbidden_visual_elements": grounded["forbidden_visual_elements"],
        "search_queries": grounded["search_queries"],
        "overlay_instructions": grounded["overlay_instructions"],
    }


def _catalogue() -> dict:
    path = PHASE16 / "asset_cache" / "candidates.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["page_url"]: row for row in rows if row.get("page_url")}


def _prior_accepts(catalogue: dict) -> dict:
    found = {}
    for video_id in LESSON:
        rows = json.loads((PHASE18 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        for row in rows:
            if row.get("retrieval_status") != "verified":
                continue
            page = row.get("selected_asset_id") or ""
            evidence = row.get("verification_evidence") or {}
            gemini = evidence.get("gemini") or {}
            if gemini.get("decision") != "ACCEPT" or not page:
                continue
            local = (catalogue.get(page) or {}).get("local_path") or evidence.get("local_path") or ""
            found[page] = {
                "decision": "ACCEPT",
                "object": (row.get("director") or {}).get("object") or row.get("primary_subject"),
                "source_in": row.get("selected_source_in"),
                "source_out": row.get("selected_source_out"),
                "local_path": local,
                "license": evidence.get("license"),
                "sha256": (catalogue.get(page) or {}).get("sha256") or "",
            }
    return found


def _rejected_pages() -> set[str]:
    pages: set[str] = set()
    folders = [PHASE18]
    if (OUT / "editorial_plan_VIDEO_1.json").is_file():
        folders.append(OUT)
    for folder in folders:
        for video_id in LESSON:
            path = folder / f"editorial_plan_{video_id}.json"
            if not path.is_file():
                continue
            rows = json.loads(path.read_text(encoding="utf-8"))
            for row in rows:
                if row.get("retrieval_status") == "rejected":
                    page = (row.get("verification_evidence") or {}).get("page_url") or ""
                    if page:
                        pages.add(page)
                for attempt in row.get("attempts") or []:
                    if attempt.get("status") == "REJECT" and attempt.get("page_url"):
                        pages.add(attempt["page_url"])
    return pages


def _summary(plans: dict, holdout: dict, examples: list[dict], pexels_on: bool) -> None:
    flat = [row for rows in plans.values() for row in rows]
    attempts = [item for row in flat for item in (row.get("attempts") or [])]
    lines = [
        "# Phase 20 editorial summary",
        "",
        f"Reference examples: {len(examples)} from trusted Shorts. Partials SHORT_30, SHORT_32, and SHORT_37 are excluded.",
        f"Held-out cutaway prediction: precision {holdout['precision']}, recall {holdout['recall']}.",
        holdout["note"],
        "",
        f"Phrases evaluated: {len(flat)}.",
        f"Scheduled cutaways: {sum(1 for row in flat if row.get('schedule') == 'broll')}.",
        f"Left on the teacher: {sum(1 for row in flat if row.get('schedule') != 'broll')}.",
        f"Pexels configured: {pexels_on}. Commons is the retrieval source.",
        "",
    ]
    for row in flat:
        if row.get("schedule") == "broll" or row.get("retrieval_status") not in {"teacher", "not_searched"}:
            lines.append(
                f"- {row['lesson_id']} {row['transcript_segment_id']}: {row.get('schedule')} "
                f"{row.get('retrieval_status')} object {row.get('primary_subject')}. {row.get('decision_reason')}"
            )
    (OUT / "editorial_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "editorial_report.json").write_text(
        json.dumps(
            {
                "phrases_evaluated": len(flat),
                "selected_for_cutaway": sum(1 for row in flat if row.get("retrieval_status") != "teacher" or row.get("schedule") == "broll"),
                "scheduled": sum(1 for row in flat if row.get("schedule") == "broll"),
                "left_on_teacher": sum(1 for row in flat if row.get("schedule") != "broll"),
                "verified": sum(1 for row in flat if row.get("retrieval_status") == "verified" and row.get("schedule") == "broll"),
                "rejected": sum(1 for row in flat if row.get("retrieval_status") == "rejected"),
                "review": sum(1 for row in flat if row.get("retrieval_status") == "review"),
                "api_unavailable": sum(1 for row in flat if row.get("retrieval_status") == "API_UNAVAILABLE"),
                "unfilled_after_search": sum(1 for row in flat if row.get("retrieval_status") == "unfilled"),
                "attempt_counts": _count(attempts),
                "pexels_configured": pexels_on,
                "holdout": holdout,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _count(attempts: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in attempts:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _render(plans: dict, catalogue: dict) -> int:
    accepted = assets_for_render(plans, catalogue)
    phase8.OUT = OUT
    phase8.SUFFIX = "phase20"
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase20"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.TITLE_OVERRIDES = phase5.TITLE_OVERRIDES
    phase5.CACHE.mkdir(parents=True, exist_ok=True)
    observations = PHASE16 / "visual_observations.json"
    if observations.is_file():
        shutil.copyfile(observations, OUT / "visual_observations.json")
    rows = accepted + phase5._music_record()
    (phase5.CACHE / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (OUT / "accepted.json").write_text(json.dumps({"page_urls": [row.get("page_url") for row in rows if row.get("page_url")]}, indent=2), encoding="utf-8")
    phase5.MATCH_REFERENCE_LOOK = True
    kind = "verified_broll" if accepted else "renderer_integration_no_broll"
    status = phase8.render_lessons()
    (OUT / "render_report.json").write_text(
        json.dumps({"kind": kind, "accepted_segments": len(accepted), "renderer_status": status}, indent=2),
        encoding="utf-8",
    )
    if status == 0:
        dest = OUT / "rendered"
        dest.mkdir(parents=True, exist_ok=True)
        for video_id in LESSON:
            source = OUT / video_id / f"{video_id}_phase20.mp4"
            if source.is_file():
                shutil.copy2(source, dest / f"{video_id}.mp4")
        measured = _measure()
        (OUT / "rendered_coverage.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
    return status


def _measure() -> list[dict]:
    """Pixel check. A planned insert counts only when the midpoint frame differs from the teacher."""
    from analysis.uniqueness import hamming
    from assets.visual_rank import average_hash, load_rgb
    from broll_intel.coverage import union_seconds
    from render.outro import tool

    notes = []
    for video_id, lesson in LESSON.items():
        plan_path = OUT / video_id / "editing_plan.json"
        final = OUT / video_id / f"{video_id}_phase20.mp4"
        if not plan_path.is_file() or not final.is_file():
            notes.append({"video_id": video_id, "verified_broll_seconds": 0, "reason": "render missing"})
            continue
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        inserts = [item for item in plan["decisions"] if item.get("action") == "replace_with_broll"]
        teacher = next(item for item in plan["decisions"] if item.get("action") == "show_teacher")
        teacher_frame = OUT / video_id / "measure_teacher.jpg"
        _frame(final, float(teacher["start"]) + 0.4, teacher_frame, tool("ffmpeg"))
        teacher_hash = average_hash(load_rgb(teacher_frame))
        visible = []
        for item in inserts:
            shot = OUT / video_id / f"measure_{item['id']}.jpg"
            midpoint = (float(item["start"]) + float(item["end"])) / 2
            _frame(final, midpoint, shot, tool("ffmpeg"))
            if hamming(teacher_hash, average_hash(load_rgb(shot))) >= 6:
                visible.append((float(item["start"]), float(item["end"])))
        seconds = union_seconds(visible, lesson)
        notes.append(
            {
                "video_id": video_id,
                "method": "midpoint frame versus a teacher frame; outro excluded; overlapping intervals counted once",
                "rendered_broll_seconds": seconds,
                "verified_broll_seconds": seconds,
                "inserts_planned": len(inserts),
                "inserts_visible": len(visible),
                "rendered_broll_coverage_of_lesson": round(seconds / lesson, 4) if lesson else 0,
            }
        )
        print(video_id, "pixel broll", seconds, flush=True)
    return notes


def _frame(video: Path, second: float, dest: Path, ffmpeg: str) -> None:
    import subprocess

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-ss", f"{second:.3f}", "-frames:v", "1", "-update", "1", str(dest)],
        check=False,
        capture_output=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
