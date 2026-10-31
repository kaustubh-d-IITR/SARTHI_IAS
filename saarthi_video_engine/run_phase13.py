"""Phase 13. Fuller VIDEO_1 coverage, one dotted canvas, and the reference look.

Does not call ElevenLabs and does not rewrite earlier phase outputs.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import run_phase5 as phase5
import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from analysis.subjects import opportunity_records  # noqa: E402
from analysis.uniqueness import file_sha256  # noqa: E402
from assets.video_commons import (  # noqa: E402
    _fill_public_domain_url,
    accept_video_candidate,
    download_video,
    search_videos,
)

PHASE12 = ROOT / "outputs" / "dr_shivin_phase12"
LIBRARY_PAGE = (
    "https://commons.wikimedia.org/wiki/File%3APressing_screen_to_move_compact_bookshelves_"
    "in_East_Community_Library_at_St._Petersburg_College_Clearwater_Campus%2C_Florida%2C_US%2C_"
    "on_21_August_2019.webm"
)
STUDY_QUERIES = ("writing", "studying", "classroom")
STUDY_BLOCK = (
    "swim",
    "president",
    "parliament",
    "stacker",
    "presentation",
    "interview",
    "roundtable",
    "wedding",
    "concert",
    "wikipedia",
    "wikimania",
    "conference",
    "proposal",
    "webinar",
)
STUDY_VISUAL = ("student", "studying", "desk", "classroom", "homework", "notebook", "fountain pen", "pen ")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _bind_output()
    if "--render-only" not in args:
        summary = prepare()
        print(json.dumps(summary, ensure_ascii=False))
    if "--render" in args or "--render-only" in args:
        phase5.MATCH_REFERENCE_LOOK = True
        return phase8.render_lessons()
    return 0


def _bind_output() -> None:
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase13"
    phase8.SUFFIX = "phase13"
    phase5.OUT = phase8.OUT
    phase5.CACHE = phase8.OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase13"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.TITLE_OVERRIDES = phase5.TITLE_OVERRIDES


def prepare() -> dict:
    _bind_output()
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    cache = phase5.CACHE
    if not cache.exists():
        shutil.copytree(PHASE12 / "asset_cache", cache)
    else:
        for item in (PHASE12 / "asset_cache").iterdir():
            dest = cache / item.name
            if item.is_file() and not dest.exists():
                shutil.copyfile(item, dest)
    for name in ("visual_observations.json",):
        source = PHASE12 / name
        dest = phase8.OUT / name
        if source.is_file() and not dest.exists():
            shutil.copyfile(source, dest)
    rows = json.loads((cache / "candidates.json").read_text(encoding="utf-8"))
    for row in rows:
        if row.get("page_url") == LIBRARY_PAGE and row.get("video_id") == "VIDEO_1":
            row["clip_in"] = 7.5
            row["reason"] = "Shelves are on screen from 7.5 seconds. The opening checkout screen is skipped."
    study, study_log = _find_study_clip(cache)
    if study:
        rows = [row for row in rows if row.get("match_terms") != ["process"]]
        rows.append(study)
    accepted = {
        "page_urls": sorted(
            {
                row["page_url"]
                for row in rows
                if not row.get("rejected") and row.get("page_url") and row.get("local_path")
            }
        )
    }
    (phase8.OUT / "accepted.json").write_text(json.dumps(accepted, indent=2), encoding="utf-8")
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    transcript = json.loads((ROOT / "outputs" / "dr_shivin_phase7" / "VIDEO_1" / "transcript.json").read_text(encoding="utf-8"))
    audit = opportunity_records(transcript)
    (phase8.OUT / "video1_subjects.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "study_clip": study.get("page_url") if study else None,
        "study_log": study_log,
        "sentences": len(audit),
        "searchable": [row["text"] for row in audit if not row["teacher_preferred"]],
    }


def _find_study_clip(cache: Path) -> tuple[dict | None, list[dict]]:
    log: list[dict] = []
    speech = "you will make mistakes jab aap poore process ke through jaoge"
    for query in STUDY_QUERIES:
        for offset in (0, 10):
            time.sleep(1)
            try:
                found = search_videos(query, limit=8, offset=offset)
            except Exception as error:  # noqa: BLE001
                log.append({"query": query, "offset": offset, "error": type(error).__name__})
                continue
            for candidate in found:
                _fill_public_domain_url(candidate)
                _consider(candidate, query, speech, cache, log)
                if log and log[-1].get("reason") == "downloaded":
                    return _study_row(candidate, cache / log[-1]["file"]), log
    return None, log


def _consider(candidate: dict, query: str, speech: str, cache: Path, log: list[dict]) -> None:
    reason = accept_video_candidate(candidate, query, speech)
    text = f"{candidate.get('title') or ''} {candidate.get('description') or ''}".lower()
    if reason and reason.startswith("mime video/mpeg") and "pen" in text:
        reason = None
    if reason is None and any(word in text for word in STUDY_BLOCK):
        reason = "description is off the subject"
    if reason is None and not any(word in text for word in STUDY_VISUAL):
        reason = "description misses the subject"
    if reason:
        log.append({"query": query, "title": candidate.get("title"), "reason": reason})
        return
    mime = candidate.get("mime") or ""
    suffix = ".webm" if "webm" in mime else ".ogv" if "ogg" in mime else ".mpg" if "mpeg" in mime else ".mp4"
    name = "study-" + "".join(ch.lower() if ch.isalnum() else "-" for ch in (candidate.get("title") or "clip"))[:40] + suffix
    dest = cache / name
    if not dest.exists():
        try:
            download_video(candidate["download_url"], dest)
        except Exception as error:  # noqa: BLE001
            log.append({"query": query, "title": candidate.get("title"), "reason": f"download failed: {type(error).__name__}"})
            return
    log.append({"query": query, "title": candidate.get("title"), "reason": "downloaded", "file": dest.name})


def _study_row(candidate: dict, dest: Path) -> dict:
    return {
        "video_id": "VIDEO_1",
        "kind": "video",
        "match_terms": ["process"],
        "title": candidate.get("title"),
        "page_url": candidate.get("page_url"),
        "download_url": candidate.get("download_url"),
        "license": candidate.get("license"),
        "license_url": candidate.get("license_url"),
        "artist": candidate.get("artist"),
        "description": candidate.get("description"),
        "local_path": str(dest),
        "sha256": file_sha256(dest),
        "bytes": dest.stat().st_size,
        "duration_seconds": None,
        "source_window": [46.76, 50.8],
        "clip_in": 1,
        "rejected": False,
        "reason": "The audience-process sentence. A different file from the library shelves.",
        "provider": "commons",
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc, flush=True)
        sys.stdout.flush()
        raise
