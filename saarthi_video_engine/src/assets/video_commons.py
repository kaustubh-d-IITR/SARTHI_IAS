"""Wikimedia Commons video search. Still-image search is not used as a video source."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .commons import ALLOWED_LICENSES, AssetRejected, _plain

USER_AGENT = "SarthiVideoEngine/0.4 (local editing test)"
VIDEO_MIMES = {
    "video/mp4",
    "video/webm",
    "video/ogg",
    "application/ogg",
    "video/quicktime",
    "video/mpeg",
}
# Used only when Commons names the license and omits LicenseUrl.
CANONICAL_LICENSE_URLS = {
    "public domain": "https://creativecommons.org/publicdomain/mark/1.0/",
    "pd": "https://creativecommons.org/publicdomain/mark/1.0/",
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}

MISLEADING = (
    "windmill",
    "high-pressure",
    "high pressure",
    "cleaning",
    "gym",
    "exercise",
    "logo",
    "icon",
    "diagram only",
    "cartoon",
    "mascot",
    "anime",
    "baby",
    "stop motion",
    "origami",
    "visualization",
    "elitebook",
    "probook",
    "photography",
    "fine-art",
    "fine art",
    "lecture",
    "author",
    "quote",
    "presentation",
    "swim",
    "freestyle",
    "underwater",
)


class SearchHits(list):
    """A Commons page list plus the transport facts for that one request."""

    search_meta: dict | None = None


def search_videos(
    query: str,
    limit: int = 6,
    offset: int = 0,
    *,
    opener=None,
    sleep=time.sleep,
    attempts: int = 4,
    http_budget=None,
    trace: list | None = None,
) -> list:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:video",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    }
    if offset:
        params["gsroffset"] = str(offset)
    if opener is None and attempts == 4 and http_budget is None and sleep is time.sleep and trace is None:
        return _search(params)
    return _search(params, opener=opener, sleep=sleep, attempts=attempts, http_budget=http_budget, trace=trace)


def accept_video_candidate(candidate: dict, query: str, speech: str = "") -> str | None:
    """Return a rejection reason, or None when the file may be downloaded."""
    license_name = (candidate.get("license") or "").lower()
    tokens = set(re.split(r"[^a-z0-9]+", license_name))
    if tokens & {"nc", "nd"} or "noncommercial" in license_name or "noderiv" in license_name:
        return f"license {candidate.get('license')} does not allow this adaptation"
    compact = license_name.replace(" ", "-")
    if compact not in ALLOWED_LICENSES and "public domain" not in license_name and compact != "cc0":
        return f"license {candidate.get('license') or 'missing'} is not on the allow list"
    if not candidate.get("license_url"):
        return "license url is missing"
    if not candidate.get("download_url"):
        return "no download url"
    mime = (candidate.get("mime") or "").lower()
    if mime not in VIDEO_MIMES:
        return f"mime {candidate.get('mime')} is not a video"
    size = candidate.get("size") or 0
    if not isinstance(size, int) or size < 50_000:
        return "video size is missing or too small"
    if size > 40_000_000:
        return "video is over 40 MB"
    haystack = f"{candidate.get('description') or ''} {candidate.get('title') or ''}".lower()
    terms = [term for term in query.lower().replace("-", " ").split() if len(term) >= 4]
    if not terms or not all(re.search(rf"\b{re.escape(term)}(?!-)\b", haystack) for term in terms):
        return "description does not contain the speech query"
    speech_l = speech.lower()
    if any(word in haystack for word in MISLEADING) and not any(word in speech_l for word in MISLEADING):
        return "description points at a different subject from the spoken query"
    from .concepts import denies

    if denies(speech, query) and any(word in haystack for word in ("reading", "reads", "reader")):
        return "clip depicts the denied action of reading"
    return None


def _duration_seconds(value: object) -> float | None:
    """Commons puts a numeric duration on video imageinfo. Anything else stays unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _fill_public_domain_url(candidate: dict) -> None:
    """Record the canonical mark when Commons states public domain or CC0 and omits the URL."""
    if candidate.get("license_url"):
        return
    name = (candidate.get("license") or "").lower().strip()
    url = CANONICAL_LICENSE_URLS.get(name) or CANONICAL_LICENSE_URLS.get(name.replace(" ", "-"))
    if not url:
        return
    candidate["license_url"] = url
    candidate["license_url_note"] = (
        "Commons omitted LicenseUrl. The short name is public domain or CC0, so the canonical mark is recorded."
    )


def download_video(
    url: str,
    destination: Path,
    max_bytes: int = 40_000_000,
    opener=None,
    sleep=time.sleep,
    attempts: int = 4,
    http_budget=None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    data = _read(
        request,
        timeout=120,
        opener=opener or urllib.request.urlopen,
        sleep=sleep,
        attempts=attempts,
        http_budget=http_budget,
    )
    if len(data) > max_bytes:
        raise AssetRejected("video exceeded 40 MB")
    if len(data) < 50_000:
        raise AssetRejected("video download was empty")
    destination.write_bytes(data)
    return destination


def _search(params: dict, opener=None, sleep=time.sleep, attempts: int = 4, http_budget=None, trace: list | None = None) -> list:
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    sent = str(params.get("gsrsearch") or "")
    status: dict = {}
    try:
        raw = _read(
            request,
            timeout=40,
            opener=opener or urllib.request.urlopen,
            sleep=sleep,
            attempts=attempts,
            http_budget=http_budget,
            status_box=status,
        )
    except urllib.error.HTTPError as error:
        _remember(trace, error, {"sent_query": sent, "http_status": error.code, "succeeded": False, "outcome": "http_error", "raw_count": 0})
        raise
    except urllib.error.URLError as error:
        _remember(trace, error, {"sent_query": sent, "http_status": None, "succeeded": False, "outcome": "transport_error", "raw_count": 0})
        raise
    payload = json.loads(raw.decode("utf-8"))
    pages = (payload.get("query") or {}).get("pages") or {}
    found = []
    for page in pages.values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}
        title = page.get("title") or ""
        found.append(
            {
                "title": title,
                "page_url": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                "download_url": info.get("url"),
                "mime": info.get("mime"),
                "size": info.get("size"),
                "duration": _duration_seconds(info.get("duration")),
                "license": _plain(meta.get("LicenseShortName")) or _plain(meta.get("License")),
                "license_url": _plain(meta.get("LicenseUrl")),
                "artist": _plain(meta.get("Artist")),
                "description": (_plain(meta.get("ImageDescription")) + " " + _plain(meta.get("ObjectName"))).strip(),
            }
        )
    found_list = SearchHits(found) if trace is not None else found
    if trace is not None:
        meta = {
            "sent_query": sent,
            "http_status": status.get("code"),
            "succeeded": True,
            "raw_count": len(found),
        }
        found_list.search_meta = meta
        trace.append(meta)
    return found_list


def _remember(trace: list | None, error: BaseException, meta: dict) -> None:
    """Attach transport facts to the exception. Headers are not copied."""
    try:
        error.search_meta = meta  # type: ignore[attr-defined]
    except Exception:
        return
    if trace is not None:
        trace.append(meta)


def _read(request, timeout: float, opener, sleep, attempts: int = 4, http_budget=None, status_box: dict | None = None) -> bytes:
    """Retry rate limits and transient gateway errors. Other HTTP errors surface immediately."""
    delay = 1.0
    for attempt in range(attempts):
        if http_budget is not None:
            http_budget.acquire()
        try:
            with opener(request, timeout=timeout) as response:
                if status_box is not None:
                    code = getattr(response, "status", None)
                    if code is None:
                        code = getattr(response, "code", None)
                    status_box["code"] = code
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
            sleep(delay)
            delay = min(delay * 2, 8.0)
        except urllib.error.URLError:
            if attempt == attempts - 1:
                raise
            sleep(delay)
            delay = min(delay * 2, 8.0)
    raise RuntimeError("request was not completed")
