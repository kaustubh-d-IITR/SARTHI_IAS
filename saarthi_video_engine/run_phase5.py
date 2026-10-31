"""Phase 5 pass for VIDEO_1 and VIDEO_2.

prepare transcribes, merges a non-loop second pass, and searches licensed video.
render builds a new composition, overlays the supplied logo, and appends the outro.
Phase 4 files are read and not rewritten.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import run_phase4 as phase4  # noqa: E402
from analysis.uniqueness import asset_identity  # noqa: E402
from assets.concepts import visual_queries  # noqa: E402
from assets.video_commons import accept_video_candidate, download_video  # noqa: E402
from assets.video_commons import search_videos  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from dataset_adapter.retrieve import retrieve  # noqa: E402
from editing_plan.phase2 import build_phase2_plan, clip_plan_to_duration, picture_fraction  # noqa: E402
from editing_plan.style_choice import choose_exemplar  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from render.outro import append_outro, tool  # noqa: E402
from transcription.captions import is_recognition_loop, prepare_segment  # noqa: E402
from transcription.hinglish import clause_for_segment, merge_passes  # noqa: E402
from transcription.whisper_cpp import transcript_from_payload  # noqa: E402
from treatment.grade import infer_treatment  # noqa: E402
from validation.audio_compare import compare_wavs  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

RAW = ROOT.parent / "DR_SHIVIN_RAW"
OUT = ROOT / "outputs" / "dr_shivin_phase5"
CACHE = OUT / "asset_cache"
PHASE4 = ROOT / "outputs" / "dr_shivin_phase4"
DEFAULTS = json.loads((ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8"))
VIDEOS = (("VIDEO_1", "VIDEO_1.mp4"), ("VIDEO_2", "VIDEO_2.mp4"))
MUSIC_PAGE = "https://commons.wikimedia.org/wiki/File%3AInspire_Action_by_Yuri_Megis.ogg"
TITLE_OVERRIDES: dict[str, str] = {}
FINAL_SUFFIX = "phase5"
MATCH_REFERENCE_LOOK = False


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
    outro = _find_outro()
    logo = _find_logo()
    phase4._dump(OUT / "brand.json", {"outro": str(outro), "logo": str(logo)})
    if not (OUT / "visual_observations.json").is_file():
        source = PHASE4 / "visual_observations.json"
        if source.is_file():
            shutil.copyfile(source, OUT / "visual_observations.json")
    transcripts = {}
    manifest = []
    for video_id, filename in VIDEOS:
        source = RAW / filename
        work = OUT / video_id / "work"
        work.mkdir(parents=True, exist_ok=True)
        probe = phase4._probe(source)
        manifest.append(phase4._source_record(video_id, source, probe))
        if not (work / "speech_16k.wav").is_file():
            phase4._extract_audio(source, work / "speech_16k.wav")
        phase4._sample_frames(source, work / "frames")
        transcript = _load_transcript(video_id, work)
        transcripts[video_id] = transcript
        phase4._dump(work / "transcript.json", transcript)
        review = [_review_row(segment, transcript.get("language") or "hi") for segment in transcript["segments"]]
        phase4._dump(OUT / video_id / "caption_review.json", review)
        print(video_id, "segments", len(transcript["segments"]), "captions", sum(1 for row in review if row["caption"]))
    phase4._dump(OUT / "source_manifest.json", manifest)
    candidates = _collect_candidates(transcripts)
    candidates.extend(_music_record())
    phase4._dump(CACHE / "candidates.json", candidates)
    print("candidates", len(candidates))


def render() -> None:
    dataset = load_dataset()
    accepted = _accepted()
    observations = json.loads((OUT / "visual_observations.json").read_text(encoding="utf-8"))
    candidates = json.loads((CACHE / "candidates.json").read_text(encoding="utf-8"))
    outro = _find_outro()
    logo = _find_logo()
    for video_id, filename in VIDEOS:
        _render_one(dataset, video_id, RAW / filename, candidates, accepted, observations, outro, logo)
    print(f"{FINAL_SUFFIX} renders written")


def _render_one(dataset, video_id, source, candidates, accepted, observations, outro, logo) -> None:
    work = OUT / video_id / "work"
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    selection = phase4._selection(transcript["segments"])
    probe = phase4._probe(source)
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    portrait = int(video["height"]) > int(video["width"])
    speech = _speech_text(transcript)
    visual_terms = (observations.get(video_id) or {}).get("terms") or []
    grade = infer_treatment(speech, visual_terms)
    if MATCH_REFERENCE_LOOK:
        from treatment.reference_look import lesson_grade

        grade = lesson_grade(float(selection["source_out"]) - float(selection["source_in"]))
    assets, music = _chosen_media(video_id, speech, candidates, accepted, observations)
    offset = float(selection["source_in"])
    for asset in assets:
        window = asset.get("source_window")
        if isinstance(window, (list, tuple)) and len(window) == 2:
            asset["window"] = [round(float(window[0]) - offset, 3), round(float(window[1]) - offset, 3)]
    composition = OUT / video_id / "composition"
    asset_dir = composition / "assets"
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True)
    _trim_logo(logo, asset_dir / "logo.png")
    video_filter = grade.get("ffmpeg_video_filter") if grade.get("applied") else None
    phase4._cut_teacher(source, selection, asset_dir / "teacher.mp4", video_filter)
    _remove_letterbox(asset_dir / "teacher.mp4")
    _paint_card_fill(asset_dir / "teacher.mp4")
    phase4._cut_audio(source, selection, asset_dir / "narration.wav")
    for asset in assets:
        phase4._place_asset(asset, asset_dir, video_filter)
    music_plan = None
    if music:
        phase4._trim_music(Path(music["local_path"]), asset_dir / "music.wav", selection["source_out"] - selection["source_in"])
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
    credit, license_name, license_url = phase4._credit(assets, music)
    plan = build_phase2_plan(
        dataset,
        transcript=transcript,
        selection=selection,
        media={"teacher": "assets/teacher.mp4", "narration": "assets/narration.wav"},
        source={
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": phase4._fps(video),
            "attribution_line": credit,
            "license": license_name,
            "license_url": license_url,
            "changes": "9:16 edit. The supplied logo sits on the lesson. The supplied outro is appended after it.",
        },
        assets=assets,
        exemplar_id=hit["reference_id"],
        plan_id=f"phase5-{video_id.lower()}",
        primary_category="lesson",
        secondary_categories=[grade["name"]],
        music=music_plan,
        grade=grade,
    )
    plan["render"]["object_fit"] = "cover" if portrait else "contain"
    plan["render"]["corner_logo"] = "assets/logo.png"
    heading = ((observations.get(video_id) or {}).get("heading") or "").strip()
    override = TITLE_OVERRIDES.get(video_id)
    if override or heading:
        for decision in plan["decisions"]:
            if decision["action"] == "title":
                if override:
                    decision["text"] = override
                    decision["reason"] = (
                        "Topic title from board text or the reference short for this lesson. "
                        "Yellow words are the topic. It is not a fragment of a faulty transcript."
                    )
                else:
                    decision["text"] = heading[:42]
                    decision["reason"] = "Title repeats the heading visible on the source picture. It is not an ASR guess."
                decision["source"] = "observed"
                decision["confidence"] = 0.8
    narration = phase4._probe(asset_dir / "narration.wav")
    duration = round(float(narration["format"]["duration"]), 3)
    teacher_probe = phase4._probe(asset_dir / "teacher.mp4")
    teacher_video = next(stream for stream in teacher_probe["streams"] if stream["codec_type"] == "video")
    plan["input_media"]["width"] = int(teacher_video["width"])
    plan["input_media"]["height"] = int(teacher_video["height"])
    clip_plan_to_duration(plan, duration)
    plan["limitations"].append("The corner logo is the file supplied in the LOGO folder. It is not drawn on the outro.")
    plan["limitations"].append(
        "A corner logo uses the reference red card even when the matched dataset family is full bleed."
    )
    if transcript.get("method") == "elevenlabs":
        plan["limitations"].append(
            f"Speech is {transcript.get('model')} from {transcript.get('endpoint')}. "
            "The wording is the provider text. logprob is stored and a confidence score was not invented."
        )
    else:
        plan["limitations"].append(
            "No cloud ASR key was configured. Hindi text is local whisper.cpp ggml-small, "
            "with the base pass used where the small pass looped."
        )
    errors = validate_plan(plan)
    if errors:
        raise SystemExit(video_id + "\n" + "\n".join(errors))
    write_composition(plan, DEFAULTS, composition / "index.html")
    phase4._dump(OUT / video_id / "editing_plan.json", plan)
    phase4._dump(OUT / video_id / "captions.json", phase4._caption_export(plan))
    lesson = OUT / video_id / f"{video_id}_lesson.mp4"
    final = OUT / video_id / f"{video_id}_{FINAL_SUFFIX}.mp4"
    phase4._hyperframes(composition, lesson)
    report = append_outro(lesson, outro, final, ffmpeg=tool("ffmpeg"), ffprobe=tool("ffprobe"))
    report["broll_fraction"] = picture_fraction(plan, "replace_with_broll")
    report["broll_seconds"] = round(report["broll_fraction"] * duration, 3)
    report["lesson_plan_duration"] = duration
    phase4._dump(OUT / video_id / "outro.json", report)
    _verify(video_id, asset_dir / "narration.wav", final, plan, report)
    print(video_id, "broll", f"{report['broll_fraction']:.1%}", "final", report["final_duration"])


def _load_transcript(video_id: str, work: Path) -> dict:
    small_path = work / "asr_small.json"
    if not small_path.is_file():
        raise SystemExit(f"missing {small_path}. Transcribe before prepare.")
    payload = json.loads(small_path.read_bytes().decode("utf-8", "replace"))
    primary = transcript_from_payload(
        payload,
        requested_language="hi",
        translate=False,
        model_name="ggml-small.bin",
        provenance="local whisper-cli, model ggml-small.bin, language hi, max-context 0, translate=False",
    )
    secondary = _phase4_transcript(video_id)
    chosen = merge_passes(primary["segments"], secondary.get("segments") or [], is_recognition_loop)
    other = {"primary": secondary.get("segments") or [], "secondary": primary["segments"]}
    for segment in chosen:
        segment["english_clause"] = clause_for_segment(segment, other.get(segment.get("asr_pass"), []))
    return {
        "language": "hi",
        "language_source": "configured",
        "translated_to_english": False,
        "method": "whisper.cpp",
        "model": "ggml-small.bin+ggml-base.bin",
        "provenance": primary["provenance"] + " Looping spans were replaced from the ggml-base non-translate pass when that pass was not itself a loop.",
        "segments": chosen,
    }


def _phase4_transcript(video_id: str) -> dict:
    path = PHASE4 / video_id / "transcript.json"
    if not path.is_file():
        return {"segments": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _review_row(segment: dict, language: str) -> dict:
    prepared = prepare_segment(segment, language)
    notes = prepared.get("normalization_notes") or []
    if prepared["caption_text"]:
        kind = "automatic" if notes else "unchanged"
    else:
        kind = "withheld"
    return {
        "start": segment.get("start"),
        "end": segment.get("end"),
        "recognizer_output": prepared["original_text"],
        "romanized_text": prepared["romanized_text"],
        "caption": prepared["caption_text"],
        "confidence": segment.get("confidence"),
        "flags": prepared["flags"],
        "correction": kind,
        "verification": "not_listened",
        "reason": "; ".join(notes) if notes else ("recognition loop or long uncertain span withheld" if not prepared["caption_text"] else "shown as recognized after transliteration"),
        "asr_pass": segment.get("asr_pass"),
    }


def _collect_candidates(transcripts: dict) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = []
    downloads = 0
    seen_queries = set()
    for video_id, transcript in transcripts.items():
        speech = _speech_text(transcript)
        for item in visual_queries(speech):
            if item["action"] == "skip":
                rows.append({"video_id": video_id, "kind": "video", "term": item["term"], "rejected": item["reason"]})
                continue
            query = item["query"]
            if query in seen_queries:
                continue
            seen_queries.add(query)
            queries = [query]
            if query == "newspaper":
                queries.append("reading newspaper")
            hits = []
            try:
                for attempt in queries:
                    hits.extend(search_videos(attempt, limit=5))
            except Exception as error:  # noqa: BLE001
                rows.append({"video_id": video_id, "query": query, "kind": "video", "rejected": f"search failed: {error}"})
                continue
            if not hits:
                rows.append({"video_id": video_id, "query": query, "kind": "video", "rejected": "Commons returned no video for this query"})
            for hit in hits:
                reason = accept_video_candidate(hit, query, speech=speech)
                if reason:
                    rows.append({"video_id": video_id, "query": query, "kind": "video", "title": hit.get("title"), "page_url": hit.get("page_url"), "license": hit.get("license"), "rejected": reason})
                    continue
                if downloads >= 4:
                    rows.append({"video_id": video_id, "query": query, "kind": "video", "title": hit.get("title"), "page_url": hit.get("page_url"), "rejected": "download cap reached"})
                    continue
                suffix = Path(urllib.parse.urlparse(hit["download_url"]).path).suffix.lower() or ".mp4"
                dest = CACHE / f"{phase4._slug(hit.get('title') or 'clip')}{suffix}"
                try:
                    download_video(hit["download_url"], dest)
                except Exception as error:  # noqa: BLE001
                    rows.append({"video_id": video_id, "query": query, "title": hit.get("title"), "rejected": f"download failed: {error}"})
                    continue
                downloads += 1
                record = dict(hit)
                record.update({
                    "video_id": video_id,
                    "query": query,
                    "match_terms": [item["term"]],
                    "kind": "video",
                    "local_path": str(dest),
                    "rejected": None,
                    "reason": f"Commons video matched the spoken word {item['term']}.",
                })
                phase4._describe_media(record, dest)
                rows.append(record)
    return rows


def _music_record() -> list[dict]:
    path = PHASE4 / "asset_cache" / "candidates.json"
    if not path.is_file():
        return [{"kind": "audio", "rejected": "phase 4 music record is missing"}]
    rows = json.loads(path.read_text(encoding="utf-8"))
    kept = []
    for row in rows:
        if row.get("kind") != "audio" or row.get("rejected") or row.get("page_url") != MUSIC_PAGE:
            continue
        source = Path(row["local_path"])
        if not source.is_file():
            kept.append({"kind": "audio", "rejected": "phase 4 music file is missing on disk"})
            continue
        dest = CACHE / "inspire-action-yuri-megis.ogg"
        CACHE.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            shutil.copyfile(source, dest)
        copied = dict(row)
        copied["local_path"] = str(dest)
        copied["reused_from"] = "phase 4 licensed instrumental; not a second download"
        kept.append(copied)
    return kept or [{"kind": "audio", "rejected": "the phase 4 instrumental could not be reused"}]


def _chosen_media(video_id, speech, candidates, accepted, observations):
    blocked = set()
    obs = observations.get(video_id) or {}
    if obs.get("has_writing"):
        blocked.update(term.lower() for term in obs.get("terms") or [])
    assets = []
    music = None
    used_clips: set[str] = set()
    for row in candidates:
        if row.get("rejected") or row.get("page_url") not in accepted:
            continue
        if row.get("kind") == "audio":
            if music is None:
                music = row
            continue
        if row.get("video_id") not in {None, video_id}:
            continue
        terms = [term.lower() for term in row.get("match_terms") or []]
        if any(term in blocked for term in terms):
            continue
        if not any(term in speech.lower() for term in terms):
            continue
        if not row.get("local_path") or not Path(row["local_path"]).is_file():
            continue
        identity = asset_identity(row)
        if identity and identity in used_clips:
            continue
        if identity:
            used_clips.add(identity)
        assets.append(phase4._asset_from_row(row))
    return assets, music


def _speech_text(transcript: dict) -> str:
    parts = []
    for segment in transcript["segments"]:
        prepared = prepare_segment(segment, transcript.get("language") or "hi")
        parts.append(prepared["caption_text"] or prepared["romanized_text"] or "")
    return " ".join(parts)


def _trim_logo(source: Path, dest: Path) -> None:
    from PIL import Image

    image = Image.open(source).convert("RGBA")
    alpha = image.getchannel("A")
    box = alpha.point(lambda value: 255 if value > 10 else 0).getbbox()
    if not box:
        image.save(dest)
        return
    left, top, right, bottom = box
    left = max(0, left - 8)
    top = max(0, top - 8)
    right = min(image.width, right + 8)
    bottom = min(image.height, bottom + 8)
    cropped = image.crop((left, top, right, bottom))
    if max(cropped.size) > 192:
        cropped.thumbnail((192, 192), Image.Resampling.LANCZOS)
    cropped.save(dest)


def _remove_letterbox(path: Path) -> None:
    """Crop stable black bars out of the lesson file. Real picture is left untouched."""
    import re

    detected = subprocess.run(
        [tool("ffmpeg"), "-i", str(path), "-vf", "cropdetect=limit=24:round=2:reset=30", "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", detected.stderr or "")
    if not crops:
        return
    parsed = [tuple(int(value) for value in crop) for crop in crops]
    parsed.sort(key=lambda item: item[1])
    width, height, x, y = parsed[len(parsed) // 2]
    probe = phase4._probe(path)
    stream = next(item for item in probe["streams"] if item["codec_type"] == "video")
    source_w = int(stream["width"])
    source_h = int(stream["height"])
    if width < 2 or height < 2:
        return
    if width >= source_w * 0.92 and height >= source_h * 0.92:
        return
    if height < source_h * 0.75:
        return
    if width * height < source_w * source_h * 0.5:
        return
    dest = path.with_suffix(".letterbox.mp4")
    subprocess.run(
        [
            tool("ffmpeg"), "-y", "-i", str(path),
            "-vf", f"crop={width}:{height}:{x}:{y},setsar=1",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-movflags", "+faststart", str(dest),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dest.replace(path)


def _paint_card_fill(path: Path, card_w: int = 680, card_h: int = 1122) -> None:
    """The browser card covers B-roll and portrait lessons. A baked plate is not used."""
    return


def _verify(video_id: str, narration: Path, output: Path, plan: dict, report: dict) -> None:
    probe = phase4._probe(output)
    lesson_seconds = report["lesson_duration"]
    rendered = OUT / video_id / "rendered_audio.wav"
    reference = OUT / video_id / "narration_16k.wav"
    phase4._run([tool("ffmpeg"), "-y", "-i", str(output), "-t", str(lesson_seconds), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rendered)])
    phase4._run([tool("ffmpeg"), "-y", "-i", str(narration), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(reference)])
    audio = compare_wavs(reference, rendered)
    snaps = OUT / video_id / "snapshots"
    if snaps.exists():
        shutil.rmtree(snaps)
    snaps.mkdir(parents=True)
    times = [0.5, round(lesson_seconds * 0.5, 2), round(max(0.4, lesson_seconds - 0.6), 2)]
    times.append(round(lesson_seconds + min(4.0, report["outro_duration"] * 0.5), 2))
    times.append(round(lesson_seconds + max(0.3, report["outro_duration"] - 0.4), 2))
    for decision in plan["decisions"]:
        if decision["action"] != "replace_with_broll":
            continue
        times.append(round(decision["start"] + 0.35, 2))
        times.append(round(min(lesson_seconds - 0.15, decision["end"] + 0.45), 2))
    times = sorted(set(times))
    for stamp in times:
        phase4._run([tool("ffmpeg"), "-y", "-ss", str(stamp), "-i", str(output), "-frames:v", "1", "-update", "1", str(snaps / f"t{stamp:.1f}.jpg")])
    phase4._dump(OUT / video_id / "validation.json", {"probe": probe, "audio_lesson_portion": audio, "outro": report})
    print(video_id, "audio", audio)


def _accepted() -> set[str]:
    path = OUT / "accepted.json"
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload.get("page_urls") or [])


def _find_outro() -> Path:
    folder = ROOT.parent / "OUTRO"
    videos = sorted(path for path in folder.iterdir() if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"})
    if len(videos) != 1:
        raise SystemExit(f"expected one outro video in {folder}, found {len(videos)}")
    return videos[0]


def _find_logo() -> Path:
    folder = ROOT.parent / "LOGO"
    images = sorted(path for path in folder.iterdir() if path.suffix.lower() in {".png", ".webp", ".svg"})
    pngs = [path for path in images if path.suffix.lower() == ".png"]
    if len(pngs) == 1:
        return pngs[0]
    if len(images) == 1:
        return images[0]
    raise SystemExit(f"expected one logo image in {folder}, found {[path.name for path in images]}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
