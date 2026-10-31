"""Phase 4 pass for VIDEO_1 and VIDEO_2.

prepare transcribes without translation and collects licensed candidates.
render builds a new composition from those files and the accept list.
Nothing under outputs/dr_shivin_evaluation or outputs/phase2 is written.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from assets.commons import AssetRejected  # noqa: E402
from assets.music_commons import accept_music_candidate, download_audio, search_instrumentals  # noqa: E402
from assets.video_commons import accept_video_candidate, download_video, search_videos  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from dataset_adapter.retrieve import retrieve  # noqa: E402
from editing_plan.phase2 import build_phase2_plan, clip_plan_to_duration, picture_fraction  # noqa: E402
from editing_plan.segment import choose_segment  # noqa: E402
from editing_plan.style_choice import choose_exemplar  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from transcription.captions import prepare_segment  # noqa: E402
from transcription.romanize import has_devanagari  # noqa: E402
from transcription.whisper_cpp import default_model, transcribe_wav  # noqa: E402
from treatment.grade import infer_treatment  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

RAW = ROOT.parent / "DR_SHIVIN_RAW"
OUT = ROOT / "outputs" / "dr_shivin_phase4"
CACHE = OUT / "asset_cache"
DEFAULTS = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
VIDEOS = (("VIDEO_1", "VIDEO_1.mp4"), ("VIDEO_2", "VIDEO_2.mp4"))
TERM_QUERIES = {
    "hospital": "hospital ward",
    "covid": "COVID-19 hospital",
    "ppe": "PPE hospital gown",
    "mask": "surgical mask",
    "gown": "medical gown",
    "river": "river",
    "mountain": "mountain landscape",
    "parliament": "parliament building",
    "glacier": "glacier",
    "forest": "forest",
    "vaccine": "vaccine",
    "ambulance": "ambulance",
    "monument": "monument",
    "temple": "temple",
}
MUSIC_QUERIES = {
    "serious": "dark ambient piano instrumental",
    "documentary": "strings instrumental",
    "natural": "acoustic guitar instrumental",
    "neutral": "piano instrumental",
}
PPE_STILL = {
    "title": "AsiaWorld-Expo makeshift hospital PPE gown-up area",
    "page_url": "https://commons.wikimedia.org/wiki/File:AsiaWorld-Expo_makeshift_hospital_PPE_Gown_up_area_20200801.png",
    "license": "CC BY 4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0",
    "artist": "China News Service",
    "mime": "image/png",
    "kind": "image",
    "match_terms": ["hospital", "covid", "ppe"],
    "local_path": str(ROOT / "outputs" / "dr_shivin_evaluation" / "asset_cache" / "ppe_hospital.png"),
    "note": "Still already on disk from the phase-3 Commons download. It is a fallback, not a video.",
}


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "prepare"
    if command == "prepare":
        prepare()
    elif command == "render":
        render()
    else:
        raise SystemExit("use prepare or render")
    return 0


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    transcripts = {}
    for video_id, filename in VIDEOS:
        source = RAW / filename
        work = OUT / video_id / "work"
        work.mkdir(parents=True, exist_ok=True)
        probe = _probe(source)
        manifest.append(_source_record(video_id, source, probe))
        _extract_audio(source, work / "speech_16k.wav")
        _sample_frames(source, work / "frames")
        if (work / "transcript.json").is_file() and "--retranscribe" not in sys.argv:
            transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
        else:
            transcript = _transcribe(work / "speech_16k.wav", work)
            _dump(work / "transcript.json", transcript)
        transcripts[video_id] = transcript
        print(video_id, "language", transcript.get("language"), "segments", len(transcript["segments"]), "devanagari", _any_devanagari(transcript))
    _dump(OUT / "source_manifest.json", manifest)
    candidates = _collect_candidates(transcripts)
    _dump(CACHE / "candidates.json", candidates)
    print("candidates", len(candidates))


def render() -> None:
    dataset = load_dataset()
    accepted = _accepted()
    observations = _observations()
    candidates = json.loads((CACHE / "candidates.json").read_text(encoding="utf-8")) if (CACHE / "candidates.json").is_file() else []
    for video_id, filename in VIDEOS:
        _render_one(dataset, video_id, RAW / filename, candidates, accepted, observations)
    print("phase 4 renders written")


def _render_one(dataset, video_id, source, candidates, accepted, observations) -> None:
    work = OUT / video_id / "work"
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    selection = _selection(transcript["segments"])
    probe = _probe(source)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    portrait = int(video["height"]) > int(video["width"])
    speech = _speech_text(transcript)
    visual_terms = (observations.get(video_id) or {}).get("terms") or []
    grade = infer_treatment(speech, visual_terms)
    assets, music = _chosen_media(video_id, speech, candidates, accepted, observations)
    composition = OUT / video_id / "composition"
    asset_dir = composition / "assets"
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True)
    video_filter = grade.get("ffmpeg_video_filter") if grade.get("applied") else None
    _cut_teacher(source, selection, asset_dir / "teacher.mp4", video_filter)
    _cut_audio(source, selection, asset_dir / "narration.wav")
    for asset in assets:
        _place_asset(asset, asset_dir, video_filter)
    music_plan = None
    if music:
        dest = asset_dir / "music.wav"
        _trim_music(Path(music["local_path"]), dest, selection["source_out"] - selection["source_in"])
        music_plan = {
            "path": "assets/music.wav",
            "identity": music.get("title"),
            "identity_status": "identified",
            "source": "licensed",
            "gain_under_speech": 0.12,
            "gain_open": 0.28,
            "license": music.get("license"),
            "license_url": music.get("license_url"),
            "page_url": music.get("page_url"),
            "artist": music.get("artist") or "",
        }
    query = " ".join(segment.get("original_text") or "" for segment in transcript["segments"])
    hit = choose_exemplar(dataset, query[:400], portrait=portrait)
    if not assets and hit.get("layout_group") == "split_teacher_and_graphic":
        fallback = retrieve(dataset, layout_group="bounded_card_with_title", trusted_only=True, limit=1)
        if fallback:
            hit = fallback[0]
    credit, license_name, license_url = _credit(assets, music)
    plan = build_phase2_plan(
        dataset,
        transcript=transcript,
        selection=selection,
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": _fps(video),
            "attribution_line": credit,
            "license": license_name,
            "license_url": license_url,
            "changes": "9:16 edit. Captions are source-language text. Speech was not replaced.",
        },
        assets=assets,
        exemplar_id=hit["reference_id"],
        plan_id=f"phase4-{video_id.lower()}",
        primary_category="lesson",
        secondary_categories=[grade["name"]],
        music=music_plan,
        grade=grade,
    )
    plan["render"]["object_fit"] = "cover" if portrait else "contain"
    heading = ((observations.get(video_id) or {}).get("heading") or "").strip()
    if heading:
        for decision in plan["decisions"]:
            if decision["action"] == "title":
                decision["text"] = heading[:42]
                decision["reason"] = "Title repeats the heading visible on the source picture. It is not an ASR guess."
                decision["source"] = "observed"
                decision["confidence"] = 0.8
    narration = _probe(asset_dir / "narration.wav")
    duration = round(float(narration["format"]["duration"]), 3)
    clip_plan_to_duration(plan, duration)
    plan["limitations"].append(
        f"Style exemplar {hit['reference_id']} is a layout example. Token overlap was not treated as topical similarity."
    )
    plan["limitations"].append(
        "Grade and B-roll queries are keyword rules. No multimodal model was configured or called."
    )
    if not _any_devanagari(transcript) and transcript.get("language") not in {"en", "english"}:
        plan["limitations"].append(
            "The recognizer did not return Devanagari. Urdu-script letters were transliterated and English words from the same non-translate pass were kept. whisper -tr was not used."
        )
    errors = validate_plan(plan)
    if errors:
        raise SystemExit(video_id + "\n" + "\n".join(errors))
    write_composition(plan, DEFAULTS, composition / "index.html")
    _dump(OUT / video_id / "editing_plan.json", plan)
    _dump(OUT / video_id / "transcript.json", transcript)
    _dump(OUT / video_id / "captions.json", _caption_export(plan))
    _dump(OUT / video_id / "selection.json", selection)
    share = picture_fraction(plan, "replace_with_broll")
    print(video_id, "broll", f"{share:.1%}", "grade", grade["name"], "music", bool(music_plan), "captions", sum(1 for item in plan["decisions"] if item["action"] == "caption"))
    output = OUT / video_id / f"{video_id}_phase4.mp4"
    _hyperframes(composition, output)
    _verify(video_id, asset_dir / "narration.wav", output, plan)


def _transcribe(wav: Path, work: Path) -> dict:
    larger = _larger_model()
    model = larger or default_model()
    first = transcribe_wav(wav, language="auto", translate=False, model=model, output_stem=work / "asr_auto", threads=4)
    text = " ".join(segment.get("original_text") or "" for segment in first["segments"])
    if first.get("language") in {"en", "english"} or has_devanagari(text) or larger is None:
        return first
    forced = first.get("language") if first.get("language") not in {None, "", "und"} else "hi"
    return transcribe_wav(
        wav,
        language=forced,
        translate=False,
        model=larger,
        output_stem=work / "asr_source",
        threads=4,
    )


def _larger_model() -> Path | None:
    root = ROOT / "tools" / "whisper"
    for name in ("ggml-small.bin", "ggml-base.bin"):
        path = root / name
        if path.is_file() and path.stat().st_size > 20_000_000:
            return path
    return None


def _collect_candidates(transcripts: dict) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    found_terms = set()
    music_queries = set()
    for transcript in transcripts.values():
        speech = _speech_text(transcript)
        found_terms.update(_terms(speech))
        music_queries.add(_music_query(speech, infer_treatment(speech)["name"]))
    rows = []
    downloads = 0
    for term in sorted(found_terms):
        query = TERM_QUERIES[term]
        try:
            hits = search_videos(query, limit=5)
        except Exception as error:  # noqa: BLE001 - the search failure is recorded and the edit continues
            rows.append({"query": query, "rejected": f"search failed: {error}"})
            continue
        for hit in hits:
            reason = accept_video_candidate(hit, query, speech=term)
            if reason:
                rows.append({"query": query, "title": hit.get("title"), "page_url": hit.get("page_url"), "rejected": reason, "license": hit.get("license")})
                continue
            if downloads >= 3:
                rows.append({"query": query, "title": hit.get("title"), "page_url": hit.get("page_url"), "rejected": "download cap reached"})
                continue
            slug = _slug(hit.get("title") or "clip")
            dest = CACHE / f"{slug}{Path(hit['download_url']).suffix or '.mp4'}"
            try:
                download_video(hit["download_url"], dest)
            except (AssetRejected, OSError, Exception) as error:  # noqa: BLE001
                rows.append({"query": query, "title": hit.get("title"), "page_url": hit.get("page_url"), "rejected": f"download failed: {error}"})
                continue
            downloads += 1
            record = dict(hit)
            record.update({"query": query, "match_terms": [term], "kind": "video", "local_path": str(dest), "rejected": None})
            _describe_media(record, dest)
            rows.append(record)
    if {"hospital", "covid", "ppe"} & found_terms and Path(PPE_STILL["local_path"]).is_file():
        rows.append(dict(PPE_STILL))
    for query in music_queries:
        if query:
            rows.extend(_music_candidates(query))
    return rows


def _music_candidates(query: str) -> list[dict]:
    rows = []
    try:
        hits = search_instrumentals(query, limit=6)
    except Exception as error:  # noqa: BLE001
        return [{"query": query, "kind": "audio", "rejected": f"search failed: {error}"}]
    kept = 0
    for hit in hits:
        reason = accept_music_candidate(hit)
        if reason:
            rows.append({"query": query, "kind": "audio", "title": hit.get("title"), "page_url": hit.get("page_url"), "license": hit.get("license"), "rejected": reason})
            continue
        if kept >= 1:
            rows.append({"query": query, "kind": "audio", "title": hit.get("title"), "page_url": hit.get("page_url"), "rejected": "one instrumental is enough"})
            continue
        dest = CACHE / f"{_slug(hit.get('title') or 'music')}{Path(hit['download_url']).suffix or '.ogg'}"
        try:
            download_audio(hit["download_url"], dest)
        except (AssetRejected, OSError, Exception) as error:  # noqa: BLE001
            rows.append({"query": query, "kind": "audio", "title": hit.get("title"), "rejected": f"download failed: {error}"})
            continue
        record = dict(hit)
        record.update({"query": query, "kind": "audio", "local_path": str(dest), "rejected": None})
        _describe_media(record, dest)
        rows.append(record)
        kept += 1
    if not any(row.get("kind") == "audio" and not row.get("rejected") for row in rows):
        rows.append({"query": query, "kind": "audio", "rejected": "no licensed instrumental matched this query"})
    return rows


def _chosen_media(video_id, speech, candidates, accepted, observations):
    blocked = set()
    obs = observations.get(video_id) or {}
    if obs.get("has_writing"):
        blocked.update(term.lower() for term in obs.get("terms") or [])
    terms = [term for term in _terms(speech) if term not in blocked]
    assets = []
    music = None
    grade_name = infer_treatment(speech, list(obs.get("terms") or [])).get("name")
    wanted_music = _music_query(speech, grade_name)
    for row in candidates:
        if row.get("rejected") or row.get("page_url") not in accepted:
            continue
        if row.get("kind") == "audio":
            if music is None and wanted_music and row.get("query") == wanted_music:
                music = row
            continue
        if not any(term in terms for term in row.get("match_terms") or []):
            continue
        if not row.get("local_path") or not Path(row["local_path"]).is_file():
            continue
        assets.append(_asset_from_row(row))
    return assets, music


def _asset_from_row(row: dict) -> dict:
    kind = row.get("kind") or "video"
    suffix = Path(row["local_path"]).suffix.lower()
    window = row.get("source_window") or []
    stamp = f"-{int(float(window[0]) * 10)}" if len(window) == 2 else ""
    name = f"broll_{_slug(row.get('title') or 'clip')}{stamp}{suffix}"
    return {
        "id": (_slug(row.get("title") or "clip")[:32] + stamp),
        "path": f"assets/{name}",
        "kind": "image" if kind == "image" or suffix in {".png", ".jpg", ".jpeg", ".webp"} else "video",
        "match_terms": row.get("match_terms") or [],
        "reason": row.get("reason") or row.get("note") or f"Commons file matched {', '.join(row.get('match_terms') or [])}.",
        "page_url": row.get("page_url"),
        "license": row.get("license"),
        "license_url": row.get("license_url"),
        "artist": row.get("artist"),
        "duration_seconds": row.get("duration_seconds"),
        "source_window": row.get("source_window"),
        "clip_in": row.get("clip_in"),
        "graphic": row.get("graphic") or "",
        "local_path": row["local_path"],
        "confidence": 0.55,
        "file_name": name,
    }


def clip_take_seconds(asset: dict) -> float | None:
    """Seconds to keep after clip_in. Never longer than the source that remains."""
    clip_in = asset.get("clip_in")
    if not isinstance(clip_in, (int, float)) or float(clip_in) <= 0:
        return None
    needed = 8.0
    window = asset.get("source_window")
    if isinstance(window, (list, tuple)) and len(window) == 2:
        span = float(window[1]) - float(window[0])
        if span > 0.4:
            needed = span + 0.15
    duration = asset.get("duration_seconds")
    if isinstance(duration, (int, float)):
        remaining = float(duration) - float(clip_in)
        needed = min(needed, max(0.5, remaining - 0.05))
    return round(needed, 3)


def _place_asset(asset: dict, asset_dir: Path, video_filter: str | None) -> None:
    src = Path(asset["local_path"])
    dest = asset_dir / asset["file_name"]
    if asset["kind"] != "image" and dest.suffix.lower() not in {".mp4", ".m4v", ".mov"}:
        dest = dest.with_suffix(".mp4")
        asset["file_name"] = dest.name
        asset["path"] = f"assets/{dest.name}"
    if asset["kind"] == "image":
        if video_filter:
            _run([_tool("ffmpeg"), "-y", "-i", str(src), "-vf", video_filter, str(dest)])
        else:
            shutil.copyfile(src, dest)
        return
    command = [_tool("ffmpeg"), "-y"]
    clip_in = asset.get("clip_in")
    take = clip_take_seconds(asset)
    if isinstance(clip_in, (int, float)) and float(clip_in) > 0:
        command += ["-ss", f"{float(clip_in):.3f}"]
    command += ["-i", str(src), "-an"]
    if take is not None:
        command += ["-t", f"{take:.3f}"]
    if video_filter:
        command += ["-vf", video_filter]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-g", "30", "-movflags", "+faststart", str(dest)]
    _run(command)


def _cut_teacher(source: Path, selection: dict, dest: Path, video_filter: str | None) -> None:
    command = [_tool("ffmpeg"), "-y", "-i", str(source), "-ss", f"{selection['source_in']:.3f}", "-to", f"{selection['source_out']:.3f}", "-map", "0:v:0", "-an"]
    if video_filter:
        command += ["-vf", video_filter]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-g", "30", "-movflags", "+faststart", str(dest)]
    _run(command)


def _cut_audio(source: Path, selection: dict, dest: Path) -> None:
    _run([
        _tool("ffmpeg"), "-y", "-i", str(source),
        "-ss", f"{selection['source_in']:.3f}", "-to", f"{selection['source_out']:.3f}",
        "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dest),
    ])


def _trim_music(source: Path, dest: Path, duration: float) -> None:
    _run([
        _tool("ffmpeg"), "-y", "-i", str(source), "-t", f"{duration:.3f}",
        "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dest),
    ])


def _describe_media(record: dict, path: Path) -> None:
    try:
        probe = _probe(path)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
        record["rejected"] = f"ffprobe failed: {error}"
        return
    record["bytes"] = path.stat().st_size
    duration = (probe.get("format") or {}).get("duration")
    if duration:
        record["duration_seconds"] = round(float(duration), 3)
    video = next((stream for stream in probe.get("streams") or [] if stream.get("codec_type") == "video"), None)
    if video:
        record["width"] = video.get("width")
        record["height"] = video.get("height")
        frame = path.with_suffix(".jpg")
        grabbed = subprocess.run(
            [_tool("ffmpeg"), "-y", "-ss", "0.5", "-i", str(path), "-frames:v", "1", str(frame)],
            capture_output=True,
        )
        if grabbed.returncode == 0 and frame.is_file():
            record["frame"] = str(frame)
    if record.get("kind") == "video" and (not record.get("duration_seconds") or record["duration_seconds"] < 2):
        record["rejected"] = "video is shorter than 2 seconds"


def _hyperframes(composition: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    npx_js = Path(node).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
    npx = [node, str(npx_js), "--yes", "hyperframes@0.8.186"]
    lint = subprocess.run(npx + ["lint"], cwd=composition, capture_output=True, text=True)
    (composition / "lint.txt").write_text((lint.stdout or "") + (lint.stderr or ""), encoding="utf-8")
    if lint.returncode != 0:
        raise SystemExit(f"lint failed for {composition}")
    rendered = subprocess.run(
        npx + ["render", "--quality", "draft", "--workers", "1", "--output", str(output)],
        cwd=composition,
        capture_output=True,
        text=True,
    )
    (composition / "render.txt").write_text((rendered.stdout or "") + (rendered.stderr or ""), encoding="utf-8")
    if rendered.returncode != 0 or not output.is_file():
        raise SystemExit(f"render failed for {composition}")


def _verify(video_id: str, narration: Path, output: Path, plan: dict) -> None:
    probe = _probe(output)
    rendered = OUT / video_id / "rendered_audio.wav"
    _run([_tool("ffmpeg"), "-y", "-i", str(output), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rendered)])
    reference = OUT / video_id / "narration_16k.wav"
    _run([_tool("ffmpeg"), "-y", "-i", str(narration), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(reference)])
    sys.path.insert(0, str(ROOT / "src"))
    from validation.audio_compare import compare_wavs

    audio = compare_wavs(reference, rendered)
    snaps = OUT / video_id / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    times = [0.4]
    for decision in plan["decisions"]:
        if decision["action"] in {"replace_with_broll", "show_teacher", "caption"}:
            times.append(round(decision["start"] + 0.3, 2))
    duration = plan["input_media"]["duration_seconds"]
    times.append(round(max(0.2, duration - 0.4), 2))
    seen = set()
    for stamp in times:
        if stamp in seen or stamp >= duration:
            continue
        seen.add(stamp)
        _run([_tool("ffmpeg"), "-y", "-ss", str(stamp), "-i", str(output), "-frames:v", "1", "-update", "1", str(snaps / f"t{stamp:.1f}.jpg")])
    _dump(OUT / video_id / "validation.json", {"probe": probe, "audio": audio, "broll_fraction": picture_fraction(plan, "replace_with_broll")})
    print(video_id, "audio", audio)


def _selection(segments: list[dict]) -> dict:
    try:
        return choose_segment(segments)
    except ValueError:
        start = segments[0]["start"]
        end = segments[-1]["end"]
        return {
            "source_in": round(start, 3),
            "source_out": round(end, 3),
            "duration": round(end - start, 3),
            "text": " ".join(segment.get("text") or "" for segment in segments),
            "reason": "No 30–60 second boundary run was available, so the spoken span that exists was used.",
        }


def _speech_text(transcript: dict) -> str:
    parts = []
    for segment in transcript["segments"]:
        prepared = prepare_segment(segment, transcript.get("language") or "und")
        parts.append(prepared["romanized_text"] or prepared["original_text"])
    return " ".join(parts)


def _music_query(speech: str, grade_name: str) -> str | None:
    if grade_name in MUSIC_QUERIES:
        return MUSIC_QUERIES[grade_name]
    lowered = speech.lower()
    if any(word in lowered for word in ("exam", "upsc", "syllabus", "guidance", "mistake", "prepare", "youtube")):
        return "soft piano instrumental"
    return None


def _terms(text: str) -> list[str]:
    import re

    lowered = text.lower().replace("-", " ")
    return [term for term in TERM_QUERIES if re.search(rf"\b{re.escape(term)}\b", lowered)]


def _caption_export(plan: dict) -> list[dict]:
    return [
        {"id": item["id"], "start": item["start"], "end": item["end"], "text": item.get("text")}
        for item in plan["decisions"]
        if item["action"] == "caption"
    ]


def _credit(assets: list[dict], music: dict | None) -> tuple[str, str, str]:
    text = "Dr. Shivin. Local recording."
    license_name = "not stated in the file"
    license_url = "not-stated-in-file"
    for asset in assets:
        text += f" Visual: {asset.get('artist') or 'unknown'}, {asset.get('license')}."
        license_name = asset.get("license") or license_name
        license_url = asset.get("license_url") or license_url
    if music:
        text += f" Music: {music.get('artist') or 'unknown'}, {music.get('license')}."
        license_name = music.get("license") or license_name
        license_url = music.get("license_url") or license_url
    return text, license_name, license_url


def _any_devanagari(transcript: dict) -> bool:
    return any(has_devanagari(segment.get("original_text") or "") for segment in transcript["segments"])


def _source_record(video_id: str, source: Path, probe: dict) -> dict:
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next((stream for stream in probe["streams"] if stream["codec_type"] == "audio"), {})
    return {
        "id": video_id,
        "path": str(source),
        "bytes": source.stat().st_size,
        "sha256": _sha256(source),
        "duration": float(probe["format"]["duration"]),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("avg_frame_rate"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "sample_rate": audio.get("sample_rate"),
        "channels": audio.get("channels"),
    }


def _accepted() -> set[str]:
    path = OUT / "accepted.json"
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload.get("page_urls") or [])


def _observations() -> dict:
    path = OUT / "visual_observations.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_frames(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _run([_tool("ffmpeg"), "-y", "-i", str(source), "-vf", "fps=1/8", "-frames:v", "8", str(dest / "f%02d.jpg")])


def _extract_audio(source: Path, dest: Path) -> None:
    _run([_tool("ffmpeg"), "-y", "-i", str(source), "-ac", "1", "-ar", "16000", "-vn", str(dest)])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fps(video: dict) -> float:
    rate = video.get("avg_frame_rate") or "30/1"
    num, den = rate.split("/")
    return round(float(num) / float(den), 3) if float(den) else 30.0


def _slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:60] or "asset"


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def _run(command: list[str], cwd: Path | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode}): {' '.join(command[:6])}")


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if local.is_file():
        return str(local)
    raise RuntimeError(name)


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
