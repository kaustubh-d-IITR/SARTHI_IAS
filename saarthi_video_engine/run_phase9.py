"""Phase 9 layout and B-roll pass. Does not rewrite Phase 8 outputs."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import run_phase8 as phase8

ROOT = Path(__file__).resolve().parent
PHASE8_CACHE = ROOT / "outputs" / "dr_shivin_phase8" / "asset_cache"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    phase8.OUT = ROOT / "outputs" / "dr_shivin_phase9"
    phase8.SUFFIX = "phase9"
    phase8.OUT.mkdir(parents=True, exist_ok=True)
    cache = phase8.OUT / "asset_cache"
    cache.mkdir(parents=True, exist_ok=True)
    if PHASE8_CACHE.is_dir():
        for path in PHASE8_CACHE.iterdir():
            dest = cache / path.name
            if path.is_file() and not dest.exists():
                shutil.copyfile(path, dest)
    if "--render" in args:
        if not (cache / "candidates.json").is_file():
            status = phase8.collect_assets()
            if status:
                return status
        return phase8.render_lessons()
    return phase8.collect_assets()


if __name__ == "__main__":
    raise SystemExit(main())
