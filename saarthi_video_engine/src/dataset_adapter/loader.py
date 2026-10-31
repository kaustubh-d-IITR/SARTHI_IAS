"""Read shorts_dataset.json without treating partial records as a full baseline."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED_RECORD_KEYS = (
    "reference_id",
    "quality",
    "layout_group",
    "visual_style",
    "category",
    "timeline",
    "captions",
    "broll_segments",
    "teacher_visibility",
    "audio_music",
    "effects_transitions",
)

# Every record sets manual_review_required because audio was metered and not
# listened to. That flag is a standing limitation, not a completeness gate.
TRUSTED_STATUS = "completed"


def default_dataset_path() -> Path:
    project = Path(__file__).resolve().parents[3]
    return project / "reference_dataset" / "shorts_dataset.json"


def load_dataset(path: Path | None = None) -> dict:
    dataset_path = Path(path) if path else default_dataset_path()
    with dataset_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "shorts" not in data:
        raise ValueError(f"{dataset_path} has no shorts array")
    shorts = data["shorts"]
    if not isinstance(shorts, list) or not shorts:
        raise ValueError(f"{dataset_path} has an empty shorts array")
    seen = set()
    for record in shorts:
        missing = [key for key in REQUIRED_RECORD_KEYS if key not in record]
        if missing:
            raise ValueError(f"{record.get('reference_id')} missing {missing}")
        ref = record["reference_id"]
        if ref in seen:
            raise ValueError(f"duplicate reference_id {ref}")
        seen.add(ref)
        if "status" not in record.get("quality", {}):
            raise ValueError(f"{ref} has no quality.status")
    data["_path"] = str(dataset_path)
    return data


def is_trusted(record: dict) -> bool:
    """True when the record is complete enough to feed style rules.

    Partial records stay loadable. Their missing buckets or unusable transcripts
    must not become editing rules.
    """
    return record.get("quality", {}).get("status") == TRUSTED_STATUS


def trusted_records(dataset: dict) -> list[dict]:
    return [record for record in dataset["shorts"] if is_trusted(record)]


def partial_ids(dataset: dict) -> list[str]:
    return [
        record["reference_id"]
        for record in dataset["shorts"]
        if not is_trusted(record)
    ]
