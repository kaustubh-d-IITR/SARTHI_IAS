"""Phase 6 layout, titles, and one inspected newspaper clip.

Reads the Phase 5 transcripts. Does not rewrite Phase 5 outputs.
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

import run_phase5 as phase5  # noqa: E402
from assets.concepts import denies, visual_queries  # noqa: E402
from render.frame import layout_issues, reference_frame  # noqa: E402
from render.outro import tool  # noqa: E402

PHASE4 = ROOT / "outputs" / "dr_shivin_phase4"
PHASE5 = ROOT / "outputs" / "dr_shivin_phase5"
OUT = ROOT / "outputs" / "dr_shivin_phase6"
PRESS = OUT / "asset_cache" / "sambad-press.mp4"
PRESS_PAGE = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(
    "File:Printing_in_progress_at_Sambad_Press,_Bhubaneswar,_Odisha,_India.webm"
)
PRESS_LICENSE = "CC BY-SA 4.0"
PRESS_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0"
SOURCE_WEBM = ROOT / "outputs" / "dr_shivin_phase6_preview" / "1.webm"


def main() -> int:
    prepare()
    phase5.OUT = OUT
    phase5.CACHE = OUT / "asset_cache"
    phase5.TITLE_OVERRIDES = {
        "VIDEO_1": "You Will Make\n[[Mistakes]]",
        "VIDEO_2": "From [[NEET]] to [[UPSC]]",
    }
    phase5.FINAL_SUFFIX = "phase6"
    phase5.render()
    review_layouts()
    return 0


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PHASE5 / "visual_observations.json", OUT / "visual_observations.json")
    for video_id in ("VIDEO_1", "VIDEO_2"):
        source = PHASE5 / video_id / "work" / "transcript.json"
        dest = OUT / video_id / "work" / "transcript.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        review = PHASE5 / video_id / "caption_review.json"
        if review.is_file():
            shutil.copyfile(review, OUT / video_id / "caption_review.json")
    cache = OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    music_src = PHASE5 / "asset_cache" / "inspire-action-yuri-megis.ogg"
    if music_src.is_file():
        shutil.copyfile(music_src, cache / "inspire-action-yuri-megis.ogg")
    _trim_press(SOURCE_WEBM, PRESS)
    rows = _candidate_rows()
    (cache / "candidates.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pages = [phase5.MUSIC_PAGE, PRESS_PAGE]
    (OUT / "accepted.json").write_text(
        json.dumps(
            {
                "page_urls": pages,
                "note": "Music reused from phase 4. One press clip was accepted after its frames were viewed.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _trim_press(source: Path, dest: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"missing inspected press clip {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            tool("ffmpeg"), "-y", "-ss", "0.4", "-t", "4.0", "-i", str(source), "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(dest),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _candidate_rows() -> list[dict]:
    rows = []
    speech = {}
    for video_id in ("VIDEO_1", "VIDEO_2"):
        transcript = json.loads((OUT / video_id / "work" / "transcript.json").read_text(encoding="utf-8"))
        text = " ".join(segment.get("text") or "" for segment in transcript["segments"])
        speech[video_id] = text
        for item in visual_queries(text):
            if item["action"] == "skip":
                rows.append({
                    "video_id": video_id,
                    "kind": "video",
                    "term": item["term"],
                    "rejected": item["reason"],
                })
    denial = denies(speech["VIDEO_2"], "newspaper")
    reason = (
        "Sambad Press, Bhubaneswar, prints newspapers. Frames show the press and paper rolls, not a person reading. "
    )
    if denial:
        reason += "The spoken line denies reading the paper, so a negation mark is drawn over the cutaway. negation mark"
    rows.append({
        "video_id": "VIDEO_2",
        "kind": "video",
        "query": "newspaper",
        "title": "File:Printing in progress at Sambad Press, Bhubaneswar, Odisha, India.webm",
        "page_url": PRESS_PAGE,
        "license": PRESS_LICENSE,
        "license_url": PRESS_LICENSE_URL,
        "artist": "Wikimedia Commons contributor, Sambad Press, Bhubaneswar",
        "match_terms": ["newspaper"],
        "local_path": str(PRESS),
        "duration_seconds": 4.0,
        "rejected": None,
        "reason": reason,
    })
    rows.append({
        "video_id": "VIDEO_2",
        "kind": "video",
        "query": "newspaper",
        "title": "File:Folha de Sao Paulo - Stop Motion Newspaper Ad.webm",
        "license": "CC BY 3.0",
        "page_url": "https://commons.wikimedia.org/wiki/File:%22Folha_de_S%C3%A3o_Paulo%22_-_Stop_Motion_Newspaper_Ad.webm",
        "rejected": "preview frames show origami birds, not a newspaper",
    })
    music = _music_row()
    if music:
        rows.append(music)
    return rows


def _music_row() -> dict | None:
    path = PHASE4 / "asset_cache" / "candidates.json"
    if not path.is_file():
        return None
    for row in json.loads(path.read_text(encoding="utf-8")):
        if row.get("kind") == "audio" and row.get("page_url") == phase5.MUSIC_PAGE and not row.get("rejected"):
            copied = dict(row)
            copied["local_path"] = str(OUT / "asset_cache" / "inspire-action-yuri-megis.ogg")
            copied["reused_from"] = "phase 4 licensed instrumental"
            return copied
    return None


def review_layouts() -> None:
    frame = reference_frame()
    issues = layout_issues(frame)
    notes = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        html_path = OUT / video_id / "composition" / "index.html"
        html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
        broll_at = html.find('class="clip broll')
        card_at = html.find('id="card"')
        notes.append({
            "video_id": video_id,
            "html_has_red_frame": "#d01212" in html,
            "html_has_logo": 'id="corner-logo"' in html,
            "broll_inside_card": broll_at > card_at > -1,
        })
    for note in notes:
        if not note["html_has_red_frame"]:
            issues.append(f"{note['video_id']} is missing the red frame")
        if not note["html_has_logo"]:
            issues.append(f"{note['video_id']} is missing the corner logo")
        html_path = OUT / note["video_id"] / "composition" / "index.html"
        html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
        if 'class="clip broll' in html and not note["broll_inside_card"]:
            issues.append(f"{note['video_id']} draws B-roll outside the content frame")
    payload = {"frame": frame, "issues": issues, "videos": notes}
    (OUT / "layout_review.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("layout issues", issues or "none")
    if issues:
        raise SystemExit("layout review failed")


if __name__ == "__main__":
    raise SystemExit(main())
