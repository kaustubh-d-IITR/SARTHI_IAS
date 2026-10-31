"""Download a Wikimedia Commons image only when the license and the description match."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ALLOWED_LICENSES = {
    "cc-by-2.0",
    "cc-by-2.5",
    "cc-by-3.0",
    "cc-by-4.0",
    "cc-by-sa-2.0",
    "cc-by-sa-3.0",
    "cc-by-sa-4.0",
    "cc0",
    "cc0-1.0",
    "pd",
    "public domain",
}

STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "because", "been", "before",
    "can", "could", "device", "direction", "down", "easy", "first", "from", "function",
    "here", "into", "just", "larger", "lets", "let's", "mass", "masses", "more",
    "must", "near", "now", "over", "point", "reasonable", "right", "same", "small",
    "something", "that", "the", "then", "there", "this", "time", "want", "what",
    "when", "which", "will", "with", "would", "your",
}


class AssetRejected(RuntimeError):
    pass


def concepts_from_speech(text: str, limit: int = 3) -> list[str]:
    """Words and phrases that actually occur in the transcript."""
    normalized = text.lower().replace("-", " ")
    words = re.findall(r"[a-z][a-z0-9']+", normalized)
    phrases = []
    for word in words:
        if word in STOPWORDS or len(word) < 5 or word in phrases:
            continue
        phrases.append(word)
    for size in (2, 3):
        for index in range(len(words) - size + 1):
            chunk = words[index : index + size]
            if any(word in STOPWORDS or len(word) < 4 for word in chunk):
                continue
            phrase = " ".join(chunk)
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases[:limit]


def search_commons(query: str, limit: int = 6) -> list[dict]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "960",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "SarthiVideoEngine/0.2 (local editing test)"})
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))
    pages = (payload.get("query") or {}).get("pages") or {}
    found = []
    for page in pages.values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}
        license_name = _plain(meta.get("LicenseShortName")) or _plain(meta.get("License"))
        description = _plain(meta.get("ImageDescription")) + " " + _plain(meta.get("ObjectName"))
        found.append(
            {
                "title": page.get("title"),
                "page_url": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote((page.get("title") or "").replace(" ", "_")),
                "download_url": info.get("thumburl") or info.get("url"),
                "original_url": info.get("url"),
                "mime": info.get("mime"),
                "size": info.get("size"),
                "license": license_name,
                "license_url": _plain(meta.get("LicenseUrl")),
                "artist": _plain(meta.get("Artist")),
                "description": description.strip(),
                "attribution_required": _plain(meta.get("AttributionRequired")),
            }
        )
    return found


def accept_candidate(candidate: dict, query: str, speech: str = "") -> str | None:
    """Return a rejection reason, or None when the file may be used."""
    license_name = (candidate.get("license") or "").lower()
    if any(flag in license_name for flag in ("nc", "noncommercial", "nd", "noderiv")):
        return f"license {candidate.get('license')} does not allow this adaptation"
    compact = license_name.replace(" ", "-")
    if compact not in ALLOWED_LICENSES and "public domain" not in license_name and compact != "cc0":
        return f"license {candidate.get('license') or 'missing'} is not on the allow list"
    if not candidate.get("download_url"):
        return "no download url"
    if candidate.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
        return f"mime {candidate.get('mime')} is not a still image"
    haystack = (candidate.get("description") or "").lower() + " " + (candidate.get("title") or "").lower()
    terms = [term for term in query.lower().split() if len(term) >= 4]
    if not terms or not any(term in haystack for term in terms):
        return "description does not contain the speech query"
    speech_l = speech.lower()
    physics = any(word in speech_l for word in ("force", "mass", "acceleration", "tension", "friction"))
    if physics:
        unrelated = ("windmill", "flagpole", "clothesline", "gym", "exercise", "ship", "sail", "barn", "well ")
        if any(word in haystack for word in unrelated) and not any(word in speech_l for word in unrelated):
            return "image description is a different kind of pulley from the lecture"
        if not any(word in haystack for word in ("diagram", "physics", "force", "mass", "mechanics", "atwood", "tension", "machine")):
            return "description does not show this is the physics apparatus being discussed"
    return None


def download_image(url: str, destination: Path, max_bytes: int = 8_000_000) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SarthiVideoEngine/0.2 (local editing test)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AssetRejected("image exceeded 8 MB")
    if len(data) < 1000:
        raise AssetRejected("image download was empty")
    destination.write_bytes(data)
    return destination


def _plain(field) -> str:
    if isinstance(field, dict):
        value = field.get("value") or ""
    else:
        value = field or ""
    return re.sub(r"<[^>]+>", " ", str(value)).replace("\n", " ").strip()
