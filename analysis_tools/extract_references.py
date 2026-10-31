"""Extract metadata, captions, scene cuts, audio stats, and frames for reference Shorts.

This is a Step-0 inspection tool. It does not implement the video editor.
Media files stay in the local cache, outside the dataset folder.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import imageio_ffmpeg
import yt_dlp

ROOT = Path(r"c:\Users\shubh\OneDrive\Desktop\SAARTHI IAS")
LINKS = ROOT / "links.txt"
CACHE = Path(r"C:\Users\shubh\AppData\Local\sarthi_shorts_cache")
DATASET = ROOT / "reference_dataset"
RAW = DATASET / "raw"
LOGS = DATASET / "analysis_logs"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
NODE = r"C:\Program Files\nodejs\node.exe"

SCENE_THRESHOLD = 0.28


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_links(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        m = re.match(r"^(\d+)\)\s+(\S+)\s*(.*)$", raw)
        if not m:
            rows.append({"parse_error": raw})
            continue
        index = int(m.group(1))
        url = m.group(2).strip()
        description = m.group(3).strip()
        idm = re.search(r"/shorts/([A-Za-z0-9_-]{6,})", url)
        video_id = idm.group(1) if idm else None
        rows.append(
            {
                "index": index,
                "reference_id": f"SHORT_{index:02d}",
                "url": url,
                "video_id": video_id,
                "user_topic_description": description,
            }
        )
    return rows


def run_ffmpeg(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def probe_media(path: Path) -> dict:
    proc = run_ffmpeg(["-i", str(path)])
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    duration = None
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if dm:
        duration = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
    vm = re.search(
        r"Video:\s*(\w+).*?(\d{2,5})x(\d{2,5}).*?(\d+(?:\.\d+)?)\s*fps",
        text,
    )
    am = re.search(r"Audio:\s*([^,\n]+),\s*(\d+)\s*Hz,\s*([^,\n]+)", text)
    out = {"duration_seconds": duration, "ffmpeg_probe_excerpt": text[-2500:]}
    if vm:
        out.update(
            {
                "video_codec": vm.group(1),
                "width": int(vm.group(2)),
                "height": int(vm.group(3)),
                "fps": float(vm.group(4)),
            }
        )
    if am:
        out.update(
            {
                "audio_codec": am.group(1).strip(),
                "sample_rate_hz": int(am.group(2)),
                "audio_channels": am.group(3).strip(),
            }
        )
    return out


def parse_json3(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        text = "".join(seg.get("utf8", "") for seg in segs).replace("\n", " ").strip()
        if not text:
            continue
        start = event.get("tStartMs")
        dur = event.get("dDurationMs")
        if start is None:
            continue
        start_s = round(start / 1000.0, 3)
        end_s = round((start + dur) / 1000.0, 3) if dur is not None else None
        segments.append({"start": start_s, "end": end_s, "text": text})
    return segments


def scene_times(video: Path) -> list[float]:
    proc = run_ffmpeg(
        [
            "-i",
            str(video),
            "-vf",
            f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ],
        timeout=240,
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    times = []
    for match in re.finditer(r"pts_time:([0-9.]+)", text):
        times.append(round(float(match.group(1)), 3))
    # showinfo can repeat; preserve order and drop near-duplicates
    cleaned = []
    for t in times:
        if not cleaned or abs(t - cleaned[-1]) > 0.05:
            cleaned.append(t)
    return cleaned


def extract_scene_frames(video: Path, times: list[float], dest: Path) -> list[str]:
    """Save one post-cut frame per scene-change timestamp in a single pass."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = run_ffmpeg(
        [
            "-i",
            str(video),
            "-vf",
            f"select='gt(scene,{SCENE_THRESHOLD})',scale=540:-1",
            "-fps_mode",
            "vfr",
            "-q:v",
            "3",
            str(dest / "scene_%03d.jpg"),
        ],
        timeout=180,
    )
    raw_frames = sorted(dest.glob("scene_*.jpg"))
    saved = []
    for i, frame in enumerate(raw_frames):
        t = times[i] if i < len(times) else None
        if t is None:
            saved.append(str(frame))
            continue
        named = dest / f"scene_{i + 1:03d}_{t:.3f}.jpg"
        if frame != named:
            frame.replace(named)
        saved.append(str(named))
    if not saved and proc.returncode != 0:
        saved.append(f"FAILED: {(proc.stderr or '')[-300:]}")
    return saved


def extract_second_frames(video: Path, dest: Path, duration: float | None) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    font = r"C\:/Windows/Fonts/arial.ttf"
    vf = (
        "fps=1,scale=420:-1,"
        f"drawtext=fontfile='{font}':text='%{{eif\\:t\\:d}}s':"
        "fontsize=26:fontcolor=white:box=1:boxcolor=black@0.65:x=8:y=8"
    )
    proc = run_ffmpeg(
        ["-i", str(video), "-vf", vf, "-q:v", "4", str(dest / "sec_%03d.jpg")],
        timeout=240,
    )
    frames = sorted(dest.glob("sec_*.jpg"))
    drawtext_ok = bool(frames)
    if not frames:
        proc = run_ffmpeg(
            [
                "-i",
                str(video),
                "-vf",
                "fps=1,scale=420:-1",
                "-q:v",
                "4",
                str(dest / "sec_%03d.jpg"),
            ],
            timeout=240,
        )
        frames = sorted(dest.glob("sec_*.jpg"))
    sheets = []
    sheets_dir = dest.parent / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    if frames:
        # 3x3 grids so each frame stays large enough to read captions.
        per = 9
        for page, start in enumerate(range(0, len(frames), per), start=1):
            chunk = frames[start : start + per]
            # Build a concat list via the tile filter on a generated sequence.
            # Copy chunk to a temp numbered sequence.
            tmp = dest / f"_tile_{page}"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir()
            for j, frame in enumerate(chunk, start=1):
                shutil.copyfile(frame, tmp / f"f_{j:02d}.jpg")
            sheet = sheets_dir / f"sheet_{page:02d}.jpg"
            run_ffmpeg(
                [
                    "-framerate",
                    "1",
                    "-i",
                    str(tmp / "f_%02d.jpg"),
                    "-vf",
                    "tile=3x3:padding=4:margin=4:color=black",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(sheet),
                ],
                timeout=60,
            )
            shutil.rmtree(tmp, ignore_errors=True)
            if sheet.exists():
                first_t = start  # fps=1, sec_001 is t=0
                sheets.append(
                    {
                        "path": str(sheet),
                        "frame_index_start": start + 1,
                        "approx_start_second": first_t,
                        "frame_count": len(chunk),
                    }
                )
    return {
        "drawtext_ok": drawtext_ok,
        "one_fps_frame_count": len(frames),
        "one_fps_frames_dir": str(dest),
        "contact_sheets": sheets,
        "ffmpeg_tail": (proc.stderr or "")[-500:],
        "expected_seconds": duration,
    }


def audio_stats(video: Path) -> dict:
    silence = run_ffmpeg(
        [
            "-i",
            str(video),
            "-af",
            "silencedetect=noise=-32dB:d=0.20",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    stext = silence.stderr or ""
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", stext)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", stext)]
    intervals = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        intervals.append(
            {
                "start": round(start, 3),
                "end": round(end, 3) if end is not None else None,
                "duration": round(end - start, 3) if end is not None else None,
            }
        )
    volume = run_ffmpeg(
        ["-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
        timeout=180,
    )
    vtext = volume.stderr or ""
    vol = {}
    for key in ("mean_volume", "max_volume"):
        m = re.search(rf"{key}:\s*([-\d.]+)\s*dB", vtext)
        if m:
            vol[key + "_db"] = float(m.group(1))
    rms = run_ffmpeg(
        [
            "-i",
            str(video),
            "-af",
            "asetnsamples=n=48000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    levels = []
    for m in re.finditer(
        r"pts_time:([0-9.]+).*?lavfi\.astats\.Overall\.RMS_level=(-?inf|[+-]?\d+(?:\.\d+)?)",
        (rms.stderr or ""),
        flags=re.S,
    ):
        val = m.group(2)
        try:
            number = None if val in {"inf", "-inf", "+inf"} else float(val)
        except ValueError:
            number = None
        levels.append({"t": round(float(m.group(1)), 3), "rms_db": number})
    # Fallback line-oriented parse if the combined regex misses.
    if not levels:
        current_t = None
        for line in (rms.stderr or "").splitlines():
            tm = re.search(r"pts_time:([0-9.]+)", line)
            if tm:
                current_t = float(tm.group(1))
            rm = re.search(r"RMS_level=([-\d.]+)", line)
            if rm and current_t is not None:
                levels.append({"t": round(current_t, 3), "rms_db": float(rm.group(1))})
                current_t = None
    pause_levels = []
    for interval in intervals:
        if interval["end"] is None:
            continue
        window = [
            row["rms_db"]
            for row in levels
            if row["rms_db"] is not None and interval["start"] <= row["t"] <= interval["end"]
        ]
        if window:
            pause_levels.append(round(sum(window) / len(window), 2))
    return {
        "silence_threshold": "-32dB for >=0.20s",
        "silence_intervals": intervals,
        "volume": vol,
        "rms_1s": levels[:180],
        "mean_rms_during_detected_silence_db": (
            round(sum(pause_levels) / len(pause_levels), 2) if pause_levels else None
        ),
        "note": "Single mixed track. Music identity cannot be separated from speech by this measurement.",
    }


def slim_metadata(info: dict) -> dict:
    keep = [
        "id",
        "title",
        "format",
        "format_id",
        "resolution",
        "fulltitle",
        "channel",
        "channel_id",
        "uploader",
        "upload_date",
        "timestamp",
        "duration",
        "duration_string",
        "width",
        "height",
        "fps",
        "aspect_ratio",
        "vcodec",
        "acodec",
        "language",
        "availability",
        "webpage_url",
        "original_url",
        "categories",
        "tags",
        "view_count",
        "like_count",
        "comment_count",
        "description",
        "channel_url",
        "uploader_url",
        "live_status",
        "was_live",
        "age_limit",
        "playable_in_embed",
    ]
    out = {k: info.get(k) for k in keep}
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    out["manual_subtitle_langs"] = sorted(subs.keys())
    out["automatic_caption_langs_sample"] = sorted(auto.keys())[:12]
    out["automatic_caption_lang_count"] = len(auto)
    return out


def fetch_url(url: str, dest: Path, attempts: int = 5) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as resp:
                dest.write_bytes(resp.read())
            return
        except Exception as exc:  # noqa: BLE001 - retry network and 429 errors
            last_error = exc
            time.sleep(2.5 * (attempt + 1))
    raise RuntimeError(f"download failed for caption: {last_error}")


def save_caption_tracks(info: dict, dest: Path) -> dict[str, list[dict]]:
    auto = info.get("automatic_captions") or {}
    manual = info.get("subtitles") or {}
    saved: dict[str, list[dict]] = {}
    # One original-language track only. Extra tracks were triggering HTTP 429.
    for lang in ("hi-orig", "en-orig", "hi", "en"):
        if manual.get(lang) or auto.get(lang):
            wanted = (lang,)
            break
    else:
        wanted = ()
    for lang in wanted:
        tracks = manual.get(lang) or auto.get(lang) or []
        chosen = next((t for t in tracks if t.get("ext") == "json3" and t.get("url")), None)
        if not chosen:
            continue
        path = dest / f"caption.{lang}.json3"
        try:
            fetch_url(chosen["url"], path)
        except Exception as exc:  # noqa: BLE001
            saved[f"{lang}_error"] = [{"start": None, "end": None, "text": str(exc)}]
            continue
        try:
            saved[lang] = parse_json3(path)
        except Exception as exc:  # noqa: BLE001
            saved[f"{lang}_error"] = [{"start": None, "end": None, "text": f"parse failed: {exc}"}]
        time.sleep(0.4)
    return saved


def download_one(ref: dict, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest / "media.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {"path": NODE}},
        "ffmpeg_location": FFMPEG,
        "format": (
            "bv*[vcodec^=avc1][height>=720][height<=1280][ext=mp4]+ba[ext=m4a]/"
            "bv*[vcodec^=avc1][height<=1280][ext=mp4]+ba[ext=m4a]/"
            "b[height<=1280]/b"
        ),
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "noprogress": True,
        "sleep_interval_requests": 0.5,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(ref["url"], download=True)
    media = next(dest.glob("media.mp4"), None)
    if media is None:
        candidates = [p for p in dest.glob("media.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
        media = candidates[0] if candidates else None
    return {"info": info, "media_path": str(media) if media else None}


def process(ref: dict) -> dict:
    ref_id = ref["reference_id"]
    work = CACHE / ref_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    started = time.time()
    downloaded = download_one(ref, work)
    info = downloaded["info"]
    media_path = downloaded["media_path"]
    result = {
        "reference_id": ref_id,
        "source_url": ref["url"],
        "video_id_from_url": ref["video_id"],
        "user_topic_description": ref["user_topic_description"],
        "extracted_at": now_iso(),
        "metadata": slim_metadata(info),
        "media_path": media_path,
        "status": "extracted",
    }
    result["caption_files"] = save_caption_tracks(info, work)
    if media_path:
        media = Path(media_path)
        result["probe"] = probe_media(media)
        result["scene_change_times_seconds"] = scene_times(media)
        result["scene_frames"] = extract_scene_frames(
            media, result["scene_change_times_seconds"], work / "scenes"
        )
        result["frames"] = extract_second_frames(
            media, work / "frames", result["probe"].get("duration_seconds")
        )
        result["audio"] = audio_stats(media)
    result["elapsed_seconds"] = round(time.time() - started, 1)
    return result


def write_log(ref_id: str, payload: dict) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"reference_id: {ref_id}",
        f"time: {payload.get('extracted_at') or now_iso()}",
        f"status: {payload.get('status')}",
        f"video_id: {(payload.get('metadata') or {}).get('id')}",
        f"title: {(payload.get('metadata') or {}).get('title')}",
        f"duration: {(payload.get('metadata') or {}).get('duration')}",
        f"media: {payload.get('media_path')}",
        f"scene_changes: {len(payload.get('scene_change_times_seconds') or [])}",
        f"caption_tracks: {list((payload.get('caption_files') or {}).keys())}",
        f"elapsed_seconds: {payload.get('elapsed_seconds')}",
    ]
    if payload.get("error"):
        lines.append("error:")
        lines.append(payload["error"])
    (LOGS / f"{ref_id}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    only = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    refs = parse_links(LINKS)
    RAW.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    manifest_path = DATASET / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "created_at": now_iso(),
            "source": "links.txt",
            "references": [],
        }
    by_id = {row["reference_id"]: row for row in manifest["references"] if "reference_id" in row}
    for ref in refs:
        if "parse_error" in ref:
            continue
        row = by_id.get(ref["reference_id"]) or {
            "reference_id": ref["reference_id"],
            "url": ref["url"],
            "video_id": ref["video_id"],
            "user_topic_description": ref["user_topic_description"],
            "status": "pending",
        }
        row.update(
            {
                "url": ref["url"],
                "video_id": ref["video_id"],
                "user_topic_description": ref["user_topic_description"],
            }
        )
        by_id[ref["reference_id"]] = row
    ordered = [by_id[f"SHORT_{i:02d}"] for i in range(1, 51) if f"SHORT_{i:02d}" in by_id]
    manifest["references"] = ordered
    manifest["updated_at"] = now_iso()
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    failures = 0
    for ref in refs:
        if "parse_error" in ref:
            print("PARSE_ERROR", ref["parse_error"], flush=True)
            failures += 1
            continue
        if only and ref["reference_id"] not in only and ref["video_id"] not in only:
            continue
        raw_path = RAW / f"{ref['reference_id']}.raw.json"
        if raw_path.exists() and not only:
            print("SKIP existing", ref["reference_id"], flush=True)
            continue
        print("START", ref["reference_id"], ref["video_id"], flush=True)
        row = by_id[ref["reference_id"]]
        row["status"] = "in_progress"
        row["updated_at"] = now_iso()
        manifest["updated_at"] = now_iso()
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            result = process(ref)
            # Drop bulky probe excerpt from the committed raw file after keeping a short version.
            probe = result.get("probe") or {}
            if "ffmpeg_probe_excerpt" in probe:
                probe["ffmpeg_probe_excerpt"] = probe["ffmpeg_probe_excerpt"][-800:]
            raw_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            write_log(ref["reference_id"], result)
            row["status"] = "extracted"
            row["title"] = (result.get("metadata") or {}).get("title")
            row["duration"] = (result.get("metadata") or {}).get("duration")
            row["channel"] = (result.get("metadata") or {}).get("channel")
            row["raw_file"] = str(raw_path)
            print("OK", ref["reference_id"], row["title"], row["duration"], flush=True)
            time.sleep(1.0)
        except Exception:
            failures += 1
            err = traceback.format_exc()
            payload = {
                "reference_id": ref["reference_id"],
                "source_url": ref["url"],
                "status": "failed",
                "extracted_at": now_iso(),
                "error": err,
            }
            raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            write_log(ref["reference_id"], payload)
            row["status"] = "failed"
            row["error"] = err[-1000:]
            print("FAIL", ref["reference_id"], flush=True)
            print(err, flush=True)
        row["updated_at"] = now_iso()
        manifest["updated_at"] = now_iso()
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("DONE failures", failures, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
