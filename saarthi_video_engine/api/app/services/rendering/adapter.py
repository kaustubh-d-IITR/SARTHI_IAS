"""Turn one approved Studio plan into a render folder and run the existing renderer on it.

The approved engine plan is copied. The copy only gains real file paths, the
licensed music bed already used by the engine, the trimmed logo, and source
dimensions measured from the cut lesson. Picture and caption decisions keep the
approved times. Footage is never searched for or substituted here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.errors import StudioError
from app.services.rendering import studio as studio_prep
from app.services.rendering.bridge import OPEN, UNDER_SPEECH, EngineBridge, EngineFailure
from app.storage import StudioStore

CORRELATION_FLOOR = 0.90
CANVAS = {"width": 720, "height": 1280, "fps": 30}
PICTURE = {"show_teacher", "replace_with_broll"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class RenderError(RuntimeError):
    """A message that is safe to show in the Studio."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError("A required render input could not be read.") from exc


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, path: Path) -> Path:
    resolved_root, resolved = root.resolve(), path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise RenderError("A render asset points outside this session.")
    return resolved


def load_approved(store: StudioStore, record: dict, version_id: str | None = None) -> dict:
    """The exact plan version to render. Sessions from before versioning fall back to editing_plan.json."""
    work = store.work_dir(record["session_id"])
    plan_file = work / "editing_plan.json"
    if record.get("status") != "plan_ready" or not (work / "transcript.json").is_file():
        raise RenderError("A validated editing plan and transcript are required before rendering.")
    version_id = version_id or record.get("plan_version")
    if version_id:
        version_file = work / "plans" / f"{version_id}.json"
        if "/" in version_id or "\\" in version_id or not version_file.is_file():
            raise RenderError("The approved editing-plan version is missing.")
        saved = _read(version_file)
        if not saved.get("validation_ok"):
            raise RenderError("That editing-plan version did not pass validation.")
    elif plan_file.is_file():
        saved = _read(plan_file)
        saved["version_id"] = "legacy"
    else:
        raise RenderError("A validated editing plan and transcript are required before rendering.")
    studio = saved.get("studio") or {}
    if not (studio.get("validation") or {}).get("ok"):
        raise RenderError("The editing plan did not pass validation.")
    if studio.get("session_id") != record["session_id"] or studio.get("mode") != record["mode"]:
        raise RenderError("The saved editing plan does not belong to this session.")
    if not isinstance(saved.get("engine_plan"), dict):
        raise RenderError("The saved editing plan is incomplete.")
    return saved


def _media(store: StudioStore, meta: dict, materialize: bool) -> Path | None:
    """Local copy of an upload (worker), or only proof that it exists in storage (API)."""
    if not materialize:
        info = store.objects.head(meta["object_key"])
        return Path(meta["object_key"]) if info is not None and info.size > 0 else None
    try:
        path = store.media_file(meta["upload_id"])
    except StudioError:
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def resolve_inputs(store: StudioStore, record: dict, saved: dict, materialize: bool = True) -> dict:
    """Map every planned cutaway to a file this session owns. Missing files stop the render."""
    work = store.work_dir(record["session_id"])
    owner = record["client_id"]
    raw_id = record["raw_video"]["upload_id"]
    try:
        raw_meta = store.get_upload(raw_id, owner)
    except StudioError as exc:
        raise RenderError("The raw lesson file is missing.") from exc
    if raw_meta.get("kind") != "raw" or raw_meta.get("validation_status") != "valid":
        raise RenderError("The raw lesson is not a validated upload for this session.")
    raw = _media(store, raw_meta, materialize)
    if raw is None:
        raise RenderError("The raw lesson file is missing.")

    studio = saved.get("studio") or {}
    placements = {item.get("asset_id"): item for item in studio.get("placements") or []}
    decisions = [item for item in saved["engine_plan"].get("decisions") or [] if item.get("action") == "replace_with_broll"]
    clips: dict[str, dict] = {}
    if record["mode"] == "my_broll":
        if any(item.get("origin") == "commons" or item.get("page_url") for item in placements.values()):
            raise RenderError("Mode B plans cannot reference external footage.")
        owned = {item["upload_id"] for item in record.get("broll") or []}
        catalogue_path = work / "catalogue.json"
        catalogue = _read(catalogue_path).get("assets") or [] if catalogue_path.is_file() else []
        hashes = {item.get("asset_id"): item.get("sha256") for item in catalogue}
        for decision in decisions:
            asset_id = decision.get("asset_id")
            if asset_id not in owned:
                raise RenderError("The plan references a B-roll clip outside this session.")
            if decision.get("page_url") or str(decision.get("asset_path") or "").startswith("http"):
                raise RenderError("Mode B plans cannot reference external footage.")
            try:
                meta = store.get_upload(asset_id, owner)
            except StudioError as exc:
                raise RenderError("A user-supplied B-roll file is missing or belongs to another session.") from exc
            if meta.get("kind") != "broll" or meta.get("validation_status") != "valid":
                raise RenderError("A user-supplied B-roll file is not a validated video upload.")
            path = _media(store, meta, materialize)
            if path is None:
                raise RenderError("A user-supplied B-roll file is missing.")
            placement = placements.get(asset_id)
            if placement is None or placement.get("approval") != "approved":
                raise RenderError("The plan contains a B-roll decision without an approved placement.")
            clips[asset_id] = {"path": path, "filename": meta.get("filename") or "clip", "sha256": hashes.get(asset_id) or "", "origin": "user"}
    elif record["mode"] == "auto_edit":
        index_path = work / "footage_index.json"
        index = _read(index_path) if index_path.is_file() else {"assets": []}
        entries = {item.get("asset_id"): item for item in index.get("assets") or []}
        for decision in decisions:
            asset_id = decision.get("asset_id")
            entry = entries.get(asset_id)
            if not entry:
                raise RenderError("An approved Auto-Edit footage file is missing from the session catalogue.")
            path = _inside(work, work / str(entry.get("relative_path") or ""))
            if materialize:
                present = path.is_file() and path.stat().st_size > 0
            else:
                key = f"work/{record['session_id']}/{path.relative_to(work.resolve()).as_posix()}"
                info = store.objects.head(key)
                present = (info is not None and info.size > 0) or (path.is_file() and path.stat().st_size > 0)
            if not present:
                raise RenderError("An approved Auto-Edit footage file is missing.")
            placement = placements.get(asset_id)
            if placement is None or placement.get("approval") != "approved":
                raise RenderError("The plan contains unapproved external footage.")
            clips[asset_id] = {"path": path, "filename": placement.get("filename") or path.name, "sha256": entry.get("sha256") or "", "origin": "commons"}
    else:
        raise RenderError("The Studio session has an unsupported workflow mode.")
    return {"raw": raw, "raw_meta": raw_meta, "clips": clips, "placements": placements}


def _streams(probe: dict) -> tuple[dict | None, dict | None]:
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    return video, audio


def _rate(stream: dict) -> float | None:
    raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/0")
    if "/" in raw:
        top, bottom = raw.split("/", 1)
        try:
            return round(float(top) / float(bottom), 3) if float(bottom) else None
        except ValueError:
            return None
    try:
        return round(float(raw), 3)
    except ValueError:
        return None


def output_metadata(probe: dict, path: Path) -> dict:
    video, audio = _streams(probe)
    if not video or not audio:
        raise RenderError("The rendered MP4 must contain both picture and audio.")
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 or path.stat().st_size == 0:
        raise RenderError("The rendered MP4 is empty or has no duration.")
    return {
        "duration_seconds": round(duration, 3),
        "width": video.get("width"),
        "height": video.get("height"),
        "frame_rate": _rate(video),
        "size_bytes": path.stat().st_size,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
    }


def _check_canvas(meta: dict, what: str) -> None:
    if meta["width"] != CANVAS["width"] or meta["height"] != CANVAS["height"]:
        raise RenderError(f"The {what} is {meta['width']}×{meta['height']}, not 720×1280.")
    if meta["frame_rate"] is None or abs(float(meta["frame_rate"]) - CANVAS["fps"]) > 0.1:
        raise RenderError(f"The {what} is not 30 fps.")


def _probe_media(bridge: EngineBridge, path: Path, what: str) -> dict:
    try:
        probe = bridge.probe(path)
    except EngineFailure as exc:
        raise RenderError(f"The {what} is unreadable or corrupted.") from exc
    video, _audio = _streams(probe)
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not video or duration <= 0:
        raise RenderError(f"The {what} is unreadable or corrupted.")
    return probe


def timeline_changes(approved: dict, rendered: dict) -> list[str]:
    """Differences in picture or caption decisions other than the file paths the render fills in."""
    def key(plan):
        rows = {}
        for item in plan.get("decisions") or []:
            if item.get("action") in PICTURE or item.get("action") == "caption":
                rows[item["id"]] = (item["action"], item["start"], item["end"], item.get("asset_id"), item.get("text"))
        return rows

    before, after = key(approved), key(rendered)
    end = float(rendered["input_media"]["duration_seconds"])
    changes = []
    for decision_id, row in before.items():
        if decision_id not in after:
            if row[1] >= end - 0.05:
                continue
            changes.append(f"{decision_id} was removed")
            continue
        now = after[decision_id]
        if now == row:
            continue
        trimmed = now[:2] == row[:2] and now[3:] == row[3:] and abs(now[2] - end) < 1e-3 and row[2] > end
        if not trimmed:
            changes.append(f"{decision_id} changed")
    for decision_id in after:
        if decision_id not in before:
            changes.append(f"{decision_id} was added")
    return changes


def composition_checks(html: str, duration: float, music_required: bool = True) -> list[str]:
    errors = []
    videos = re.findall(r"<video\b[^>]*>", html)
    if any(" muted" not in tag for tag in videos):
        errors.append("a video element is not muted")
    narration = re.findall(r'<audio\s+id="narration"[^>]*>', html, flags=re.S)
    if len(narration) != 1:
        errors.append("the composition must have exactly one narration track")
    elif 'data-start="0"' not in narration[0] or f'data-duration="{duration}"' not in narration[0]:
        errors.append("narration does not span the lesson from 0")
    if music_required and 'id="music-bed"' not in html:
        errors.append("the music bed is missing")
    if 'id="corner-logo"' not in html:
        errors.append("the corner logo is missing")
    return errors


def _music_plan(row: dict) -> dict:
    return {
        "path": "assets/music.wav",
        "identity": row.get("title"),
        "identity_status": "identified",
        "source": "licensed",
        "gain_under_speech": UNDER_SPEECH,
        "gain_open": OPEN,
        "license": row.get("license"),
        "license_url": row.get("license_url"),
        "page_url": row.get("page_url"),
        "artist": row.get("artist") or "",
    }


def choose_music(store: StudioStore, record: dict, settings: dict | None, bridge: EngineBridge) -> tuple[Path | None, dict]:
    """The track this version asked for. Without settings it is the licensed default, as before."""
    source = (settings or {}).get("music", {}).get("source") or "default"
    if source == "library":
        from app.services.music import library_track

        track = library_track(settings["music"]["track_id"])
        return Path(track["file"]), {"title": track["title"], "license": track.get("license"), "license_url": track.get("license_url"), "page_url": track.get("page_url"), "artist": track.get("artist"), "kind": "library", "track_id": track["track_id"]}
    if source == "upload":
        meta = store.get_upload(settings["music"]["upload_id"], record["client_id"])
        if meta.get("kind") != "music" or meta.get("validation_status") != "valid":
            raise RenderError("The selected music upload is not a validated audio file.")
        return store.media_file(meta["upload_id"]), {"title": meta.get("filename"), "kind": "upload", "upload_id": meta["upload_id"]}
    if source == "none":
        return None, {"title": None, "kind": "none"}
    path, row = bridge.music()
    return path, {**row, "kind": "default"}


def studio_music_plan(row: dict, music: dict) -> tuple[dict, dict]:
    """Schema-valid music entry plus the fades. Uploaded music carries no claimed identity or licence."""
    volume = float(music["volume"])
    under = round(min(1.0, float(music["under_speech"]) * volume), 4)
    open_level = round(min(1.0, float(music["in_pauses"]) * volume), 4) if music["ducking"] else under
    if row["kind"] in {"default", "library"}:
        plan = _music_plan(row)
        plan = {key: value for key, value in plan.items() if value is not None}
    else:
        plan = {"path": "assets/music.wav", "identity": None, "identity_status": "unknown", "source": "default"}
    plan.update({"gain_under_speech": under, "gain_open": open_level})
    extras = {"fade_in": float(music["fade_in"]), "fade_out": 0.05 if music["into_outro"] else float(music["fade_out"]), "label": row.get("title") or "", "kind": row["kind"]}
    return plan, extras


def _cleanup(root: Path) -> None:
    for pattern in ("composition/assets/*.mp4", "composition/assets/*.wav", "verification/*.wav", "lesson.mp4"):
        for path in root.glob(pattern):
            path.unlink(missing_ok=True)
    shutil.rmtree(root / "_outro_work", ignore_errors=True)


def render_attempt(store: StudioStore, record: dict, render_id: str, bridge: EngineBridge, stage=None, plan_version_id: str | None = None) -> dict:
    """Render one validated plan into data/studio/renders/<session>/<render>/."""
    session_id = record["session_id"]
    root = store.renders / session_id / render_id
    root.mkdir(parents=True, exist_ok=False)
    stage = stage or (lambda _name, _message: None)
    steps: list[dict] = []

    def step(name: str, **detail) -> None:
        steps.append({"step": name, "at": _now(), **detail})
        _dump(root / "logs" / "steps.json", steps)

    saved = load_approved(store, record, plan_version_id)
    settings = saved.get("settings")
    plan_version = saved.get("version_id") or "legacy"
    title_decision = next((item for item in saved["engine_plan"].get("decisions") or [] if item.get("action") == "title"), {})
    title = {"text": title_decision.get("text", ""), "status": (saved.get("title") or {}).get("status") or ("legacy" if plan_version == "legacy" else "none")}
    _dump(root / "editing_plan_snapshot.json", saved)
    approved = saved["engine_plan"]
    errors = bridge.validate(approved)
    if errors:
        raise RenderError("The stored editing plan failed validation: " + "; ".join(errors[:5]))
    inputs = resolve_inputs(store, record, saved)

    raw_probe = _probe_media(bridge, inputs["raw"], "raw lesson")
    if _streams(raw_probe)[1] is None:
        raise RenderError("The raw lesson has no audio track, so there is no narration to keep.")
    raw_seconds = float(raw_probe["format"]["duration"])
    clip_seconds: dict[str, float] = {}
    for asset_id, clip in inputs["clips"].items():
        probe = _probe_media(bridge, clip["path"], f"B-roll clip {clip['filename']}")
        clip_seconds[asset_id] = float(probe["format"]["duration"])
        if _HEX64.match(clip["sha256"] or "") and _sha256(clip["path"]) != clip["sha256"]:
            raise RenderError(f"B-roll clip {clip['filename']} changed after the plan was approved.")
    step("inputs checked", clips=len(inputs["clips"]))

    logo = bridge.logo_path()
    outro = bridge.outro_path()
    music_path, music_row = choose_music(store, record, settings, bridge)
    manifest = {
        "session_id": session_id,
        "render_id": render_id,
        "mode": record["mode"],
        "started_at": _now(),
        "raw_lesson": {"upload_id": record["raw_video"]["upload_id"], "filename": record["raw_video"]["filename"], "sha256": _sha256(inputs["raw"]), "duration_seconds": round(raw_seconds, 3)},
        "broll": [
            {
                "asset_id": asset_id,
                "filename": clip["filename"],
                "origin": clip["origin"],
                "sha256": _sha256(clip["path"]),
                "duration_seconds": round(clip_seconds[asset_id], 3),
            }
            for asset_id, clip in inputs["clips"].items()
        ],
        "logo": {"filename": logo.name, "sha256": _sha256(logo)},
        "outro": {"filename": outro.name, "sha256": _sha256(outro)},
        "music": {"identity": music_row.get("title"), "kind": music_row.get("kind"), "license": music_row.get("license"), "page_url": music_row.get("page_url"), "sha256": _sha256(music_path) if music_path else None},
        "settings": settings,
        "renderer": "existing engine: hyperframes_adapter.compose, run_phase4._hyperframes (hyperframes@0.8.186), render.outro.append_outro",
        "plan_validation": (saved.get("studio") or {}).get("validation"),
        "plan_version_id": plan_version,
        "title": title,
    }
    _dump(root / "input_manifest.json", manifest)

    plan = copy.deepcopy(approved)
    duration = float(plan["input_media"]["duration_seconds"])
    interval = plan["audio"].get("narration_interval") or {}
    source_in = float(interval.get("source_in") or 0.0)
    source_out = float(interval.get("source_out") or source_in + duration)
    if duration <= 0 or source_in < 0 or source_out <= source_in or abs((source_out - source_in) - duration) > 0.25:
        raise RenderError("The approved narration interval is invalid.")
    if source_out > raw_seconds + 0.25:
        raise RenderError("The approved timeline is longer than the uploaded lesson.")
    cut = {"source_in": source_in, "source_out": source_out}
    grade = (plan.get("render") or {}).get("grade") or {}
    video_filter = grade.get("ffmpeg_video_filter") or None
    composition = root / "composition"
    assets = composition / "assets"
    assets.mkdir(parents=True)

    bridge.cut_teacher(inputs["raw"], cut, assets / "teacher.mp4", video_filter)
    bridge.cut_narration(inputs["raw"], cut, assets / "narration.wav")
    bridge.trim_logo(logo, assets / "logo.png")
    if music_path is not None:
        if settings:
            studio_prep.prepare_music(music_path, assets / "music.wav", duration, bridge.ffmpeg())
        else:
            bridge.trim_music(music_path, assets / "music.wav", duration)
    step("lesson media cut", source_in=source_in, source_out=source_out)

    teacher_video, _ = _streams(_probe_media(bridge, assets / "teacher.mp4", "cut lesson picture"))
    width, height = int(teacher_video["width"]), int(teacher_video["height"])
    portrait = height > width
    plan["input_media"]["video_path"] = "assets/teacher.mp4"
    plan["input_media"]["audio_path"] = "assets/narration.wav"
    plan["input_media"]["width"] = width
    plan["input_media"]["height"] = height
    plan["audio"]["narration_path"] = "assets/narration.wav"
    plan["audio"]["preserve_original"] = True
    plan["render"].update(CANVAS)
    plan["render"]["object_fit"] = "cover" if portrait else "contain"
    plan["render"]["corner_logo"] = "assets/logo.png"
    plan["audio"]["music"] = _music_plan(music_row)
    music_note = "The music bed is the licensed instrumental already used by the phase 5 renders, at 0.12 under speech and 0.28 in the open."
    music_extras: dict = {}
    if settings:
        if music_path is None:
            plan["audio"]["music"] = {"path": None, "identity": None, "identity_status": "unknown", "source": "default", "gain_under_speech": 0.0, "gain_open": 0.0}
            music_note = "No music bed: the user chose no music."
        else:
            plan["audio"]["music"], music_extras = studio_music_plan(music_row, settings["music"])
            music_note = f"Music: {music_row.get('title') or 'uploaded track'} ({music_row['kind']}), {plan['audio']['music']['gain_under_speech']} under speech and {plan['audio']['music']['gain_open']} in pauses."
    plan["limitations"] = [line for line in plan.get("limitations") or [] if not line.startswith("No music bed is used")] + [music_note]

    used = []
    broll_index = 0
    for decision in plan["decisions"]:
        if decision.get("action") != "replace_with_broll":
            continue
        asset_id = decision["asset_id"]
        clip = inputs["clips"][asset_id]
        placement = inputs["placements"][asset_id]
        clip_in = float(placement.get("source_in") or 0.0)
        play = float(decision["end"]) - float(decision["start"])
        approved_out = float(placement.get("source_out") if placement.get("source_out") is not None else clip_in + play)
        if clip_in < 0 or play <= 0:
            raise RenderError("A planned B-roll interval is invalid.")
        if clip_in + play > approved_out + 0.05:
            raise RenderError(f"The cutaway for {clip['filename']} would play past its approved source interval.")
        if clip_in + play > clip_seconds[asset_id] + 0.05:
            raise RenderError(f"The cutaway for {clip['filename']} is longer than the clip.")
        asset = {
            "local_path": str(clip["path"]),
            "file_name": f"broll-{broll_index:03d}.mp4",
            "kind": "video",
            "clip_in": clip_in,
            "source_window": [decision["start"], decision["end"]],
            "duration_seconds": clip_seconds[asset_id],
        }
        bridge.place_broll(asset, assets, video_filter)
        placed = assets / asset["file_name"]
        _v, placed_audio = _streams(_probe_media(bridge, placed, f"prepared cutaway {clip['filename']}"))
        if placed_audio is not None:
            raise RenderError("A prepared cutaway still has an audio track.")
        decision["asset_path"] = f"assets/{asset['file_name']}"
        decision["asset_kind"] = "video"
        if record["mode"] == "my_broll" and (decision.get("page_url") or decision.get("license")):
            raise RenderError("Mode B plans cannot reference external footage.")
        used.append({"decision_id": decision["id"], "asset_id": asset_id, "filename": clip["filename"], "origin": clip["origin"], "timeline": [decision["start"], decision["end"]], "source_interval": [round(clip_in, 3), round(clip_in + play, 3)], "audio": "removed"})
        broll_index += 1
    step("cutaways prepared", placements=used)

    narration_seconds = float(_probe_media_audio(bridge, assets / "narration.wav"))
    if narration_seconds < duration - 0.001:
        bridge.clip_to(plan, round(narration_seconds, 3))
        step("plan clipped to narration", narration_seconds=round(narration_seconds, 3))
    duration = float(plan["input_media"]["duration_seconds"])
    changes = timeline_changes(approved, plan)
    if changes:
        raise RenderError("The render copy would change the approved timeline: " + "; ".join(changes[:5]))
    errors = bridge.validate(plan)
    if errors:
        raise RenderError("The render-ready plan failed validation: " + "; ".join(errors[:5]))

    if settings:
        ffmpeg = bridge.ffmpeg()
        focus = studio_prep.estimate_focus(assets / "teacher.mp4", width, height, ffmpeg)
        backdrop = studio_prep.make_backdrop(assets / "teacher.mp4", assets / "teacher_backdrop.jpg", CANVAS["width"], CANVAS["height"], duration * 0.35, ffmpeg)
        sizes = {}
        for decision in plan["decisions"]:
            if decision.get("action") == "replace_with_broll" and decision.get("asset_path"):
                stream, _ = _streams(_probe_media(bridge, composition / decision["asset_path"], "prepared cutaway"))
                sizes[decision["asset_path"]] = (int(stream["width"]), int(stream["height"]))
        studio_layout = studio_prep.studio_block(settings, src_width=width, src_height=height, focus=focus, backdrop=f"assets/{backdrop.name}", broll_sizes=sizes)
        studio_layout["music"] = music_extras
        _dump(root / "studio_layout.json", studio_layout)
        step("studio layout measured", focus=focus, framing=settings["framing"], effects=sorted(studio_layout["effects"]))
    else:
        studio_layout = None

    stage("composing", "Writing the HyperFrames composition.")
    bridge.compose(plan, composition / "index.html", studio=studio_layout)
    _dump(root / "render_plan.json", plan)
    html = (composition / "index.html").read_text(encoding="utf-8")
    problems = composition_checks(html, plan["input_media"]["duration_seconds"], music_required=music_path is not None)
    if problems:
        raise RenderError("The composition failed its audio and branding checks: " + "; ".join(problems))
    step("composition written")

    stage("rendering", "HyperFrames is rendering the approved timeline.")
    lesson = root / "lesson.mp4"
    bridge.hyperframes(composition, lesson)
    if not lesson.is_file():
        raise RenderError("HyperFrames did not write a lesson video.")
    step("hyperframes finished")

    stage("verifying", "Checking the lesson render and its narration.")
    try:
        lesson_meta = output_metadata(bridge.probe(lesson), lesson)
    except EngineFailure as exc:
        raise RenderError("The rendered lesson is unreadable or corrupted.") from exc
    _check_canvas(lesson_meta, "rendered lesson")
    if abs(lesson_meta["duration_seconds"] - duration) > 0.5:
        raise RenderError("The rendered lesson length does not match the approved timeline.")
    lesson_audio = bridge.compare_audio(assets / "narration.wav", lesson, duration, root / "verification")
    audio = {
        "lesson": lesson_audio,
        "floor": CORRELATION_FLOOR,
        "basis": "Phase 19 renders measured 0.988 (VIDEO_1) and 0.9645 (VIDEO_2) with the same music bed.",
    }
    _dump(root / "audio_verification.json", audio)
    if lesson_audio["correlation"] < CORRELATION_FLOOR:
        raise RenderError(f"Narration correlation {lesson_audio['correlation']} is under {CORRELATION_FLOOR}.")
    step("lesson verified", correlation=lesson_audio["correlation"])

    stage("assembling_outro", "Appending the original outro and checking the finished file.")
    final = root / "final.mp4"
    if settings:
        tail = None
        if music_path is not None and settings["music"]["into_outro"]:
            tail = studio_prep.music_tail(
                music_path, assets / "music_tail.wav", duration, bridge.outro_seconds(outro),
                float(plan["audio"]["music"]["gain_open"]), float(settings["music"]["fade_out"]), bridge.ffmpeg(),
            )
        report = bridge.append_outro(lesson, outro, final, fill=settings["outro"]["fill"], audio=tail)
    else:
        report = bridge.append_outro(lesson, outro, final)
    report = {key: value for key, value in report.items() if key != "outro_path"}
    report["outro_filename"] = outro.name
    _dump(root / "outro.json", report)
    try:
        final_meta = output_metadata(bridge.probe(final), final)
    except EngineFailure as exc:
        raise RenderError("The finished video is unreadable or corrupted.") from exc
    _check_canvas(final_meta, "finished video")
    if abs(final_meta["duration_seconds"] - float(report["expected_duration"])) > 0.75:
        raise RenderError("The finished video length does not match the lesson plus the outro.")
    final_audio = bridge.compare_audio(assets / "narration.wav", final, duration, root / "verification")
    audio["final_lesson_portion"] = final_audio
    audio["status"] = "passed" if final_audio["correlation"] >= CORRELATION_FLOOR else "failed"
    _dump(root / "audio_verification.json", audio)
    if audio["status"] != "passed":
        raise RenderError(f"Narration correlation in the finished video is {final_audio['correlation']}.")
    step("final verified", correlation=final_audio["correlation"])

    stage("verifying", "Inspecting the finished video.")
    from app.services.rendering.quality import inspect

    quality = inspect(
        ffmpeg=bridge.ffmpeg(), final=final, final_meta=final_meta, lesson_seconds=duration, plan=plan,
        assets=assets, settings=settings, outro_report=report,
    )
    _dump(root / "quality_report.json", quality)
    if quality["status"] == "failed":
        problems = "; ".join(item["detail"] for item in quality["checks"] if item["status"] == "failed")
        raise RenderError(f"The finished video failed its quality checks: {problems[:300]}")
    step("quality inspected", status=quality["status"], warnings=quality["warnings"])

    result = {
        "render_id": render_id,
        "status": "completed",
        "completed_at": _now(),
        "filename": "final.mp4",
        "metadata": final_meta,
        "lesson_duration_seconds": lesson_meta["duration_seconds"],
        "audio_verification": {
            "status": "passed",
            "correlation": final_audio["correlation"],
            "lag_seconds": final_audio["lag_seconds"],
            "lesson_render_correlation": lesson_audio["correlation"],
            "floor": CORRELATION_FLOOR,
            "method": final_audio["method"],
        },
        "outro": {
            "status": "assembled",
            "duration_seconds": report.get("outro_duration"),
            "expected_duration": report.get("expected_duration"),
            "duration_delta": report.get("duration_delta"),
            "audio_policy": report.get("audio_policy"),
        },
        "approved_broll_placements": len(used),
        "quality": {"status": quality["status"], "failed": quality["failed"], "warnings": quality["warnings"], "checks": [{key: item[key] for key in ("name", "status", "detail")} for item in quality["checks"]]},
        "studio_settings": bool(settings),
        "plan_version_id": plan_version,
        "title": title,
        "mode": record["mode"],
    }
    _dump(root / "render_metadata.json", result)
    manifest["completed_at"] = _now()
    manifest["output"] = result
    _dump(root / "input_manifest.json", manifest)
    _cleanup(root)
    return result


def _probe_media_audio(bridge: EngineBridge, path: Path) -> float:
    try:
        probe = bridge.probe(path)
    except EngineFailure as exc:
        raise RenderError("The narration cut is unreadable.") from exc
    _video, audio = _streams(probe)
    duration = float((probe.get("format") or {}).get("duration") or 0)
    if audio is None or duration <= 0:
        raise RenderError("The narration cut has no audio.")
    return duration
