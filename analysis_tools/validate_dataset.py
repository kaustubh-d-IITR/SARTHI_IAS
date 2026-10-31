"""Check the Step-0 dataset against links.txt and the reports."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(r"c:\Users\shubh\OneDrive\Desktop\SAARTHI IAS")
DATASET = ROOT / "reference_dataset"


def parse_links(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        match = re.match(r"^(\d+)\)\s+(\S+)", raw)
        if not match:
            raise SystemExit(f"bad link line: {raw}")
        index = int(match.group(1))
        url = match.group(2)
        video_id = re.search(r"/shorts/([A-Za-z0-9_-]+)", url).group(1)
        rows.append((f"SHORT_{index:02d}", url, video_id))
    return rows


def main() -> int:
    errors = []
    links = parse_links(ROOT / "links.txt")
    copy = parse_links(DATASET / "links.txt")
    if links != copy:
        errors.append("reference_dataset/links.txt does not match the project links.txt")
    if len(links) != 50:
        errors.append(f"expected 50 links, found {len(links)}")
    ids = [row[0] for row in links]
    if len(ids) != len(set(ids)):
        errors.append("duplicate reference ids in links.txt")
    data = json.loads((DATASET / "shorts_dataset.json").read_text(encoding="utf-8"))
    shorts = data["shorts"]
    if [item["reference_id"] for item in shorts] != ids:
        errors.append("dataset order does not match links.txt")
    summary = data["analysis_summary"]
    counted = {"completed": 0, "partial": 0, "failed": 0, "blocked": 0}
    for item, (ref_id, url, video_id) in zip(shorts, links):
        if item["url"].split("?")[0].rstrip("/") != url.split("?")[0].rstrip("/"):
            # links.txt keeps the shorts form; the record may store the same URL.
            link_id = video_id
            if item["video_id"] != link_id:
                errors.append(f"{ref_id} video id {item['video_id']} != {link_id}")
        if item["video_id"] != video_id:
            errors.append(f"{ref_id} id mismatch")
        report = DATASET / "reports" / f"{ref_id}.md"
        if not report.exists():
            errors.append(f"missing report {ref_id}")
        log = DATASET / "analysis_logs" / f"{ref_id}.log"
        if not log.exists():
            errors.append(f"missing log {ref_id}")
        status = item["quality"]["status"]
        counted[status] = counted.get(status, 0) + 1
        vis = item.get("teacher_visibility") or {}
        for key, value in vis.items():
            if key.endswith("percent_of_samples") and value is not None:
                if not 0 <= float(value) <= 100:
                    errors.append(f"{ref_id} percent out of range: {key}={value}")
        cursor = -0.01
        for row in item.get("timeline") or []:
            start = float(row["start"])
            end = float(row["end"])
            if end < start - 0.05:
                errors.append(f"{ref_id} timeline end before start {start}-{end}")
            if start < cursor - 0.05:
                errors.append(f"{ref_id} timeline out of order at {start}")
            cursor = end
        original = item["transcript"].get("original_text") or ""
        english = item["transcript"].get("english_translation") or ""
        if original and english and original.strip() == english.strip() and item["metadata"].get("language_metadata") == "hi":
            errors.append(f"{ref_id} english translation is identical to the Hindi caption")
    for key, value in summary.items():
        if counted.get(key, 0) != value:
            errors.append(f"summary {key}={value} but records say {counted.get(key, 0)}")
    if errors:
        print("FAILED")
        for error in errors:
            print("-", error)
        return 1
    print("OK", json.dumps(counted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
