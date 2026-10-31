"""One source clip per lesson. A repeated subject needs a different asset or the teacher."""

from __future__ import annotations

import hashlib
from pathlib import Path


def query_phrase_missing(variant: str, haystack: str) -> bool:
    """True when a multi-word query is only present as scattered words.

    "newspaper stand" must occur as that phrase. A line that says "newspaper"
    and, elsewhere, "stand down" is not a newsstand.
    """
    phrase = " ".join(variant.lower().split())
    words = [word for word in phrase.split() if len(word) >= 4]
    if len(words) < 2:
        return False
    return phrase not in haystack.lower()


def asset_identity(row: dict) -> str:
    """Stable id. A second download of the same Commons file does not become a new clip."""
    page = (row.get("page_url") or "").strip()
    if page:
        return "page:" + page
    digest = (row.get("sha256") or "").strip()
    if digest:
        return "sha256:" + digest
    path = (row.get("local_path") or "").strip()
    return "path:" + path.lower() if path else ""


def file_sha256(path: str | None) -> str:
    if not path or not Path(path).is_file():
        return ""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hamming(left: str, right: str) -> int:
    """Bit distance between two equal-length hex hashes. A mismatch in length is not a match."""
    if not left or not right or len(left) != len(right):
        return 64
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


def enforce_unique(rows: list[dict], near_bits: int = 8) -> tuple[list[dict], int]:
    """Reject a repeated source inside one lesson. The same file may appear in another lesson."""
    lesson_ids = [row.get("video_id") for row in rows if row.get("video_id")]
    if len(set(lesson_ids)) <= 1:
        return _enforce_one_lesson(rows, near_bits)
    kept: list[dict] = []
    rejected = 0
    seen: list = []
    for video_id in dict.fromkeys(row.get("video_id") for row in rows):
        if video_id in seen:
            continue
        seen.append(video_id)
        part, count = _enforce_one_lesson([row for row in rows if row.get("video_id") == video_id], near_bits)
        kept.extend(part)
        rejected += count
    return kept, rejected


def _enforce_one_lesson(rows: list[dict], near_bits: int = 8) -> tuple[list[dict], int]:
    """Reject a later row that repeats a page, the same bytes, or a near-identical frame.

    Audio beds are not inserts. Rows without a visual hash are compared only by page and file.
    """
    used_ids: set[str] = set()
    used_hashes: set[str] = set()
    used_visual: list[str] = []
    kept: list[dict] = []
    rejected = 0
    for row in rows:
        if row.get("rejected") or row.get("kind") == "audio" or not row.get("source_window"):
            kept.append(row)
            continue
        identity = asset_identity(row)
        digest = (row.get("sha256") or "") or file_sha256(row.get("local_path"))
        visual = (row.get("visual_hash") or "").strip()
        near = next((previous for previous in used_visual if hamming(visual, previous) <= near_bits), "")
        if (identity and identity in used_ids) or (digest and digest in used_hashes) or near:
            copy = dict(row)
            copy["rejected"] = (
                "this source clip looks like footage already used in this lesson"
                if near and identity not in used_ids and digest not in used_hashes
                else "this source clip was already used earlier in this lesson"
            )
            copy["duplicate_of"] = identity or digest or near
            kept.append(copy)
            rejected += 1
            continue
        if identity:
            used_ids.add(identity)
        if digest:
            used_hashes.add(digest)
            row = dict(row)
            row["sha256"] = digest
        if visual:
            used_visual.append(visual)
        kept.append(row)
    return kept, rejected
