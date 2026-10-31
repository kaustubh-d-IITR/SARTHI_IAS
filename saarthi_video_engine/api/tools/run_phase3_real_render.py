"""One real Phase 3 render through the Studio render service. Run one mode at a time.

auto_edit: VIDEO_1 with the known Phase 19 transcript, plan, and approved Commons cutaway.
my_broll:  VIDEO_2 with the known Phase 19 transcript. The Studio Phase 2 matcher places
           one user clip (a copy of the Phase 19 PPE clip) from its description.

Sources are copied into a new Studio session. Nothing under DR_SHIVIN_RAW, LOGO, OUTRO,
or outputs/ is written. ElevenLabs and Commons are not called.

From api/:  .venv\\Scripts\\python.exe tools\\run_phase3_real_render.py auto_edit
A failed attempt can be retried on the same approved session with
            tools\\run_phase3_real_render.py retry <session_id> <owner>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

API = Path(__file__).resolve().parents[1]
ENGINE = API.parent
PROJECT = ENGINE.parent
sys.path.insert(0, str(API))

from app.config import Settings  # noqa: E402
from app.media_probe import probe_video  # noqa: E402
from app.processing.jobs import Services, queue_processing, run_processing  # noqa: E402
from app.services.rendering.jobs import queue_render, run_render  # noqa: E402
from app.storage import StudioStore  # noqa: E402

PHASE19 = ENGINE / "outputs" / "dr_shivin_phase19"
PPE_DESCRIPTION = "A health worker on COVID duty taking off a protective gown and mask."


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_upload(store: StudioStore, owner: str, source: Path, kind: str, description: str = "") -> dict:
    upload_id = str(uuid.uuid4())
    folder = store.uploads / upload_id
    folder.mkdir(parents=True, exist_ok=False)
    stored = folder / f"media{source.suffix.lower()}"
    shutil.copyfile(source, stored)
    meta = {
        "upload_id": upload_id,
        "kind": kind,
        "client_id": owner,
        "filename": source.name,
        "description": description,
        "size_bytes": stored.stat().st_size,
        "content_type": "video/mp4",
        **probe_video(stored),
        "validation_status": "valid",
        "stored_name": stored.name,
        "created_at": _now(),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _require(*paths: Path) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Known inputs are missing: " + ", ".join(missing))


def auto_edit(store: StudioStore) -> tuple[str, str]:
    raw = PROJECT / "DR_SHIVIN_RAW" / "VIDEO_1.mp4"
    plan_path = PHASE19 / "VIDEO_1" / "editing_plan.json"
    transcript = PHASE19 / "VIDEO_1" / "work" / "transcript.json"
    clip = PHASE19 / "VIDEO_1" / "composition" / "assets" / "broll_clip-467.mp4"
    _require(raw, plan_path, transcript, clip)
    owner = str(uuid.uuid4())
    raw_meta = _copy_upload(store, owner, raw, "raw")
    session = store.create_session(owner, "auto_edit", raw_meta["upload_id"], [])
    session_id = session["session_id"]
    work = store.work_dir(session_id)
    footage = work / "footage"
    footage.mkdir()
    copied = footage / clip.name
    shutil.copyfile(clip, copied)
    clip_seconds = probe_video(copied)["duration_seconds"]
    engine_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shutil.copyfile(transcript, work / "transcript.json")
    placements = []
    index = []
    for decision in engine_plan["decisions"]:
        if decision["action"] != "replace_with_broll":
            continue
        play = round(decision["end"] - decision["start"], 3)
        placements.append(
            {
                "asset_id": decision["asset_id"],
                "filename": Path(decision.get("asset_path") or clip.name).name,
                "description": decision.get("reason") or "",
                "speech_start": decision["start"],
                "speech_end": decision["end"],
                "source_in": 0.0,
                "source_out": play,
                "clip_duration": clip_seconds,
                "explanation": decision.get("reason") or "",
                "evidence": "existing_pipeline",
                "approval": "approved",
                "page_url": decision.get("page_url") or "",
                "license": decision.get("license") or "",
                "origin": "commons",
            }
        )
        index.append({"asset_id": decision["asset_id"], "sha256": _sha(copied), "relative_path": f"footage/{copied.name}"})
    (work / "footage_index.json").write_text(json.dumps({"assets": index}, indent=2), encoding="utf-8")
    studio = {"session_id": session_id, "mode": "auto_edit", "status": "plan_ready", "validation": {"ok": True, "errors": []}, "placements": placements, "rendered": False}
    (work / "editing_plan.json").write_text(json.dumps({"engine_plan": engine_plan, "studio": studio}, indent=2), encoding="utf-8")
    record = store.get_session(session_id, owner)
    record.update(
        {
            "status": "plan_ready",
            "stage": "plan_ready",
            "note": "Phase 3 real render: known Phase 19 VIDEO_1 transcript and plan.",
            "counts": {
                "segments": len(json.loads(transcript.read_text(encoding="utf-8"))["segments"]),
                "broll_supplied": 0,
                "candidate_matches": len(placements),
                "approved_placements": len(placements),
                "teacher_intervals": sum(1 for row in engine_plan["decisions"] if row["action"] == "show_teacher"),
                "validation": "passed",
            },
        }
    )
    store.write_session(record)
    return session_id, owner


def my_broll(store: StudioStore) -> tuple[str, str]:
    raw = PROJECT / "DR_SHIVIN_RAW" / "VIDEO_2.mp4"
    transcript_path = PHASE19 / "VIDEO_2" / "work" / "transcript.json"
    clips = sorted((PHASE19 / "VIDEO_2" / "composition" / "assets").glob("broll_file-demonstration-of-doffing*.mp4"))
    _require(raw, transcript_path)
    if len(clips) != 1:
        raise SystemExit("Expected the one Phase 19 PPE clip.")
    owner = str(uuid.uuid4())
    raw_meta = _copy_upload(store, owner, raw, "raw")
    clip_meta = _copy_upload(store, owner, clips[0], "broll", PPE_DESCRIPTION)
    session = store.create_session(owner, "my_broll", raw_meta["upload_id"], [{"asset_id": clip_meta["upload_id"], "description": PPE_DESCRIPTION}])
    session_id = session["session_id"]
    known = json.loads(transcript_path.read_text(encoding="utf-8"))

    def transcribe(path: Path, _cache: Path) -> dict:
        body = dict(known)
        body["source_sha256"] = _sha(path)
        body["reused"] = True
        body["processed_at"] = _now()
        body.pop("source_file", None)
        return body

    def no_search(*_args):
        raise RuntimeError("Mode B must not search for footage.")

    services = Services(transcribe=transcribe, select_auto=no_search)
    queue_processing(store, session_id, owner, services)
    run_processing(store, session_id, owner, services)
    record = store.get_session(session_id, owner)
    if record["status"] != "plan_ready":
        raise SystemExit(f"Phase 2 processing did not finish: {record.get('error')}")
    return session_id, owner


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if mode not in {"auto_edit", "my_broll", "retry"}:
        raise SystemExit("use auto_edit, my_broll, or retry <session_id> <owner>")
    store = StudioStore(Settings.from_env())
    if not store.ensure():
        raise SystemExit("Studio storage is not writable.")
    if mode == "retry":
        session_id, owner = argv[2], argv[3]
    else:
        session_id, owner = auto_edit(store) if mode == "auto_edit" else my_broll(store)
    record = queue_render(store, session_id, owner)
    print("session", session_id, "owner", owner, "render", record["render"]["render_id"], flush=True)
    run_render(store, session_id, owner)
    state = store.get_session(session_id, owner).get("render") or {}
    print(json.dumps({key: state.get(key) for key in ("render_id", "status", "error", "metadata", "audio_verification", "outro", "approved_broll_placements")}, indent=2))
    return 0 if state.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
