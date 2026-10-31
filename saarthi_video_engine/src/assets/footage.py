"""Search every configured provider and rank the span that will play.

Pexels is called only when PEXELS_API_KEY is set. Openverse video is not a source.
A relative or a named building is searched and then not scheduled.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from analysis.query_guide import expand
from analysis.subject_rules import records
from analysis.uniqueness import asset_identity, file_sha256
from assets.concepts import denies
from assets.library import Catalogue
from assets.providers import openverse_status, pexels_status, search_pexels
from assets.queries import description_block, retrieval_plan
from assets.video_commons import _fill_public_domain_url, accept_video_candidate, download_video, search_videos
from assets.visual_rank import black_fraction, framing_decision, load_rgb, sample_frames

MIN_SCORE = 0.22
MAX_INSERT = 12.0
MAX_DOWNLOADS = 4
# Words that appear in almost any caption. They must not accept a file on their own.
_GENERIC = {
    "student", "person", "people", "hand", "writing", "open", "stack", "studying",
    "reading", "using", "watching", "close", "view", "printed", "table", "desk",
    "room", "wearing", "man", "woman", "notes", "process", "doctor", "answer",
    "school", "large", "with", "from", "medical", "personal", "equipment",
}
_CONFLICTS = {
    "study process": ("sheriff", "deputy", "police", "shooting", "custody", "disability", "dyslexia"),
    "studying": ("sheriff", "deputy", "police", "shooting", "disability", "dyslexia", "vulture"),
    "book": ("sheriff", "deputy", "police", "disability"),
    "textbook": ("sheriff", "deputy", "police", "elitebook"),
    "YouTube": ("shooting", "police", "patrol"),
    "laptop": ("shooting", "police"),
    "exam paper": ("sheriff", "deputy", "police", "shooting"),
    "essay paper": ("sheriff", "deputy", "police"),
    "GS papers": ("sheriff", "deputy", "police"),
}


def provider_status() -> dict:
    return {
        "commons": {"provider": "commons", "available": True, "endpoint": "https://commons.wikimedia.org/w/api.php"},
        "openverse": openverse_status(),
        "pexels": pexels_status(),
    }


def build_plan(transcript: dict) -> list[dict]:
    """Opportunity records plus the deterministic query guide."""
    rows = records(transcript)
    for row in rows:
        guide = expand(row["text"], row["subjects"])
        row["query_source"] = guide["source"]
        by_name = {item.get("normalized"): item for item in guide["subjects"]}
        for subject in row["subjects"]:
            guided = by_name.get(subject["normalized"]) or {}
            if guided.get("queries"):
                subject["queries"] = guided["queries"]
    return rows


def select_clips(
    video_id: str,
    transcript: dict,
    speech: str,
    cache: Path,
    catalogue: Catalogue,
    ranker=None,
    board_terms: list[str] | None = None,
    sleep=time.sleep,
) -> tuple[list[dict], list[dict]]:
    """Download and rank placeable scenes. Log every sentence that stays on the teacher."""
    cache.mkdir(parents=True, exist_ok=True)
    board = {term.lower() for term in (board_terms or [])}
    log: list[dict] = []
    chosen: list[dict] = []
    used: set[str] = set()
    logged_subjects: set[str] = set()
    for row in build_plan(transcript):
        placed_here = False
        for subject in _subjects_to_search(row):
            if _blocked_subject(subject, board):
                log.append(_note(video_id, row, subject, "the board already shows this, so the teacher stays"))
                continue
            if subject["normalized"] == "lecture" and denies(row["text"], "lecture"):
                log.append(_note(video_id, row, subject, "he says he did not use the lecture, so a class is not shown as his"))
                continue
            if _extend(chosen, subject, row):
                placed_here = True
                break
            asset = _fill(video_id, row, subject, speech, cache, catalogue, ranker, used, sleep, log, board)
            if asset is None:
                continue
            chosen.append(asset)
            placed_here = True
            break
        if placed_here:
            continue
        for subject in row["subjects"]:
            if subject.get("schedule") == "place":
                continue
            name = str(subject.get("normalized") or "")
            if name not in logged_subjects:
                logged_subjects.add(name)
                _search_log_only(subject, log, sleep)
            log.append(_note(video_id, row, subject, subject.get("reason") or "left on the teacher"))
        if not row["subjects"]:
            log.append(_note(video_id, row, {}, "no concrete subject in the sentence"))
    _trim_overlaps(chosen)
    return chosen, log


def _extend(chosen: list[dict], subject: dict, row: dict) -> bool:
    """Keep one clip playing across a repeated mention instead of loading it twice."""
    if not chosen or chosen[-1].get("subject") != subject.get("normalized"):
        return False
    if chosen[-1].get("rejected"):
        return False
    start, end = chosen[-1]["source_window"]
    if float(row["start"]) - float(end) > 1.2:
        return False
    limit = float(chosen[-1].get("play_limit") or MAX_INSERT)
    extended = min(float(row["end"]), float(start) + limit)
    if extended - float(start) < 2:
        return False
    chosen[-1]["source_window"][1] = round(extended, 3)
    return True


_CLINICAL = {"hospital", "COVID", "protective clothing", "face mask", "n95 mask"}


def _subjects_to_search(row: dict) -> list[dict]:
    """Clinical action before a book on a mixed sentence. A denied newspaper is not filled by shelves."""
    subjects = _placeable(row)
    if (row.get("scene") or {}).get("overlay") == "negation_cross":
        return [item for item in subjects if item.get("normalized") == "newspaper"]
    clinical = [item for item in subjects if item.get("normalized") in _CLINICAL]
    other = [item for item in subjects if item.get("normalized") not in _CLINICAL]
    return clinical + other


def _placeable(row: dict) -> list[dict]:
    return [subject for subject in row["subjects"] if subject.get("schedule") == "place"]


def _blocked_subject(subject: dict, board: set[str]) -> bool:
    """The circled mistakes line stays on the teacher. Other board words can still take a clip."""
    del board
    return "mistake" in str(subject.get("normalized") or "")


def _fill(video_id, row, subject, speech, cache, catalogue, ranker, used, sleep, log, board: set[str] | None = None) -> dict | None:
    plan = retrieval_plan(subject.get("query") or subject["normalized"], row["text"], subject.get("term") or "")
    print(f"search {video_id} {row['start']:.1f} {subject['normalized']}", flush=True)
    prompt = plan["prompt"]
    pool: list[dict] = []
    seen: set[str] = set()
    for tag in _library_tags(subject, plan):
        for saved in catalogue.search_tag(tag):
            blob = f"{saved.get('title') or ''} {saved.get('description') or ''}".lower()
            if not _word(blob, tag):
                continue
            _consider_saved(saved, plan, subject, seen, used, pool)
    take = min(MAX_INSERT, max(2.5, float(row["end"]) - float(row["start"])))
    best = _best_of(pool, take, prompt, ranker, cache, log)
    if best is None or best[0] < MIN_SCORE:
        downloads = 0
        for query in subject.get("queries") or [subject.get("query")]:
            if downloads >= MAX_DOWNLOADS:
                break
            for offset in (0, 8, 16):
                found = _search(query, offset, log, sleep)
                for hit in found:
                    if downloads >= MAX_DOWNLOADS:
                        break
                    if not _keep_hit(hit, plan, subject, row["text"], seen, used, log):
                        continue
                    local = _download(hit, cache, log, sleep)
                    if local is None:
                        continue
                    hit["local_path"] = str(local)
                    hit["provider"] = hit.get("provider") or "commons"
                    pool.append(hit)
                    downloads += 1
                if downloads >= MAX_DOWNLOADS:
                    break
        best = _best_of(pool, take, prompt, ranker, cache, log)
    if not pool:
        log.append(_note(video_id, row, subject, "no licensed candidate matched the scene"))
        return None
    if best is None:
        log.append(_note(video_id, row, subject, "no clip cleared the picture check"))
        return None
    score, clip_in, quality, candidate = best
    if score < MIN_SCORE:
        log.append(_note(video_id, row, subject, f"best score {score:.3f} is under {MIN_SCORE}"))
        return None
    identity = asset_identity(candidate)
    if identity in used:
        log.append(_note(video_id, row, subject, "that source was already used in this lesson"))
        return None
    used.add(identity)
    path = Path(candidate["local_path"])
    catalogue.upsert(
        {
            **candidate,
            "sha256": file_sha256(str(path)),
            "tags": [subject["normalized"], subject.get("query") or ""],
            "subjects": [subject["normalized"]],
            "relevance": round(score, 4),
            "quality": quality,
        }
    )
    term = _match_term(subject, speech, board or set(), row["text"])
    file_duration = _duration(path) or take
    play_limit = min(take, max(2.0, file_duration - float(clip_in) - 0.2))
    return {
        "video_id": video_id,
        "kind": "video",
        "match_terms": [term],
        "title": candidate.get("title"),
        "page_url": candidate.get("page_url"),
        "download_url": candidate.get("download_url"),
        "license": candidate.get("license"),
        "license_url": candidate.get("license_url"),
        "artist": candidate.get("artist"),
        "description": candidate.get("description"),
        "local_path": str(path),
        "sha256": file_sha256(str(path)),
        "source_window": [round(float(row["start"]), 3), round(min(float(row["end"]), float(row["start"]) + play_limit), 3)],
        "play_limit": round(play_limit, 3),
        "clip_in": round(clip_in, 3) if clip_in > 0.05 else 0.05,
        "duration_seconds": _duration(path),
        "relevance": round(score, 4),
        "quality": quality,
        "rejected": False,
        "reason": f"Ranked {score:.3f} for “{prompt}”. The sampled frames are inside the played span.",
        "provider": candidate.get("provider") or "commons",
        "subject": subject["normalized"],
        "graphic": "negation_cross" if (row.get("scene") or {}).get("overlay") == "negation_cross" and subject.get("normalized") == "newspaper" else "",
        "visual_evidence": candidate.get("_evidence") or [],
        "scene": row.get("scene") or {},
    }


def _search(query: str, offset: int, log: list[dict], sleep) -> list[dict]:
    found: list[dict] = []
    try:
        found.extend(search_videos(query, limit=6, offset=offset))
    except Exception as error:  # noqa: BLE001
        log.append({"query": query, "offset": offset, "provider": "commons", "reason": type(error).__name__})
    sleep(0.8)
    if pexels_status()["available"]:
        page = 1 + (offset // 6)
        try:
            found.extend(search_pexels(query, limit=6, page=page))
        except Exception as error:  # noqa: BLE001
            log.append({"query": query, "page": page, "provider": "pexels", "reason": type(error).__name__})
        sleep(0.4)
    return found


def _search_log_only(subject: dict, log: list[dict], sleep) -> None:
    query = (subject.get("queries") or [subject.get("query") or subject.get("normalized")])[0]
    try:
        hits = search_videos(query, limit=4, offset=0)
    except Exception as error:  # noqa: BLE001
        log.append({"query": query, "provider": "commons", "reason": type(error).__name__, "scheduled": False})
        return
    log.append(
        {
            "query": query,
            "provider": "commons",
            "seen": len(hits),
            "scheduled": False,
            "reason": subject.get("reason") or "searched and not scheduled",
        }
    )
    sleep(0.8)


def _keep_hit(hit, plan, subject, sentence, seen, used, log) -> bool:
    page = hit.get("page_url") or ""
    if not page or page in seen or asset_identity(hit) in used:
        return False
    blob = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
    blocked = description_block(blob, plan)
    if blocked:
        log.append({"title": hit.get("title"), "reason": blocked})
        return False
    _fill_public_domain_url(hit)
    reason = _licence(hit, plan, subject)
    if reason:
        log.append({"title": hit.get("title"), "reason": reason})
        return False
    seen.add(page)
    return True


def _licence(hit: dict, plan: dict, subject: dict) -> str | None:
    if hit.get("provider") == "pexels":
        if hit.get("license_url") != "https://www.pexels.com/license/":
            return "pexels license url is missing"
        if not hit.get("download_url"):
            return "no download url"
        return _scene_conflict(_blob(hit), subject, plan)
    phrases: list[str] = []
    for phrase in list(subject.get("queries") or []) + list(plan.get("phrases") or []) + list(plan.get("anchors") or []):
        text = str(phrase).lower().strip()
        if text and text not in phrases and text not in _GENERIC:
            phrases.append(text)
    last = "description does not contain the speech query"
    blob = _blob(hit)
    for phrase in phrases:
        reason = accept_video_candidate(hit, phrase, "")
        if reason is None or (reason.startswith("mime video/mpeg") and "pen" in blob):
            return _scene_conflict(blob, subject, plan)
        last = reason
    return last


def _scene_conflict(blob: str, subject: dict, plan: dict) -> str | None:
    """Reject a metadata hit that shows a different activity from the sentence."""
    normalized = str(subject.get("normalized") or "")
    for word in _CONFLICTS.get(normalized, ()):
        if word in blob:
            return f"the description is about {word}, not {normalized}"
    prompt = str(plan.get("prompt") or "").lower()
    clinical = any(word in prompt for word in ("patient", "ward", "examining", "gown"))
    if clinical and "entrance" in blob and not any(word in blob for word in ("patient", "ward", "ppe", "doctor", "bed", "gown")):
        return "an exterior entrance does not show the clinical activity"
    if normalized == "YouTube":
        if any(word in blob for word in ("wikisource", "pdf", "firefox", "wikipedia", "youtube-dl", "download", "copyright", "licensing", "farmer")):
            return "a document or explainer is not opening YouTube"
        if any(word in blob for word in ("conference", "interview", "keynote", "panel", "podcast", "migrant", "deport")):
            return "a talk about YouTube is not opening the app"
        if not re.search(r"\byoutube\b", blob) or not any(word in blob for word in ("phone", "smartphone", "mobile")):
            return "the clip does not show the YouTube app on a phone"
    if normalized == "hospital" and any(word in blob for word in ("ebola", "doffing", "ppe training")):
        return "protective-equipment training is not a hospital visit"
    return None


def _blob(hit: dict) -> str:
    return f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()


def _word(blob: str, word: str) -> bool:
    import re

    return re.search(rf"\b{re.escape(word.lower())}\b", blob) is not None


def _library_tags(subject: dict, plan: dict) -> list[str]:
    tags = [str(subject.get("normalized") or "")]
    tags.extend(str(anchor) for anchor in plan.get("anchors") or [])
    tags.extend(word for query in (subject.get("queries") or []) for word in str(query).split())
    kept: list[str] = []
    for tag in tags:
        text = tag.lower().strip()
        if len(text) >= 4 and text not in _GENERIC and text not in kept:
            kept.append(text)
    return kept[:6]


def _best_of(pool, take, prompt, ranker, cache, log):
    best = None
    for candidate in pool:
        if "_scored" in candidate:
            if candidate["_scored"] is None:
                continue
            score, clip_in, quality = candidate["_scored"][0], candidate["_scored"][1], candidate["_scored"][2]
            scored = candidate["_scored"]
            candidate.setdefault("_evidence", list(scored[3]) if len(scored) > 3 else [])
        else:
            scored = _score_candidate(candidate, take, prompt, ranker, cache)
            if scored is None:
                log.append({"title": candidate.get("title"), "reason": "the played span failed the picture check"})
                candidate["_scored"] = None
                continue
            candidate["_scored"] = scored
            score, clip_in, quality = scored[0], scored[1], scored[2]
            candidate["_evidence"] = list(scored[3]) if len(scored) > 3 else []
        if best is None or score > best[0]:
            best = (score, clip_in, quality, candidate)
    return best


def _download(hit: dict, cache: Path, log: list[dict], sleep) -> Path | None:
    mime = (hit.get("mime") or "").lower()
    suffix = ".webm" if "webm" in mime else ".ogv" if "ogg" in mime else ".mpg" if "mpeg" in mime else ".mp4"
    name = "".join(ch.lower() if ch.isalnum() else "-" for ch in (hit.get("title") or "clip"))[:48].strip("-") or "clip"
    dest = cache / f"{name}{suffix}"
    if dest.is_file() and dest.stat().st_size > 50_000:
        return dest
    try:
        download_video(hit["download_url"], dest)
    except Exception as error:  # noqa: BLE001
        log.append({"title": hit.get("title"), "reason": f"download failed: {type(error).__name__}"})
        return None
    sleep(0.4)
    return dest if dest.is_file() else None


def _consider_saved(saved: dict, plan: dict, subject: dict, seen: set[str], used: set[str], pool: list[dict]) -> None:
    path = saved.get("local_path") or ""
    if not path or not Path(path).is_file():
        return
    if asset_identity(saved) in used:
        return
    page = saved.get("page_url") or path
    if page in seen:
        return
    blob = _blob(saved)
    if description_block(blob, plan) or _scene_conflict(blob, subject, plan):
        return
    seen.add(page)
    pool.append(dict(saved))


def window_starts(duration: float, take: float) -> list[float]:
    """In-points whose sampled frames are the frames that would be played."""
    play = min(take, max(2.0, duration - 0.2))
    room = duration - play
    if room > 1.5:
        return [0.0, round(room * 0.45, 2), round(room * 0.75, 2)]
    return [0.0]


def _score_candidate(candidate: dict, take: float, prompt: str, ranker, cache: Path):
    path = Path(candidate.get("local_path") or "")
    if not path.is_file():
        return None
    duration = _duration(path) or take
    play = min(take, max(2.0, duration - 0.2))
    if duration > 40:
        starts = [1.0, 12.0, min(40.0, max(0.0, duration - play))]
    else:
        starts = window_starts(duration, take)
    starts = [start for start in starts if 0 <= start < max(duration - 0.4, 0.1)] or [0.0]
    best = None
    for start in starts:
        frames = sample_frames(path, 3, cache / "frames" / path.stem, start=start, end=start + play)
        if len(frames) < 2:
            continue
        rgb = load_rgb(frames[len(frames) // 2])
        dark = black_fraction(rgb)
        if dark >= 0.72:
            continue
        if ranker is not None:
            try:
                score = float(ranker.score_frames(frames, prompt))
            except Exception:
                score = 0.0
        else:
            score = 0.35
        score += 0.02 if framing_decision(rgb) == "keep" else 0.0
        if any(word in prompt.lower() for word in ("gown", "protective", "ppe")):
            if 0.25 <= dark <= 0.70:
                score += 0.05
            elif dark < 0.08:
                score -= 0.06
        if best is None or score > best[0]:
            best = (score, start, round(1.0 - black_fraction(rgb), 4), [str(frame) for frame in frames])
    return best


def _match_term(subject: dict, speech: str, board: set[str], sentence: str) -> str:
    lowered = speech.lower()
    tokens = [subject.get("term"), subject.get("normalized")]
    tokens.extend(str(subject.get("query") or "").split())
    for query in subject.get("queries") or []:
        tokens.extend(str(query).lower().split())
    tokens.extend(sentence.lower().split())
    for token in tokens:
        text = str(token or "").lower()
        if len(text) >= 4 and text not in board and text in lowered:
            return text
    return str(subject.get("term") or "clip")


def _duration(path: Path) -> float | None:
    import json
    import subprocess

    from render.outro import tool

    probe = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    raw = (json.loads(probe.stdout).get("format") or {}).get("duration")
    return round(float(raw), 3) if raw else None


def _trim_overlaps(rows: list[dict]) -> None:
    rows.sort(key=lambda item: item["source_window"][0])
    previous_end = -1.0
    for row in rows:
        start, end = row["source_window"]
        if start < previous_end:
            start = round(previous_end + 0.04, 3)
        if end - start < 2.0:
            row["rejected"] = "the interval collided with an earlier clip"
            continue
        row["source_window"] = [start, round(end, 3)]
        previous_end = end


def _note(video_id, row, subject, reason: str) -> dict:
    return {
        "video_id": video_id,
        "start": row.get("start"),
        "end": row.get("end"),
        "subject": subject.get("normalized"),
        "reason": reason,
        "scheduled": False,
    }
