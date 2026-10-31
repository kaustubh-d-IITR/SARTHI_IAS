"""Phase 10 reference layout and B-roll framing. Does not rewrite Phase 9 outputs."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
PHASE9_CACHE = ROOT / "outputs" / "dr_shivin_phase9" / "asset_cache"
DENIAL = " The spoken line denies this subject, so a negation mark is drawn. negation mark"
OBJECT = " The spoken line denies the action, so the clip shows the object, not a person doing it."


def prepare() -> None:
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase10"
    phase8.SUFFIX = "phase10"
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    cache = phase8.OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    if not PHASE9_CACHE.is_dir():
        raise SystemExit("phase 9 asset cache is missing")
    phase9 = PHASE9_CACHE.parent
    for name in ("visual_observations.json", "accepted.json"):
        src = phase9 / name
        dest = phase8.OUT / name
        if src.is_file() and not dest.exists():
            shutil.copyfile(src, dest)
    for path in PHASE9_CACHE.iterdir():
        dest = cache / path.name
        if path.is_file() and not dest.exists():
            shutil.copyfile(path, dest)
    rows = json.loads((cache / "candidates.json").read_text(encoding="utf-8"))
    for row in rows:
        local = row.get("local_path")
        if local:
            copied = cache / Path(local).name
            if copied.is_file():
                row["local_path"] = str(copied)
        frame = row.get("frame")
        if frame:
            copied = cache / Path(frame).name
            if copied.is_file():
                row["frame"] = str(copied)
        reason = row.get("reason") or ""
        if DENIAL in reason:
            row["reason"] = reason.replace(DENIAL, OBJECT)
        window = row.get("source_window")
        if row.get("video_id") == "VIDEO_1" and row.get("query") == "textbook" and window and float(window[0]) > 40:
            row["source_window"] = [36.98, window[1]]
            row["reason"] = (
                (row.get("reason") or "")
                + " The same library aisle also covers the syllabus sentence that leads into the book line."
            )
    press = cache / "tidningstryck-1937.ogv"
    if press.is_file():
        for row in rows:
            if row.get("query") != "newspaper" or row.get("rejected"):
                continue
            row.update(
                {
                    "title": "File:Tidningstryck 1937.ogv",
                    "page_url": "https://commons.wikimedia.org/wiki/File%3ATidningstryck_1937.ogv",
                    "license": "Public domain",
                    "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
                    "artist": "AB Svensk Filmindustri",
                    "local_path": str(press),
                    "duration_seconds": 104.02,
                    "width": 704,
                    "height": 576,
                    "clip_in": 84,
                    "reason": (
                        "Public-domain footage of the newspaper Stockholms-Tidningen on the press in 1937. "
                        "The in-point is the printed page. The spoken line denies reading the paper, "
                        "so the clip shows the page being printed."
                    ),
                }
            )
    accepted_path = phase8.OUT / "accepted.json"
    if accepted_path.is_file():
        accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
        urls = accepted.setdefault("page_urls", [])
        press_page = "https://commons.wikimedia.org/wiki/File%3ATidningstryck_1937.ogv"
        if press.is_file() and press_page not in urls:
            urls.append(press_page)
        accepted_path.write_text(json.dumps(accepted, indent=2), encoding="utf-8")
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    prepare()
    args = list(sys.argv[1:] if argv is None else argv)
    if "--render" in args:
        return phase8.render_lessons()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
