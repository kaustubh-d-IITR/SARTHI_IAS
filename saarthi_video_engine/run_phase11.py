"""Phase 11. Drops a repeated clip, keeps Phase 10 footage, and renders a new pair.

Does not call ElevenLabs and does not rewrite Phase 10 outputs.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import run_phase8 as phase8
from analysis.subjects import opportunity_records
from analysis.uniqueness import asset_identity, enforce_unique

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

PHASE10 = ROOT / "outputs" / "dr_shivin_phase10"
SECOND_SPEECH = "yaar newspaper padh lo aur ncert padh lo"
ALTERNATES = ("reading a newspaper", "newspaper stand", "stack of newspapers")


def prepare() -> dict:
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase11"
    phase8.SUFFIX = "phase11"
    phase8.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    cache = phase8.OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    source_cache = PHASE10 / "asset_cache"
    if not source_cache.is_dir():
        raise SystemExit("phase 10 asset cache is missing")
    for path in source_cache.iterdir():
        dest = cache / path.name
        if path.is_file() and not dest.exists():
            shutil.copyfile(path, dest)
    for name in ("visual_observations.json", "accepted.json"):
        src = PHASE10 / name
        dest = phase8.OUT / name
        if src.is_file() and not dest.exists():
            shutil.copyfile(src, dest)
    rows = json.loads((cache / "candidates.json").read_text(encoding="utf-8"))
    for row in rows:
        blob = f"{row.get('title') or ''} {row.get('description') or ''}".lower()
        if row.get("query") == "newspaper" and any(
            word in blob for word in ("president", "parliament", "no-confidence", "corruption")
        ):
            if "printing" not in blob and "printed" not in blob:
                row["rejected"] = "news report about a person, not footage of a newspaper"
    for row in rows:
        local = row.get("local_path")
        if local:
            copied = cache / Path(local).name
            if copied.is_file():
                row["local_path"] = str(copied)
    rows, rejected = enforce_unique(rows)
    replacement = _replacement_for_second_newspaper(rows, cache)
    if replacement:
        rows.append(replacement)
        rejected_note = "the repeated printing clip was replaced with a different file"
    else:
        rejected_note = "the second newspaper mention stays on the teacher"
    rows, rejected_again = enforce_unique(rows)
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    accepted_path = phase8.OUT / "accepted.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8")) if accepted_path.is_file() else {"page_urls": []}
    urls = accepted.setdefault("page_urls", [])
    for row in rows:
        page = row.get("page_url")
        if page and not row.get("rejected") and page not in urls:
            urls.append(page)
    accepted_path.write_text(json.dumps(accepted, indent=2), encoding="utf-8")
    subjects = {}
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = phase8._load_transcript(video_id)
        records = opportunity_records(transcript)
        subjects[video_id] = records
        (phase8.OUT / video_id).mkdir(parents=True, exist_ok=True)
        (phase8.OUT / video_id / "subjects.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    summary = {
        "duplicates_rejected": rejected + rejected_again,
        "second_newspaper": rejected_note,
        "replacement_page": (replacement or {}).get("page_url"),
        "subjects": {
            video_id: sorted({item["category"] for record in records for item in record["subjects"]})
            for video_id, records in subjects.items()
        },
    }
    (phase8.OUT / "uniqueness.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _replacement_for_second_newspaper(rows: list[dict], cache: Path) -> dict | None:
    """Search a different newspaper scene for the later mention. None leaves the teacher up."""
    mentions = [
        row for row in rows
        if row.get("video_id") == "VIDEO_2" and row.get("query") == "newspaper" and row.get("source_window")
    ]
    if len(mentions) < 2:
        return None
    used = {asset_identity(row) for row in rows if row.get("page_url")}
    later = max(mentions, key=lambda row: float(row["source_window"][0]))
    if not later.get("rejected"):
        return None
    item = {
        "query": "newspaper",
        "term": "newspaper",
        "variants": list(ALTERNATES),
        "sentence": SECOND_SPEECH,
        "negation": False,
    }
    found = phase8._find_download(item, SECOND_SPEECH, cache, exclude=used)
    if not found or asset_identity(found) in used:
        return None
    found["video_id"] = "VIDEO_2"
    found["match_terms"] = ["newspaper"]
    found["source_window"] = later["source_window"]
    found["kind"] = "video"
    found["rejected"] = None
    found["reason"] = "A different newspaper scene for the second mention. The printing clip is not reused."
    return found


def main(argv: list[str] | None = None) -> int:
    summary = prepare()
    print(json.dumps(summary))
    args = list(sys.argv[1:] if argv is None else argv)
    if "--render" in args:
        return phase8.render_lessons()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
