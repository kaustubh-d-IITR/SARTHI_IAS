"""Hindi Devanagari to Latin letters.

This is transliteration. It does not translate meanings. An English word that
is already in Latin letters is left as written. A small set of English words
commonly spelled in Devanagari keeps the usual English spelling.
"""

from __future__ import annotations

import re

VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ii",
    "उ": "u",
    "ऊ": "uu",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
}

SIGNS = {
    "ा": "aa",
    "ि": "i",
    "ी": "ii",
    "ु": "u",
    "ू": "uu",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ॉ": "o",
    "ॅ": "e",
}

CONS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "ळ": "l",
}

NUKTA_CONS = {
    "क": "q",
    "ख": "kh",
    "ग": "gh",
    "ज": "z",
    "ड": "d",
    "ढ": "dh",
    "फ": "f",
    "न": "n",
}

# English words written in Devanagari. Hindi words are not in this table.
LOANWORDS = {
    "कोविड": "COVID",
    "हॉस्पिटल": "hospital",
    "हॉस्पिटल्": "hospital",
    "एनसीईआरटी": "NCERT",
    "एनसीआरटी": "NCERT",
    "नीट": "NEET",
    "पीपीई": "PPE",
    "मास्क": "mask",
    "किट": "kit",
    "यूट्यूब": "YouTube",
    "यू ट्यूब": "YouTube",
    "एग्जाम": "exam",
    "पेपर": "paper",
    "कोचिंग": "coaching",
    "ड्यूटी": "duty",
}

VIRAMA = "्"
ANUSVARA = "ं"
CHANDRABINDU = "ँ"
NUKTA = "़"
VISARGA = "ः"
_TOKEN = re.compile(r"\s+|[^\s]+")
# Vowel signs are not \\w, so a \\W edge class would cut them off the word.
_EDGES = re.compile(
    r"^([^\w\u0900-\u097f\u0600-\u06ff]*)(.*?)([^\w\u0900-\u097f\u0600-\u06ff]*)$",
    re.UNICODE,
)


def has_devanagari(text: str) -> bool:
    return any("\u0900" <= char <= "\u097f" for char in text)


def has_arabic(text: str) -> bool:
    """True for Arabic or Urdu letters. Whisper sometimes writes Hindi in this script."""
    return any("\u0600" <= char <= "\u06ff" for char in text)


ARABIC = {
    "ا": "a",
    "آ": "aa",
    "ب": "b",
    "پ": "p",
    "ت": "t",
    "ٹ": "t",
    "ث": "s",
    "ج": "j",
    "چ": "ch",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ڈ": "d",
    "ذ": "z",
    "ر": "r",
    "ڑ": "r",
    "ز": "z",
    "ژ": "zh",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "z",
    "ط": "t",
    "ظ": "z",
    "ع": "a",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ک": "k",
    "گ": "g",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ں": "n",
    "و": "o",
    "ہ": "h",
    "ھ": "h",
    "ء": "",
    "ی": "i",
    "ے": "e",
    "ئ": "",
    "ؤ": "o",
    "َ": "a",
    "ِ": "i",
    "ُ": "u",
    "ْ": "",
    "ّ": "",
    "ٰ": "a",
}

# Conventional Latin spellings of these exact Urdu spellings. Not translations.
URDU_WORDS = {
    "میں": "main",
    "نہیں": "nahin",
    "ہوں": "hoon",
}


def transliterate(text: str) -> str:
    """Return Latin letters for Devanagari or Urdu script. Latin text is unchanged."""
    if not text or not (has_devanagari(text) or has_arabic(text)):
        return text
    return "".join(
        part if part.isspace() else _word(part)
        for part in _TOKEN.findall(text)
    )


def _word(token: str) -> str:
    match = _EDGES.match(token)
    if not match:
        return token
    prefix, core, suffix = match.groups()
    if core in LOANWORDS:
        return f"{prefix}{LOANWORDS[core]}{suffix}"
    if core in URDU_WORDS:
        return f"{prefix}{URDU_WORDS[core]}{suffix}"
    if has_arabic(core) and not has_devanagari(core):
        return prefix + _arabic_word(core) + suffix
    if not has_devanagari(core):
        return token
    pieces: list[tuple[str, bool]] = []
    index = 0
    chars = list(core)
    count = len(chars)
    while index < count:
        char = chars[index]
        if char in {"\u200c", "\u200d"}:
            index += 1
            continue
        if char in VOWELS:
            pieces.append((VOWELS[char], False))
            index += 1
            continue
        if char in CONS:
            base = CONS[char]
            index += 1
            if index < count and chars[index] == NUKTA:
                base = NUKTA_CONS.get(char, base)
                index += 1
            if index < count and chars[index] == VIRAMA:
                pieces.append((base, False))
                index += 1
                continue
            if index < count and chars[index] in SIGNS:
                pieces.append((base + SIGNS[chars[index]], False))
                index += 1
                continue
            pieces.append((base + "a", True))
            continue
        if char in {ANUSVARA, CHANDRABINDU}:
            pieces.append(("n", False))
            index += 1
            continue
        if char == VISARGA:
            pieces.append(("h", False))
            index += 1
            continue
        if char in {"।", "|"}:
            pieces.append((".", False))
            index += 1
            continue
        pieces.append((char, False))
        index += 1
    if pieces and pieces[-1][1]:
        last = pieces[-1][0]
        if last.endswith("a") and not last.endswith("aa"):
            pieces[-1] = (last[:-1], False)
    body = _tidy("".join(part for part, _drop in pieces))
    return prefix + body + suffix


def _tidy(text: str) -> str:
    """Collapse stacked vowel letters. This is still the same word, not a translation."""
    text = text.replace("aaii", "ai").replace("iii", "i").replace("aaa", "aa")
    if text == "huun":
        return "hoon"
    if len(text) >= 4 and text.endswith("aa"):
        text = text[:-1]
    if len(text) >= 3 and text.endswith("ii"):
        text = text[:-1]
    return text


def _arabic_word(core: str) -> str:
    pieces = []
    for char in core:
        if char == "\ufffd":
            continue
        if char in ARABIC:
            pieces.append(ARABIC[char])
        else:
            pieces.append(char)
    return "".join(pieces)
