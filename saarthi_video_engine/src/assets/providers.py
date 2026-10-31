"""Extra footage sources. Commons remains the working video provider.

Openverse answers /v1/videos with HTTP 404, so it is not a video source.
Still images from Openverse are not turned into lesson footage.

Pexels is used only when PEXELS_API_KEY is already in the environment.
The key is a free API key from https://www.pexels.com/api/ . Their terms
require attribution and forbid presenting the files as your own. There is
no charge for search. This module never prints the key. No key is stored
in the project, so the search returns nothing until one is supplied.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def openverse_status() -> dict:
    return {
        "provider": "openverse",
        "video_route": "https://api.openverse.org/v1/videos/",
        "available": False,
        "reason": "The video route returns HTTP 404. Openverse still images are not used as B-roll.",
    }


def pexels_status() -> dict:
    present = bool(os.environ.get("PEXELS_API_KEY"))
    return {
        "provider": "pexels",
        "available": present,
        "environment_variable": "PEXELS_API_KEY",
        "terms": "https://www.pexels.com/api/terms/",
        "cost": "search is free with an API key",
        "reason": "" if present else "PEXELS_API_KEY is not set, so Pexels is not called.",
    }


def search_pexels(query: str, limit: int = 6, page: int = 1) -> list[dict]:
    """Authorized Pexels search. An empty list when the key is absent."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "per_page": str(limit), "page": str(max(1, page))}
    )
    request = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "SarthiVideoEngine/0.4"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    found = []
    for item in payload.get("videos") or []:
        files = item.get("video_files") or []
        ranked = sorted(files, key=lambda entry: abs((entry.get("width") or 0) - 1280))
        chosen = ranked[0] if ranked else {}
        user = (item.get("user") or {}).get("name") or ""
        found.append(
            {
                "title": f"Pexels {item.get('id')}",
                "page_url": item.get("url") or "",
                "download_url": chosen.get("link") or "",
                "mime": "video/mp4",
                "size": 1_000_000,
                "license": "Pexels License",
                "license_url": "https://www.pexels.com/license/",
                "artist": user,
                "description": query,
                "provider": "pexels",
                "width": chosen.get("width"),
                "height": chosen.get("height"),
            }
        )
    return found
