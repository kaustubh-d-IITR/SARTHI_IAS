"""Contextual spelling for Roman Hindi.

This does not translate. It only rewrites a closed set of common Hindi
spellings when the tokens are already those words. English phrases are left
as written. A badly recognized word is not replaced with a guessed sentence.
"""

from __future__ import annotations

import re

_PHRASES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bbhai[\s-]+bhin\b", re.IGNORECASE),
        "bhai-behen",
        "bhai bhin is the recognizer spelling of bhai-behen",
    ),
    (
        re.compile(r"\bbhai[\s-]+b[ae]h[ae]?n\b", re.IGNORECASE),
        "bhai-behen",
        "joined the spoken pair bhai-behen",
    ),
    (re.compile(r"\bbahan\b", re.IGNORECASE), "behen", "normalized bahan to behen"),
    (
        re.compile(r"\b(?:kai|kaii|kayi|kayee)\s+baar\b", re.IGNORECASE),
        "kai baar",
        "normalized the phrase kai baar",
    ),
    (
        re.compile(r"\b(?:kehta|kahta|kahata)\s+(?:hoon|hun|hu|huun)\b", re.IGNORECASE),
        "kehta hoon",
        "normalized the phrase kehta hoon",
    ),
    (re.compile(r"\bnahii+n\b", re.IGNORECASE), "nahi", "normalized nahiin to nahi"),
    (re.compile(r"\bnahin\b", re.IGNORECASE), "nahi", "normalized nahin to nahi"),
    (re.compile(r"\bnahe+n\b", re.IGNORECASE), "nahi", "normalized naheen to nahi"),
    (re.compile(r"\bhainn\b", re.IGNORECASE), "hain", "normalized hainn to hain"),
    (re.compile(r"\bmeraa\b", re.IGNORECASE), "mera", "normalized meraa to mera"),
    (re.compile(r"\bkoii\b", re.IGNORECASE), "koi", "normalized koii to koi"),
    (re.compile(r"\b(?:yeh|yah)\b", re.IGNORECASE), "ye", "normalized yeh/yah to ye"),
    (re.compile(r"\bnaa hi\b", re.IGNORECASE), "na hi", "normalized naa hi to na hi"),
    (re.compile(r"\bmainne\b", re.IGNORECASE), "maine", "normalized mainne to maine"),
    (
        re.compile(r"\bye vaat\b", re.IGNORECASE),
        "ye baat",
        "ye vaat is the recognizer spelling of ye baat",
    ),
)

_HAI_CUES = {"mera", "meri", "koi", "ye", "baat", "nahi", "kuch", "sab", "woh", "vo"}
_MAIN_START = re.compile(r"^main(?=\s+(?:kai|bhi|ne|to|ye|aaj|aksar|hoon)\b)", re.IGNORECASE)
_TOKEN = re.compile(r"\bhe\b", re.IGNORECASE)


def _hai_after_cue(text: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        start = match.start()
        previous = text[:start].split()
        if previous and previous[-1].lower().strip(".,") in _HAI_CUES:
            count += 1
            return "hai"
        return match.group(0)

    return _TOKEN.sub(replace, text), count


def normalize_roman(text: str) -> tuple[str, list[str]]:
    """Return the normalized line and the reasons for each change."""
    updated = text
    reasons: list[str] = []
    for pattern, replacement, reason in _PHRASES:
        updated, count = pattern.subn(replacement, updated)
        if count:
            reasons.append(reason)
    updated, hai_count = _hai_after_cue(updated)
    if hai_count:
        reasons.append("hai follows a Hindi cue, so the copula spelling is hai")
    if _MAIN_START.match(updated):
        updated = "M" + updated[1:]
        reasons.append("capitalized the Hindi pronoun Main at the start of the line")
    return updated, reasons
