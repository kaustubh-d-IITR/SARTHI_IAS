"""Choose the words that may appear in the caption box.

English speech keeps the original English words. Devanagari and Urdu
script are transliterated. English words in a Hinglish line stay in English.
A whisper -tr translation is never copied into the caption.
"""

from __future__ import annotations

import re

from .hinglish import splice_unknown_run
from .normalize import normalize_roman
from .romanize import has_arabic, has_devanagari, transliterate

ENGLISH_FUNCTION = {
    "a", "am", "an", "and", "are", "as", "at", "be", "because", "been", "but",
    "can", "coming", "could", "did", "do", "does", "even", "for", "from", "going",
    "had", "has", "have", "he", "her", "his", "i", "if", "in", "is", "it", "its",
    "just", "me", "my", "not", "of", "on", "or", "our", "she", "so", "that",
    "the", "their", "them", "then", "there", "they", "this", "to", "was", "we",
    "were", "what", "when", "will", "with", "would", "you", "your",
}

_WORD = re.compile(r"[A-Za-z']+")


def is_recognition_loop(text: str) -> bool:
    """A repeated token or one letter filling the line is not usable caption text."""
    compact = "".join(char for char in text.lower() if char.isalpha())
    if len(compact) >= 40:
        common = max(set(compact), key=compact.count)
        if compact.count(common) / len(compact) >= 0.45:
            return True
    words = [word.lower().strip(".,!?;:") for word in text.split() if word.strip(".,!?;:")]
    if len(words) >= 8 and len(set(words)) / len(words) <= 0.25:
        return True
    if len(words) >= 9:
        grams = [" ".join(words[index : index + 3]) for index in range(len(words) - 2)]
        if grams:
            top = max(set(grams), key=grams.count)
            if len(top) >= 6 and grams.count(top) >= 3:
                return True
    return False


def _thin_line(text: str) -> bool:
    words = [word for word in text.split() if word.strip(".,!?;:")]
    return len(words) <= 6


def _english_audit(original: str, supplied, language: str) -> str:
    if str(language).lower() in {"en", "english"}:
        return original
    if isinstance(supplied, str):
        return supplied
    return ""


def looks_like_english_tokens(text: str) -> bool:
    """True when a non-English recognition is mostly English function words."""
    words = _WORD.findall(text)
    if len(words) < 5:
        return False
    hits = sum(1 for word in words if word.lower().strip("'") in ENGLISH_FUNCTION)
    return hits / len(words) >= 0.34


def prepare_segment(segment: dict, language: str) -> dict:
    original = (segment.get("original_text") or segment.get("text") or "").strip()
    supplied = segment.get("english_text")
    flags: list[str] = []
    notes: list[str] = []
    start = segment.get("start")
    end = segment.get("end")
    span = end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else 0.0
    if is_recognition_loop(original) or (span >= 15 and _thin_line(original)):
        flags.append("repeated_recognition")
        return {
            "original_text": original,
            "romanized_text": original,
            "caption_text": "",
            "english_text": _english_audit(original, supplied, language),
            "flags": flags,
            "normalization_notes": notes,
        }
    if has_devanagari(original) or has_arabic(original):
        romanized = transliterate(original).strip()
    else:
        romanized = original
        if str(language).lower() not in {"en", "english"} and looks_like_english_tokens(original):
            flags.append("english_tokens_for_non_english_speech")
    caption, notes = normalize_roman(romanized)
    clause = segment.get("english_clause")
    if isinstance(clause, str) and clause.strip():
        caption, splice_note = splice_unknown_run(caption, clause.strip())
        if splice_note:
            notes.append(splice_note)
            flags.append("english_clause_restored")
    if notes:
        flags.append("spelling_normalized")
    confidence = segment.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.35:
        flags.append("low_confidence")
    return {
        "original_text": original,
        "romanized_text": romanized,
        "caption_text": caption,
        "english_text": _english_audit(original, supplied, language),
        "flags": flags,
        "normalization_notes": notes,
    }


def caption_pieces(segment: dict, prepared: dict, max_words: int = 3) -> list[dict]:
    """Timed caption lines. Word offsets are used only when the provider supplied them."""
    caption = (prepared.get("caption_text") or "").strip()
    if not caption:
        return []
    words = []
    for word in segment.get("words") or []:
        raw = (word.get("text") or "").strip()
        if not raw or raw.startswith("[") or raw.startswith("<"):
            continue
        shown = transliterate(raw).strip() if has_devanagari(raw) or has_arabic(raw) else raw
        if not shown or shown in {",", ".", "।", "?", "!", ";", ":"}:
            continue
        start = word.get("start", segment["start"])
        end = word.get("end", segment["end"])
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            continue
        words.append((shown, float(start), float(end)))
    # Whisper often emits one Unicode letter per token for Urdu or Hindi.
    # Those tokens are not words, so the segment line is the caption.
    short = sum(1 for shown, _start, _end in words if len(shown) <= 2)
    letterish = not words or short > len(words) * 0.4
    if letterish or len(words) <= 10:
        start = float(segment["start"])
        end = float(segment["end"])
        # A letter-token line spread over a long span is a recognition stall, not a sentence.
        if letterish and end - start > 8:
            end = round(start + 6.0, 3)
        return [{"text": caption, "start": start, "end": end}]
    pieces = []
    for index in range(0, len(words), max_words):
        group = words[index : index + max_words]
        shown, _notes = normalize_roman(" ".join(item[0] for item in group))
        pieces.append(
            {
                "text": shown,
                "start": group[0][1],
                "end": group[-1][2],
            }
        )
    return pieces or [{"text": caption, "start": segment["start"], "end": segment["end"]}]
