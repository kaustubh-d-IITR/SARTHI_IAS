"""Mark 1fps frames that differ sharply from the previous frame.

Complements ffmpeg scene detection, which missed some full-frame swaps.
Reads cached JPEG sequences and writes one_fps_cut_seconds back into raw JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

RAW = Path(r"c:\Users\shubh\OneDrive\Desktop\SAARTHI IAS\reference_dataset\raw")
CACHE = Path(r"C:\Users\shubh\AppData\Local\sarthi_shorts_cache")
THRESHOLD = 18.0  # mean absolute RGB difference on a tiny grayscale proxy


def frame_score(prev: Image.Image, cur: Image.Image) -> float:
    a = prev.convert("L").resize((64, 64))
    b = cur.convert("L").resize((64, 64))
    diff = ImageChops.difference(a, b)
    return ImageStat.Stat(diff).mean[0]


def main() -> None:
    for raw_path in sorted(RAW.glob("SHORT_*.raw.json")):
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        frames_dir = CACHE / raw_path.stem.replace(".raw", "") / "frames"
        # stem is SHORT_01.raw -> wrong. raw_path.stem is SHORT_01.raw if suffix is .json? 
        # Path('SHORT_01.raw.json').stem == 'SHORT_01.raw'
        ref_id = raw_path.name.split(".")[0]
        frames_dir = CACHE / ref_id / "frames"
        frames = sorted(frames_dir.glob("sec_*.jpg"))
        if len(frames) < 2:
            print("skip", ref_id, "frames", len(frames))
            continue
        cuts = []
        scores = []
        prev = Image.open(frames[0])
        for index, frame in enumerate(frames[1:], start=1):
            cur = Image.open(frame)
            score = round(frame_score(prev, cur), 2)
            scores.append({"t": index, "diff": score})
            if score >= THRESHOLD:
                cuts.append(index)
            prev.close()
            prev = cur
        prev.close()
        out_dir = Path(r"c:\Users\shubh\OneDrive\Desktop\SAARTHI IAS\reference_dataset\frame_diffs")
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "reference_id": ref_id,
            "one_fps_cut_seconds": cuts,
            "one_fps_diff_threshold": THRESHOLD,
            "one_fps_diff_sample": scores,
            "method": "mean absolute difference of consecutive 1fps frames, resized to 64x64 grayscale",
        }
        (out_dir / f"{ref_id}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(ref_id, "cuts", cuts)
        print("done", ref_id)


if __name__ == "__main__":
    main()
