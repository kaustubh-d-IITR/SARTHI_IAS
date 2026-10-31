"""Restore an English clause from a second recognition pass.

The second pass is used only when this line already has a run of tokens that
are neither Hindi nor readable English. The English words are copied from that
pass. They are not invented from a slide.
"""

from __future__ import annotations

import re

HINDI_WORDS = {
    "aaj", "agar", "aur", "baat", "baar", "bahan", "bhai", "bhaiya", "bhi",
    "behen", "choti", "hai", "hain", "hota", "hoti", "hoon", "kai", "kar",
    "ke", "kehta", "ki", "ko", "koi", "main", "mein", "mera", "mere", "meri",
    "nahi", "raha", "saath", "to", "vaat", "vo", "woh", "ye",
}

_CLAUSE = re.compile(r"[A-Za-z']+(?:\s+[A-Za-z']+){3,}")
_WORD = re.compile(r"[A-Za-z']+")


def merge_passes(primary: list[dict], secondary: list[dict], is_loop) -> list[dict]:
    """Keep the pass that is not a recognition loop, and fill a loop-sized gap from the other pass."""
    primary_ok = [_tag(segment, "primary") for segment in primary if not _unusable(segment, is_loop)]
    secondary_ok = [_tag(segment, "secondary") for segment in secondary if not _unusable(segment, is_loop)]
    if len(primary_ok) < 2:
        return secondary_ok
    covered = max(segment["end"] for segment in primary_ok)
    extra = [segment for segment in secondary_ok if segment["start"] >= covered - 1.0]
    return sorted(primary_ok + extra, key=lambda segment: segment["start"])


def _tag(segment: dict, name: str) -> dict:
    tagged = dict(segment)
    tagged["asr_pass"] = name
    return tagged


def _unusable(segment: dict, is_loop) -> bool:
    text = (segment.get("original_text") or segment.get("text") or "").strip()
    if not text or is_loop(text):
        return True
    start = segment.get("start")
    end = segment.get("end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end - start >= 15:
        words = [word for word in text.split() if word.strip(".,")]
        if len(words) <= 8:
            return True
    return False


def clause_for_segment(segment: dict, others: list[dict]) -> str:
    """English clauses from the other pass segment with the greatest time overlap."""
    start = segment.get("start")
    end = segment.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return ""
    best_overlap = 0.0
    best_text = ""
    for other in others:
        other_start = other.get("start")
        other_end = other.get("end")
        if not isinstance(other_start, (int, float)) or not isinstance(other_end, (int, float)):
            continue
        overlap = min(end, other_end) - max(start, other_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best_text = other.get("original_text") or other.get("text") or ""
    if best_overlap < 0.3:
        return ""
    return ", ".join(english_clauses(best_text))


def english_clauses(text: str) -> list[str]:
    clauses = []
    for match in _CLAUSE.finditer(text):
        clause = match.group(0).strip()
        words = _WORD.findall(clause)
        if len(words) < 4:
            continue
        if _readable_english(words):
            clauses.append(clause)
    return clauses


def splice_unknown_run(text: str, clause: str) -> tuple[str, str]:
    """Replace one unreadable run with an English clause from the other pass."""
    if not clause or clause.lower() in text.lower():
        return text, ""
    tokens = text.split()
    run = _unknown_run(tokens)
    if run is None:
        return text, ""
    start, end = run
    piece = clause
    if any(token.endswith(",") for token in tokens[start:end]):
        piece += ","
    replaced = tokens[:start] + [piece] + tokens[end:]
    return " ".join(replaced), "replaced an unreadable run with the English clause from the other recognition pass"


def _unknown_run(tokens: list[str]) -> tuple[int, int] | None:
    best = None
    index = 0
    while index < len(tokens):
        if _known(tokens[index]):
            index += 1
            continue
        end = index
        while end < len(tokens) and not _known(tokens[end]):
            end += 1
        plain = [token.strip(".,") for token in tokens[index:end]]
        if end - index >= 3 and not _readable_english(plain):
            if best is None or (end - index) > (best[1] - best[0]):
                best = (index, end)
        index = end
    return best


def _known(token: str) -> bool:
    bare = token.strip(".,;:।").lower()
    return bare in HINDI_WORDS or bare in {"i", "a"}


def _readable_english(words: list[str]) -> bool:
    if len(words) < 4:
        return False
    function = {
        "a", "and", "the", "to", "of", "you", "will", "have", "i", "don't", "not",
        "is", "it", "that", "then", "even", "from", "with", "for", "this", "was",
    }
    hits = sum(1 for word in words if word.lower().strip("'") in function or word.lower() == "don't")
    return hits >= 2
