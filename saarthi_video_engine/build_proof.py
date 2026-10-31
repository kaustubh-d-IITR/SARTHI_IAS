"""Write the proof-of-concept plan, manifest, and HyperFrames composition.

Media files are generated only when ffmpeg is on PATH. The composition paths
are relative to compositions/proof_of_concept.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from assets.manifest import build_manifest  # noqa: E402
from assets.synthetic import generate_synthetic_media  # noqa: E402
from dataset_adapter.loader import load_dataset  # noqa: E402
from dataset_adapter.style_profile import style_profile  # noqa: E402
from editing_plan.build import build_proof_plan  # noqa: E402
from hyperframes_adapter.compose import write_composition  # noqa: E402
from validation.plan import validate_plan  # noqa: E402

PROJECT = ROOT / "compositions" / "proof_of_concept"
DEFAULTS_PATH = ROOT / "configs" / "render.defaults.json"
EXEMPLAR = "SHORT_03"


def main() -> int:
    dataset = load_dataset()
    profile = style_profile(dataset, EXEMPLAR)
    media = {
        "teacher": "assets/teacher.mp4",
        "broll": "assets/broll.mp4",
        "narration": "assets/narration.wav",
        "music": "assets/music_placeholder.wav",
        "logo": "assets/logo.svg",
    }
    plan = build_proof_plan(dataset, EXEMPLAR, media)
    errors = validate_plan(plan)
    if errors:
        for error in errors:
            print(error)
        return 1
    try:
        generate_synthetic_media(PROJECT / "assets", plan["input_media"]["duration_seconds"])
        media_note = "synthetic media written"
    except RuntimeError as exc:
        media_note = f"media not generated: {exc}"
    (PROJECT / "assets").mkdir(parents=True, exist_ok=True)
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    write_composition(plan, defaults, PROJECT / "index.html")
    _dump(ROOT / "examples" / "sample_editing_plan.json", plan)
    _dump(PROJECT / "editing_plan.json", plan)
    _dump(PROJECT / "style_profile.json", profile)
    _dump(PROJECT / "asset_manifest.json", build_manifest(media))
    print(f"exemplar {EXEMPLAR} family {profile['layout_family']}")
    print(f"plan {ROOT / 'examples' / 'sample_editing_plan.json'}")
    print(f"composition {PROJECT / 'index.html'}")
    print(media_note)
    return 0


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
