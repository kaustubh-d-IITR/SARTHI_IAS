"""Build assets/music_library/ from Wikimedia Commons, through the engine's licence gate.

Run by hand when the library should change; the app never downloads music at runtime.
Each category gets up to --per-category instrumental tracks that pass
assets.music_commons.accept_music_candidate (CC0 / CC BY / CC BY-SA / public domain,
no NC or ND, instrumental, audio mime, sane size). Tracks are trimmed to --max-seconds
and stored as Ogg Vorbis. library.json records title, author, licence and source page.

    python deploy/build_music_library.py --per-category 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "src"))

from assets.music_commons import accept_music_candidate, download_audio, search_instrumentals  # noqa: E402

OUT = ENGINE / "assets" / "music_library"
QUERIES = {
    "inspirational": ["inspirational instrumental", "uplifting piano instrumental"],
    "motivational": ["motivational instrumental", "uplifting orchestra", "uplifting piano", "hopeful piano", "positive upbeat instrumental"],
    "cinematic": ["cinematic orchestra instrumental", "epic orchestral instrumental"],
    "documentary": ["documentary background instrumental", "ambient documentary music"],
    "calm_educational": ["calm piano instrumental", "soft piano background"],
    "serious_current_affairs": ["dark strings", "dramatic orchestra", "sad piano", "serious news instrumental", "suspense orchestra"],
    "energetic": ["energetic instrumental", "action orchestra", "fast piano", "upbeat electronic instrumental", "rock instrumental"],
    "minimal_ambience": ["ambient", "ambient pad", "ambient drone", "ambient piano", "minimal ambient instrumental"],
}


def is_default(page_url: str | None) -> bool:
    return "Inspire_Action_by_Yuri_Megis" in (page_url or "")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "track"


def probe_seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=2)
    parser.add_argument("--max-seconds", type=float, default=150.0)
    parser.add_argument("--min-seconds", type=float, default=45.0)
    parser.add_argument("--only", nargs="*", help="Categories to (re)fill; others are kept from library.json.")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = OUT / "library.json"
    existing = json.loads(manifest.read_text(encoding="utf-8")).get("tracks", []) if manifest.is_file() else []
    tracks = [row for row in existing if args.only and row["category"] not in args.only and not is_default(row.get("page_url"))]
    seen = {row.get("page_url") for row in tracks}
    report = []
    for category, queries in QUERIES.items():
        if args.only and category not in args.only:
            continue
        kept = 0
        for query in queries:
            if kept >= args.per_category:
                break
            try:
                candidates = search_instrumentals(query, limit=12)
            except Exception as exc:  # noqa: BLE001
                report.append({"category": category, "query": query, "error": type(exc).__name__})
                continue
            for candidate in candidates:
                if kept >= args.per_category:
                    break
                if candidate.get("page_url") in seen or is_default(candidate.get("page_url")):
                    continue
                reason = accept_music_candidate(candidate)
                seconds = float(candidate.get("duration") or 0)
                if not reason and seconds and seconds < args.min_seconds:
                    reason = f"shorter than {args.min_seconds:.0f} s"
                if reason:
                    report.append({"category": category, "title": candidate.get("title"), "rejected": reason})
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    raw = Path(tmp) / "source"
                    try:
                        download_audio(candidate["download_url"], raw)
                    except Exception as exc:  # noqa: BLE001
                        report.append({"category": category, "title": candidate.get("title"), "rejected": f"download failed ({type(exc).__name__})"})
                        continue
                    length = probe_seconds(raw)
                    if length < args.min_seconds:
                        report.append({"category": category, "title": candidate.get("title"), "rejected": f"{length:.0f} s is too short"})
                        continue
                    name = f"{category}-{slug(str(candidate['title']).removeprefix('File:').rsplit('.', 1)[0])}.ogg"
                    keep = min(length, args.max_seconds)
                    fade = 3.0
                    done = subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-t", f"{keep:.2f}", "-vn", "-af", f"afade=t=out:st={max(0.0, keep - fade):.2f}:d={fade}", "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "3", str(OUT / name)],
                        capture_output=True,
                    )
                    if done.returncode != 0:
                        report.append({"category": category, "title": candidate.get("title"), "rejected": "transcode failed"})
                        continue
                seen.add(candidate.get("page_url"))
                kept += 1
                digest = hashlib.sha256((OUT / name).read_bytes()).hexdigest()
                tracks.append(
                    {
                        "track_id": f"{category}-{digest[:10]}",
                        "title": str(candidate["title"]).removeprefix("File:").rsplit(".", 1)[0],
                        "artist": candidate.get("artist") or "",
                        "category": category,
                        "duration_seconds": round(probe_seconds(OUT / name), 2),
                        "license": candidate.get("license") or "",
                        "license_url": candidate.get("license_url") or "",
                        "page_url": candidate.get("page_url") or "",
                        "file": name,
                        "sha256": digest,
                        "trimmed_from_seconds": round(length, 2),
                    }
                )
                print(f"{category}: {tracks[-1]['title']} ({tracks[-1]['license']})", flush=True)
    (OUT / "library.json").write_text(json.dumps({"source": "Wikimedia Commons via assets.music_commons licence gate", "tracks": tracks}, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "curation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(tracks)} tracks written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
