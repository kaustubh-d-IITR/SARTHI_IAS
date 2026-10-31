"""Phase 8 B-roll pass. Uses the Phase 7 ElevenLabs transcripts and renders a new pair.

Does not call ElevenLabs again and does not rewrite Phase 7 outputs.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import run_phase4 as phase4  # noqa: E402
import run_phase5 as phase5  # noqa: E402
from analysis.opportunities import broll_opportunities, coverage_seconds, lesson_coverage  # noqa: E402
from analysis.uniqueness import asset_identity, enforce_unique, query_phrase_missing  # noqa: E402
from assets.video_commons import (  # noqa: E402
    _fill_public_domain_url,
    accept_video_candidate,
    download_video,
    search_videos,
)

PHASE7 = ROOT / "outputs" / "dr_shivin_phase7"
OUT = ROOT / "outputs" / "dr_shivin_phase8"
RAW = ROOT.parent / "DR_SHIVIN_RAW"
PRESS_PAGE = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(
    "File:Printing_in_progress_at_Sambad_Press,_Bhubaneswar,_Odisha,_India.webm"
)
ATTEMPTS: list[dict] = []
SUFFIX = "phase8"
TITLE_OVERRIDES = {
    "VIDEO_1": "You Will Make\n[[Mistakes]]",
    "VIDEO_2": "From [[NEET]] to [[UPSC]]",
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--render" in args:
        return render_lessons()
    return collect_assets()


def collect_assets() -> int:
    if not _transcripts_ready():
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    observations = PHASE7.parent / "dr_shivin_phase6" / "visual_observations.json"
    if observations.is_file():
        shutil.copyfile(observations, OUT / "visual_observations.json")
    rows: list[dict] = []
    used_identities: set[str] = set()
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = _load_transcript(video_id)
        opportunities = broll_opportunities(transcript)
        (OUT / video_id).mkdir(parents=True, exist_ok=True)
        (OUT / video_id / "opportunities.json").write_text(json.dumps(opportunities, indent=2), encoding="utf-8")
        speech = phase5._speech_text(transcript)
        for item in opportunities:
            if item["action"] != "search":
                rows.append(
                    {
                        "video_id": video_id,
                        "kind": "video",
                        "term": item["term"],
                        "rejected": item["reason"],
                    }
                )
                continue
            reused = _reuse(item["query"], cache)
            if reused and asset_identity(reused) in used_identities:
                reused = None
            base = reused or _find_download(item, speech, cache, exclude=used_identities)
            if base is None and item["query"] == "medical protective clothing":
                fallback = dict(item)
                fallback["variants"] = ["n95 respirator", "surgical mask"]
                fallback["query"] = "surgical mask"
                base = _find_download(fallback, speech, cache, exclude=used_identities)
            if base and asset_identity(base) in used_identities:
                base = None
            if not base:
                rows.append(
                    {
                        "video_id": video_id,
                        "query": item["query"],
                        "kind": "video",
                        "start": item["start"],
                        "end": item["end"],
                        "rejected": "no licensed video matched this spoken interval",
                    }
                )
                continue
            placed = dict(base)
            placed["video_id"] = video_id
            placed["match_terms"] = [item["term"]]
            placed["source_window"] = [item["start"], item["end"]]
            placed["kind"] = "video"
            placed["rejected"] = None
            reason = placed.get("reason") or f"Commons video matched the spoken word {item['term']}."
            if item.get("negation"):
                reason += " The spoken line denies the action, so the clip shows the object, not a person doing it."
            placed["reason"] = reason
            identity = asset_identity(placed)
            if identity:
                used_identities.add(identity)
            rows.append(placed)
    phase5.OUT = OUT
    phase5.CACHE = cache
    rows.extend(phase5._music_record())
    rows, _duplicates = enforce_unique(rows)
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [phase5.MUSIC_PAGE]
    pages.extend(
        row["page_url"]
        for row in rows
        if row.get("kind") == "video" and row.get("page_url") and not row.get("rejected") and row.get("local_path")
    )
    (OUT / "accepted.json").write_text(
        json.dumps({"page_urls": list(dict.fromkeys(pages)), "note": "Listed after a file was on disk and the license check passed."}, indent=2),
        encoding="utf-8",
    )
    _write_plan_summary(rows)
    return 0


def render_lessons() -> int:
    candidate_path = OUT / "asset_cache" / "candidates.json"
    if not candidate_path.is_file():
        status = collect_assets()
        if status:
            return status
    rows = json.loads(candidate_path.read_text(encoding="utf-8"))
    rows, rejected = enforce_unique(rows)
    candidate_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (OUT / "duplicate_rejections.json").write_text(
        json.dumps({"rejected": rejected}, indent=2),
        encoding="utf-8",
    )
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = _load_transcript(video_id)
        work = OUT / video_id / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.TITLE_OVERRIDES = TITLE_OVERRIDES
    phase5.FINAL_SUFFIX = SUFFIX
    phase5.render()
    _measure()
    return 0


def _transcripts_ready() -> bool:
    for video_id in ("VIDEO_1", "VIDEO_2"):
        path = PHASE7 / video_id / "transcript.json"
        if not path.is_file():
            print(f"missing Phase 7 transcript for {video_id}")
            return False
        transcript = json.loads(path.read_text(encoding="utf-8"))
        if transcript.get("method") != "elevenlabs":
            print(f"{video_id} is not an ElevenLabs transcript")
            return False
    return True


def _load_transcript(video_id: str) -> dict:
    return json.loads((PHASE7 / video_id / "transcript.json").read_text(encoding="utf-8"))


def _reuse(query: str, cache: Path) -> dict | None:
    if query == "newspaper":
        source = PHASE7 / "asset_cache" / "sambad-press.mp4"
        if not source.is_file():
            return None
        dest = cache / "sambad-press.mp4"
        if not dest.is_file():
            shutil.copyfile(source, dest)
        return {
            "query": query,
            "title": "File:Printing in progress at Sambad Press, Bhubaneswar, Odisha, India.webm",
            "page_url": PRESS_PAGE,
            "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
            "artist": "Wikimedia Commons contributor, Sambad Press, Bhubaneswar",
            "local_path": str(dest),
            "duration_seconds": 4.0,
            "reason": "Sambad Press prints newspapers. The inspected frame shows the press and paper rolls, not a person reading.",
        }
    if query == "hospital ward":
        matches = list((PHASE7 / "asset_cache").glob("file-cl*nica*.ogv"))
        if not matches:
            return None
        dest = cache / "clinic.ogv"
        if not dest.is_file():
            shutil.copyfile(matches[0], dest)
        return {
            "query": query,
            "title": "File:Clínica Especializada Internacional video.ogv",
            "page_url": "https://commons.wikimedia.org/wiki/File%3ACl%C3%ADnica_Especializada_Internacional_video.ogv",
            "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
            "artist": "Truthman10200",
            "local_path": str(dest),
            "duration_seconds": 28.3,
            "reason": "The clip shows the exterior of a clinic during the hospital and COVID-duty sentence.",
        }
    return None


def _find_download(item: dict, speech: str, cache: Path, exclude: set[str] | None = None) -> dict | None:
    blocked = exclude or set()
    for variant in item.get("variants") or [item["query"]]:
        for offset in (0, 8):
            try:
                hits = search_videos(variant, limit=8, offset=offset)
            except Exception as error:  # noqa: BLE001
                print(item["query"], "search", type(error).__name__)
                time.sleep(2)
                continue
            time.sleep(1.2)
            for hit in hits:
                if asset_identity(hit) in blocked:
                    _note_attempt(item, variant, offset, hit, "already used in this lesson")
                    continue
                haystack = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
                if query_phrase_missing(variant, haystack):
                    _note_attempt(item, variant, offset, hit, "query phrase is not in the description")
                    continue
                if item.get("negation") and any(word in haystack for word in ("reading", "reads", "reader")):
                    _note_attempt(item, variant, offset, hit, "clip depicts the denied action of reading")
                    continue
                _fill_public_domain_url(hit)
                reason = accept_video_candidate(hit, variant, speech=item.get("sentence") or speech)
                if reason:
                    _note_attempt(item, variant, offset, hit, reason)
                    continue
                suffix = Path(urllib.parse.urlparse(hit["download_url"]).path).suffix.lower() or ".mp4"
                dest = cache / f"{phase4._slug(hit.get('title') or 'clip')}{suffix}"
                if not dest.is_file():
                    try:
                        download_video(hit["download_url"], dest)
                    except Exception as error:  # noqa: BLE001
                        print(hit.get("title"), "download", type(error).__name__)
                        time.sleep(2)
                        continue
                    time.sleep(2)
                record = dict(hit)
                title = (hit.get("title") or "").lower()
                record.update(
                    {
                        "query": item["query"],
                        "local_path": str(dest),
                        "reason": f"Commons video matched {variant} for the spoken word {item['term']}.",
                    }
                )
                if "doffing" in title or "taking off" in title:
                    record["clip_in"] = 7.5
                if "bookshelf" in title or "bookshelves" in title:
                    record["clip_in"] = 7
                phase4._describe_media(record, dest)
                if record.get("rejected"):
                    _note_attempt(item, variant, offset, hit, record.get("rejected"))
                    continue
                _note_attempt(item, variant, offset, hit, None)
                return record
    return None


def _note_attempt(item: dict, variant: str, offset: int, hit: dict, reason: str | None) -> None:
    ATTEMPTS.append(
        {
            "term": item.get("term"),
            "query": item.get("query"),
            "variant": variant,
            "offset": offset,
            "title": hit.get("title"),
            "page_url": hit.get("page_url"),
            "license": hit.get("license"),
            "size": hit.get("size"),
            "rejected": reason,
        }
    )


def _write_plan_summary(rows: list[dict]) -> None:
    summary = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = _load_transcript(video_id)
        lesson = float(transcript["segments"][-1]["end"]) - float(transcript["segments"][0]["start"])
        intervals = [
            tuple(row["source_window"])
            for row in rows
            if row.get("video_id") == video_id and row.get("source_window") and not row.get("rejected")
        ]
        visible = coverage_seconds(intervals)
        summary.append(
            {
                "video_id": video_id,
                "lesson_span_seconds": round(lesson, 3),
                "planned_broll_seconds": visible,
                "planned_fraction": round(visible / lesson, 4) if lesson else 0,
                "clips": [
                    {
                        "query": row.get("query"),
                        "window": row.get("source_window"),
                        "title": row.get("title"),
                        "license": row.get("license"),
                        "page_url": row.get("page_url"),
                    }
                    for row in rows
                    if row.get("video_id") == video_id and row.get("source_window") and not row.get("rejected")
                ],
            }
        )
    (OUT / "planned_coverage.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "search_log.json").write_text(json.dumps(ATTEMPTS, indent=2), encoding="utf-8")
    for item in summary:
        print(item["video_id"], "planned", f"{item['planned_fraction']:.1%}", item["planned_broll_seconds"], "s")


def _measure() -> None:
    """Record rendered coverage from the editing plan. The outro is not in that duration."""
    notes = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        plan_path = OUT / video_id / "editing_plan.json"
        outro_path = OUT / video_id / "outro.json"
        if not plan_path.is_file():
            continue
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        intervals = [
            (float(item["start"]), float(item["end"]))
            for item in plan["decisions"]
            if item.get("action") == "replace_with_broll"
        ]
        lesson = float(plan["input_media"]["duration_seconds"])
        visible = lesson_coverage(intervals, lesson)
        spans = [end - start for start, end in intervals if end > start]
        outro = json.loads(outro_path.read_text(encoding="utf-8")) if outro_path.is_file() else {}
        notes.append(
            {
                "video_id": video_id,
                "lesson_seconds": lesson,
                "outro_seconds": outro.get("outro_duration"),
                "inserts": len(spans),
                "visible_broll_seconds": visible,
                "average_insert_seconds": round(sum(spans) / len(spans), 3) if spans else 0,
                "visible_fraction": round(visible / lesson, 4) if lesson else 0,
                "useful_fraction": round(visible / lesson, 4) if lesson else 0,
                "intervals": intervals,
            }
        )
        print(video_id, "rendered", f"{notes[-1]['visible_fraction']:.1%}", "of", lesson, "s")
    (OUT / "rendered_coverage.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
