"""Phase 15. Subject rules, retrieval, and a render of both raw lessons.

Reuses the Phase 7 transcript when it is the ElevenLabs transcript of these
files. Does not call ElevenLabs again. Does not rewrite earlier phase outputs.
"""

from __future__ import annotations

import gc
import hashlib
import json
import sys
from pathlib import Path

import run_phase5 as phase5
import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from analysis.opportunities import lesson_coverage  # noqa: E402
from analysis.uniqueness import enforce_unique, hamming  # noqa: E402
from assets.footage import provider_status, select_clips  # noqa: E402
from assets.library import Catalogue  # noqa: E402
from assets.visual_rank import ClipRanker, average_hash, load_rgb  # noqa: E402
from render.outro import tool  # noqa: E402

PHASE7 = ROOT / "outputs" / "dr_shivin_phase7"
PHASE13 = ROOT / "outputs" / "dr_shivin_phase13"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _bind()
    if "--render-only" in args:
        raise SystemExit("Phase 15 does not render from a stale candidate list. Omit --render-only.")
    prepare()
    phase5.MATCH_REFERENCE_LOOK = True
    status = phase8.render_lessons()
    if status:
        return status
    _measure_frames()
    return 0


def _bind() -> None:
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase15"
    phase8.SUFFIX = "phase15"
    phase5.OUT = phase8.OUT
    phase5.CACHE = phase8.OUT / "asset_cache"
    phase5.FINAL_SUFFIX = "phase15"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.TITLE_OVERRIDES = phase5.TITLE_OVERRIDES


def prepare() -> None:
    _bind()
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    phase5.CACHE.mkdir(parents=True, exist_ok=True)
    observations = json.loads((PHASE13 / "visual_observations.json").read_text(encoding="utf-8"))
    (phase8.OUT / "visual_observations.json").write_text(json.dumps(observations, indent=2), encoding="utf-8")
    (phase8.OUT / "provider_status.json").write_text(json.dumps(provider_status(), indent=2), encoding="utf-8")
    catalogue = Catalogue(phase8.OUT / "library.sqlite")
    _seed(catalogue)
    ranker = _ranker()
    rows: list[dict] = []
    search_log = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = json.loads((PHASE7 / video_id / "transcript.json").read_text(encoding="utf-8"))
        if transcript.get("method") != "elevenlabs":
            raise SystemExit(f"{video_id} has no ElevenLabs transcript to reuse")
        _fingerprint(video_id)
        speech = phase5._speech_text(transcript)
        board = ((observations.get(video_id) or {}).get("terms") or [])
        chosen, log = select_clips(
            video_id,
            transcript,
            speech,
            phase5.CACHE,
            catalogue,
            ranker=ranker,
            board_terms=board,
        )
        rows.extend(chosen)
        search_log.extend(log)
        (phase8.OUT / video_id).mkdir(parents=True, exist_ok=True)
        (phase8.OUT / video_id / "opportunities.json").write_text(
            json.dumps({"clips": chosen, "log": log}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(video_id, "clips", len(chosen), "logged", len(log), flush=True)
    catalogue.close()
    del ranker
    gc.collect()
    rows.extend(phase5._music_record())
    rows, rejected = enforce_unique(rows)
    (phase5.CACHE / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [
        row["page_url"]
        for row in rows
        if row.get("page_url") and row.get("local_path") and not row.get("rejected")
    ]
    (phase8.OUT / "accepted.json").write_text(json.dumps({"page_urls": pages}, indent=2), encoding="utf-8")
    (phase8.OUT / "search_log.json").write_text(json.dumps(search_log, indent=2, ensure_ascii=False), encoding="utf-8")
    (phase8.OUT / "duplicate_rejections.json").write_text(json.dumps({"rejected": rejected}, indent=2), encoding="utf-8")
    print("candidates", len(rows), "duplicates", rejected, flush=True)


def _ranker():
    try:
        ranker = ClipRanker()
        ranker._load()
        print("clip loaded", flush=True)
        return ranker
    except Exception as error:  # noqa: BLE001
        print("clip unavailable", type(error).__name__, flush=True)
        return None


def _seed(catalogue: Catalogue) -> None:
    path = PHASE13 / "asset_cache" / "candidates.json"
    if not path.is_file():
        return
    for row in json.loads(path.read_text(encoding="utf-8")):
        local = row.get("local_path")
        if row.get("rejected") or not local or not Path(local).is_file():
            continue
        catalogue.upsert({**row, "tags": row.get("match_terms") or [], "subjects": row.get("match_terms") or []})


def _fingerprint(video_id: str) -> None:
    source = ROOT.parent / "DR_SHIVIN_RAW" / f"{video_id}.mp4"
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    payload = {"video_id": video_id, "sha256": digest.hexdigest(), "transcript": "phase7 elevenlabs scribe_v2", "reused": True}
    (phase8.OUT / video_id).mkdir(parents=True, exist_ok=True)
    (phase8.OUT / video_id / "source_fingerprint.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _measure_frames() -> None:
    import subprocess

    notes = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        plan = json.loads((phase8.OUT / video_id / "editing_plan.json").read_text(encoding="utf-8"))
        final = phase8.OUT / video_id / f"{video_id}_phase15.mp4"
        lesson = float(plan["input_media"]["duration_seconds"])
        decisions = [item for item in plan["decisions"] if item.get("action") == "replace_with_broll"]
        teacher = next(item for item in plan["decisions"] if item.get("action") == "show_teacher")
        folder = phase8.OUT / video_id / "contact"
        folder.mkdir(parents=True, exist_ok=True)
        teacher_frame = folder / "teacher.jpg"
        _frame(final, float(teacher["start"]) + 0.4, teacher_frame)
        teacher_hash = average_hash(_center(load_rgb(teacher_frame))) if teacher_frame.is_file() else ""
        visible = []
        pages = []
        for decision in decisions:
            stamp = (float(decision["start"]) + float(decision["end"])) / 2
            dest = folder / f"broll_{decision['id']}.jpg"
            _frame(final, stamp, dest)
            if not dest.is_file():
                continue
            distance = hamming(teacher_hash, average_hash(_center(load_rgb(dest)))) if teacher_hash else 64
            if distance >= 6:
                visible.append((float(decision["start"]), float(decision["end"])))
                pages.append(decision.get("page_url"))
        seconds = lesson_coverage(visible, lesson)
        notes.append(
            {
                "video_id": video_id,
                "lesson_seconds": lesson,
                "visible_broll_seconds": seconds,
                "visible_fraction": round(seconds / lesson, 4) if lesson else 0,
                "inserts": len(visible),
                "unique_pages": len(set(pages)),
                "planned_inserts": len(decisions),
                "target": 0.5,
                "target_met": seconds / lesson >= 0.5 if lesson else False,
            }
        )
        _contact(final, folder / "sheet.jpg", lesson)
    (phase8.OUT / "PHASE15_COVERAGE_REPORT.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(json.dumps(notes), flush=True)


def _center(frame):
    """Hash the picture, not the dotted margin that every frame shares."""
    height, width = frame.shape[:2]
    y0, y1 = int(height * 0.22), int(height * 0.78)
    x0, x1 = int(width * 0.18), int(width * 0.82)
    return frame[y0:y1, x0:x1]


def _frame(source: Path, stamp: float, dest: Path) -> None:
    import subprocess

    subprocess.run(
        [tool("ffmpeg"), "-y", "-i", str(source), "-ss", f"{stamp:.2f}", "-frames:v", "1", "-update", "1", str(dest)],
        capture_output=True,
        check=False,
    )


def _contact(source: Path, dest: Path, lesson: float) -> None:
    import subprocess

    subprocess.run(
        [tool("ffmpeg"), "-y", "-i", str(source), "-vf", "fps=1/8,scale=180:-1,tile=6x6", "-frames:v", "1", "-update", "1", str(dest)],
        capture_output=True,
        check=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
