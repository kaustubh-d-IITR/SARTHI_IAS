"""Phase 12. Dotted background, ranked B-roll, and a local clip catalogue.

Does not call ElevenLabs and does not rewrite Phase 11 outputs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import broll_opportunities  # noqa: E402
from analysis.subjects import opportunity_records  # noqa: E402
from analysis.uniqueness import asset_identity, enforce_unique, file_sha256  # noqa: E402
from assets.library import Catalogue  # noqa: E402
from assets.providers import openverse_status, pexels_status  # noqa: E402
from assets.queries import description_block, retrieval_plan  # noqa: E402
from assets.video_commons import _fill_public_domain_url, accept_video_candidate, download_video, search_videos  # noqa: E402
from assets.visual_rank import (  # noqa: E402
    ClipRanker,
    average_hash,
    black_fraction,
    content_box,
    crop_video,
    framing_decision,
    load_rgb,
    sample_frames,
)
from render.halftone import pattern_is_animated  # noqa: E402

PHASE11 = ROOT / "outputs" / "dr_shivin_phase11"
CATALOGUE = ROOT / "asset_library" / "catalogue.sqlite"
MINIMUM_SCORE = 0.20


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--render-only" in args:
        _bind_output()
        return phase8.render_lessons()
    summary = prepare()
    print(json.dumps(summary))
    if "--render" in args:
        return phase8.render_lessons()
    return 0


def _bind_output() -> None:
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase12"
    phase8.SUFFIX = "phase12"
    phase8.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }


def prepare(ranker: ClipRanker | None = None) -> dict:
    _bind_output()
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    cache = phase8.OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    _copy_cache(PHASE11 / "asset_cache", cache)
    for name in ("visual_observations.json", "accepted.json"):
        source = PHASE11 / name
        dest = phase8.OUT / name
        if source.is_file() and not dest.exists():
            shutil.copyfile(source, dest)
    catalogue = Catalogue(CATALOGUE)
    _index_existing(cache, catalogue)
    ranker = ranker or ClipRanker()
    rows: list[dict] = []
    log: list[dict] = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = phase8._load_transcript(video_id)
        records = opportunity_records(transcript)
        (phase8.OUT / video_id).mkdir(parents=True, exist_ok=True)
        (phase8.OUT / video_id / "subjects.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        opportunities = [item for item in broll_opportunities(transcript) if item["action"] == "search"]
        opportunities.sort(key=lambda item: float(item["start"]))
        used_ids: set[str] = set()
        used_visual: list[str] = []
        for item in opportunities:
            print(f"search {video_id} {item['term']} {item['start']:.2f}", flush=True)
            chosen, notes = _fill_opportunity(item, video_id, cache, catalogue, ranker, used_ids, used_visual)
            log.append({"video_id": video_id, "term": item["term"], "start": item["start"], "end": item["end"], **notes})
            if chosen is None:
                rows.append(
                    {
                        "video_id": video_id,
                        "query": item["query"],
                        "kind": "video",
                        "start": item["start"],
                        "end": item["end"],
                        "rejected": notes.get("unfilled") or "no suitable clip",
                    }
                )
                continue
            identity = asset_identity(chosen)
            if identity:
                used_ids.add(identity)
            if chosen.get("visual_hash"):
                used_visual.append(chosen["visual_hash"])
            rows.append(chosen)
            catalogue.record_use(
                identity,
                "phase12",
                video_id,
                float(item["start"]),
                float(item["end"]),
            )
    import run_phase5 as phase5

    phase5.OUT = phase8.OUT
    phase5.CACHE = cache
    rows.extend(phase5._music_record())
    rows, rejected = enforce_unique(rows)
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = []
    for row in rows:
        if row.get("page_url") and not row.get("rejected") and row.get("local_path"):
            pages.append(row["page_url"])
    (phase8.OUT / "accepted.json").write_text(
        json.dumps({"page_urls": list(dict.fromkeys(pages))}, indent=2),
        encoding="utf-8",
    )
    summary = {
        "background": "static" if not pattern_is_animated() else "animated",
        "duplicates_rejected": rejected,
        "openverse": openverse_status(),
        "pexels": pexels_status(),
        "clip_loaded": ranker.loaded,
        "opportunities": log,
        "accepted": [
            {
                "video_id": row.get("video_id"),
                "query": row.get("query"),
                "page_url": row.get("page_url"),
                "relevance": row.get("relevance"),
                "window": row.get("source_window"),
            }
            for row in rows
            if row.get("kind") == "video" and not row.get("rejected") and row.get("source_window")
        ],
    }
    (phase8.OUT / "retrieval.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    catalogue.close()
    return summary


def _copy_cache(source: Path, dest: Path) -> None:
    if not source.is_dir():
        return
    for path in source.iterdir():
        target = dest / path.name
        if path.is_file() and not target.exists() and path.suffix.lower() != ".json":
            shutil.copyfile(path, target)


def _index_existing(cache: Path, catalogue: Catalogue) -> None:
    previous = PHASE11 / "asset_cache" / "candidates.json"
    if not previous.is_file():
        return
    for row in json.loads(previous.read_text(encoding="utf-8")):
        if row.get("rejected") or row.get("kind") == "audio" or not row.get("page_url"):
            continue
        local = cache / Path(row.get("local_path") or "").name
        if not local.is_file():
            continue
        copied = dict(row)
        copied["local_path"] = str(local)
        copied["sha256"] = copied.get("sha256") or file_sha256(str(local))
        copied["tags"] = [row.get("query") or ""]
        copied["provider"] = "wikimedia"
        catalogue.upsert(copied)


def _fill_opportunity(item, video_id, cache, catalogue, ranker, used_ids, used_visual):
    plan = retrieval_plan(item["query"], item.get("sentence") or "", item.get("term") or "")
    phrases = list(plan["phrases"])
    if plan["anchors"] and plan["anchors"][0] not in phrases:
        phrases.append(plan["anchors"][0])
    notes = {"phrases": phrases, "prompt": plan["prompt"], "seen": 0, "rejected": []}
    pool = []
    seen_pages = set()
    for saved in catalogue.search_tag(plan["anchors"][0]):
        page = saved.get("page_url") or ""
        local = saved.get("local_path") or ""
        if page in seen_pages or not local or not Path(local).is_file():
            continue
        if asset_identity(saved) in used_ids:
            continue
        seen_pages.add(page)
        saved = dict(saved)
        blob = f"{saved.get('title') or ''} {saved.get('description') or ''}".lower()
        if any(word in blob for word in plan["blocked_words"]):
            saved["_penalty"] = 0.06
        pool.append(saved)
    for phrase in phrases:
        if sum(1 for row in pool if row.get("download_url")) >= 4:
            break
        for offset in (0, 8):
            try:
                hits = search_videos(phrase, limit=6, offset=offset)
            except Exception as error:  # noqa: BLE001
                notes["rejected"].append({"phrase": phrase, "reason": type(error).__name__})
                time.sleep(2)
                continue
            time.sleep(1.1)
            for hit in hits:
                notes["seen"] += 1
                page = hit.get("page_url") or ""
                if page in seen_pages or asset_identity(hit) in used_ids:
                    notes["rejected"].append({"title": hit.get("title"), "reason": "already used or already queued"})
                    continue
                blob = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
                blocked = description_block(blob, plan)
                if blocked:
                    notes["rejected"].append({"title": hit.get("title"), "reason": blocked})
                    continue
                term = _license_term(blob, plan["anchors"])
                if not term:
                    notes["rejected"].append({"title": hit.get("title"), "reason": "description misses the subject"})
                    continue
                _fill_public_domain_url(hit)
                reason = accept_video_candidate(hit, term, speech=item.get("sentence") or "")
                if reason:
                    notes["rejected"].append({"title": hit.get("title"), "reason": reason})
                    continue
                seen_pages.add(page)
                local = _ensure_file(hit, cache)
                if local is None:
                    notes["rejected"].append({"title": hit.get("title"), "reason": "download failed"})
                    continue
                record = dict(hit)
                record["local_path"] = str(local)
                record["provider"] = "wikimedia"
                pool.append(record)
                if sum(1 for row in pool if row.get("download_url")) >= 4:
                    break
            if sum(1 for row in pool if row.get("download_url")) >= 4:
                break
    scored = []
    for record in pool:
        judged = _judge(record, plan, ranker, used_visual, cache)
        if judged.get("rejected"):
            notes["rejected"].append({"title": record.get("title"), "reason": judged["rejected"], "relevance": judged.get("relevance")})
            continue
        scored.append(judged)
    scored.sort(key=lambda row: float(row.get("relevance") or -1), reverse=True)
    if not scored or float(scored[0]["relevance"]) < MINIMUM_SCORE:
        notes["unfilled"] = "no clip cleared the picture check"
        return None, notes
    best = scored[0]
    best["video_id"] = video_id
    best["match_terms"] = [item["term"]]
    best["source_window"] = [item["start"], item["end"]]
    best["kind"] = "video"
    best["rejected"] = None
    best["tags"] = [item["query"]]
    best["subjects"] = [item["term"]]
    best["sha256"] = best.get("sha256") or file_sha256(best.get("local_path"))
    best["reason"] = (
        f"Ranked {best['relevance']:.3f} for “{plan['prompt']}”. "
        "A repeated or black-isolated clip was not used."
    )
    catalogue.upsert(best)
    notes["chosen"] = best.get("page_url")
    notes["relevance"] = best.get("relevance")
    return best, notes


def _license_term(haystack: str, anchors: list[str]) -> str | None:
    for word in anchors:
        if len(word) >= 4 and re.search(rf"\b{re.escape(word)}(?!-)\b", haystack):
            return word
    return None


def _ensure_file(hit: dict, cache: Path) -> Path | None:
    suffix = Path(urllib.parse.urlparse(hit.get("download_url") or "").path).suffix.lower() or ".mp4"
    dest = cache / f"{phase8.phase4._slug(hit.get('title') or 'clip')}{suffix}"
    if dest.is_file() and dest.stat().st_size > 50_000:
        return dest
    try:
        download_video(hit["download_url"], dest)
    except Exception:  # noqa: BLE001
        return None
    time.sleep(1.5)
    return dest if dest.is_file() else None


def _judge(record, plan, ranker, used_visual, cache) -> dict:
    path = Path(record.get("local_path") or "")
    if not path.is_file():
        record["rejected"] = "file missing"
        return record
    phase8.phase4._describe_media(record, path)
    if record.get("rejected"):
        return record
    width = int(record.get("width") or 0)
    height = int(record.get("height") or 0)
    if min(width, height) and min(width, height) < 360:
        record["rejected"] = "resolution is too low"
        return record
    frames = sample_frames(path, 3, cache / "frames" / path.stem)
    if not frames:
        record["rejected"] = "no frame could be read"
        return record
    rgb = load_rgb(frames[len(frames) // 2])
    decision = framing_decision(rgb)
    if decision == "reject" or black_fraction(rgb) >= 0.42:
        record["rejected"] = "the subject sits on a large black field"
        return record
    visual = average_hash(rgb)
    from analysis.uniqueness import hamming

    if any(hamming(visual, previous) <= 8 for previous in used_visual):
        record["rejected"] = "near-duplicate of a clip already used in this lesson"
        return record
    try:
        score = ranker.score_frames(frames, plan["prompt"])
    except Exception as error:  # noqa: BLE001
        record["rejected"] = f"visual rank failed: {type(error).__name__}"
        return record
    if decision == "keep":
        score += 0.02
    score -= float(record.get("_penalty") or 0)
    placed = dict(record)
    if decision == "crop":
        box = content_box(rgb)
        cropped = cache / f"{path.stem}-crop.mp4"
        if box and crop_video(path, cropped, box, width, height):
            placed["local_path"] = str(cropped)
            placed["clip_in"] = 0
        else:
            placed["rejected"] = "the subject could not be cropped to fill the frame"
            return placed
    placed["visual_hash"] = visual
    placed["relevance"] = round(score, 4)
    placed["quality"] = round(1.0 - black_fraction(rgb), 4)
    placed["thumbnails"] = [str(frame) for frame in frames]
    placed["framing"] = decision
    placed["rejected"] = None
    return placed


if __name__ == "__main__":
    try:
        status = main()
    except SystemExit as exc:
        print(exc, flush=True)
        status = exc.code if isinstance(exc.code, int) else 1
    sys.stdout.flush()
    os._exit(0 if status is None else status)
