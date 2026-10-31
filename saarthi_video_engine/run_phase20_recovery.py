"""Restore Phase 18 acceptances that Phase 20's selector dropped.

Reads the existing plans. Does not search, verify, or modify those directories.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import run_phase20 as phase20

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from broll_intel.retain import retain_verified  # noqa: E402

OUT = ROOT / "outputs" / "dr_shivin_phase20_recovery"
PHASE18 = ROOT / "outputs" / "dr_shivin_phase18"
PHASE20 = ROOT / "outputs" / "dr_shivin_phase20"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    OUT.mkdir(parents=True, exist_ok=True)
    plans = {}
    notes = []
    for video_id in ("VIDEO_1", "VIDEO_2"):
        current_rows = json.loads((PHASE20 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        prior_rows = {
            str(row.get("transcript_segment_id")): row
            for row in json.loads((PHASE18 / f"editorial_plan_{video_id}.json").read_text(encoding="utf-8"))
        }
        merged = []
        for row in current_rows:
            prior = prior_rows.get(str(row.get("transcript_segment_id")))
            kept = retain_verified(row, prior)
            merged.append(kept)
            if (kept.get("retain") or {}).get("action") == "restored":
                notes.append(
                    {
                        "lesson_id": video_id,
                        "segment": kept.get("transcript_segment_id"),
                        "speech_start": kept.get("speech_start"),
                        "speech_end": kept.get("speech_end"),
                        "source_in": kept.get("selected_source_in"),
                        "source_out": kept.get("selected_source_out"),
                        "page_url": kept.get("selected_asset_id"),
                        "exact_transcript": kept.get("exact_transcript"),
                    }
                )
        plans[video_id] = merged
        (OUT / f"editorial_plan_{video_id}.json").write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    (OUT / "recovered_intervals.json").write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
    print("restored", len(notes), flush=True)
    for note in notes:
        print(note["lesson_id"], note["segment"], note["speech_start"], note["speech_end"], note["source_in"], flush=True)
    if "--no-render" in args:
        return 0
    phase20.OUT = OUT
    return phase20._render(plans, phase20._catalogue())


if __name__ == "__main__":
    raise SystemExit(main())
