"""Bounded retrieval for one selected phrase.

A cached acceptance is used only when this search returns that page for the
same object. Any other file is scheduled only after its played span is ranked
and the verifier accepts it. A provider error is not an acceptance.
"""

from __future__ import annotations

import re
import urllib.error
from pathlib import Path

from assets.video_commons import accept_video_candidate
from broll_intel.gemini_api import GeminiError
from broll_intel.schema import VerificationResult, accept_verification

MIN_SCORE = 0.22
MAX_DOWNLOADS = 2
MAX_VERIFIES = 2
_WORD = re.compile(r"[a-z0-9]{4,}")
_SOFT = "description does not contain the speech query"
_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+|token\s*[:=]\s*\S+)"
)


def fill_selected(
    decision: dict,
    *,
    search,
    download,
    rank,
    verify,
    prior: dict,
    blocked_pages: set[str],
    cache: Path,
    used_pages: set[str] | None = None,
    max_downloads: int = MAX_DOWNLOADS,
    max_verifies: int = MAX_VERIFIES,
    max_searches: int | None = None,
    max_ranks: int | None = None,
) -> dict:
    """Search, then try a few licensed spans. The spoken line is not changed."""
    feasibility = decision["feasibility"]
    queries = list(feasibility.get("search_queries") or [])[:4]
    hits, requests = _collect(
        queries,
        decision["spoken_text"],
        feasibility.get("forbidden_visual_elements") or [],
        search,
        max_searches=max_searches,
    )
    used = used_pages if used_pages is not None else set()
    outcome = {
        "search": hits,
        "search_requests": requests,
        "attempts": [],
        "retrieval_status": "unfilled",
        "selected_asset_id": "",
        "selected_source_in": None,
        "selected_source_out": None,
        "verification_evidence": {"references": (decision.get("editorial") or {}).get("examples") or []},
        "catalogue_row": None,
        "reason": "Live search did not produce a verified span for this phrase.",
    }
    cached = _prior_hit(hits, prior, feasibility.get("object") or "", blocked_pages, used)
    if cached is not None:
        return _from_prior(outcome, decision, cached, prior)
    downloads = 0
    verifies = 0
    ranks = 0
    saw_reject = False
    saw_review = False
    saw_api = False
    take = max(0.4, float(decision["speech_end"]) - float(decision["speech_start"]))
    for hit in _candidates(hits, blocked_pages, used):
        if downloads >= max_downloads or verifies >= max_verifies:
            break
        if max_ranks is not None and ranks >= max_ranks:
            break
        local = hit.get("local_path") or ""
        if not local or not Path(local).is_file():
            local_path = download(hit, cache)
            downloads += 1
            if local_path is None:
                outcome["attempts"].append({"page_url": hit.get("page_url"), "status": "download_failed"})
                continue
            local = str(local_path)
            hit["local_path"] = local
        prompt = feasibility.get("visual_brief") or " ".join(queries[:1])
        if max_ranks is not None:
            ranks += 1
        try:
            ranked = rank(local, prompt, take, cache)
        except Exception as error:  # noqa: BLE001
            outcome["attempts"].append(
                {"page_url": hit.get("page_url"), "status": "rank_failed", "detail": type(error).__name__}
            )
            continue
        if not ranked or float(ranked[0]) < MIN_SCORE:
            score = None if not ranked else round(float(ranked[0]), 4)
            outcome["attempts"].append({"page_url": hit.get("page_url"), "status": "below_rank", "score": score})
            continue
        score, clip_in = float(ranked[0]), float(ranked[1])
        frames = list(ranked[3] if len(ranked) >= 4 else ranked[2])
        if len(frames) < 2:
            outcome["attempts"].append({"page_url": hit.get("page_url"), "status": "no_frames"})
            continue
        verifies += 1
        try:
            result = verify(frames, decision)
            if not isinstance(result, VerificationResult):
                result = VerificationResult.model_validate(result)
        except GeminiError as error:
            saw_api = True
            outcome["attempts"].append(
                {"page_url": hit.get("page_url"), "status": "API_UNAVAILABLE", "detail": str(error)[:240]}
            )
            continue
        except Exception as error:  # noqa: BLE001
            outcome["attempts"].append(
                {"page_url": hit.get("page_url"), "status": "verify_failed", "detail": type(error).__name__}
            )
            continue
        gated = accept_verification(result)
        outcome["attempts"].append(
            {
                "page_url": hit.get("page_url"),
                "status": gated.decision,
                "score": round(float(score), 4),
                "clip_in": round(float(clip_in), 3),
                "reason": gated.reason or gated.visual_summary,
            }
        )
        if gated.decision == "REJECT":
            saw_reject = True
            blocked_pages.add(hit.get("page_url") or "")
            continue
        if gated.decision != "ACCEPT":
            saw_review = True
            continue
        return _accepted(outcome, decision, hit, gated, score, clip_in, frames, local)
    if saw_api and not saw_reject:
        outcome["retrieval_status"] = "API_UNAVAILABLE"
        outcome["reason"] = "The verifier did not answer. The span was not accepted."
    elif saw_reject:
        outcome["retrieval_status"] = "rejected"
        outcome["reason"] = "Verified spans did not show the requested subject and action."
    elif saw_review:
        outcome["retrieval_status"] = "review"
        outcome["reason"] = "The verifier left the span unresolved. The teacher stays."
    elif outcome["retrieval_status"] == "unfilled":
        outcome["reason"] = _search_diagnosis(requests, outcome["attempts"]) or outcome["reason"]
    return outcome


def _collect(
    queries: list[str],
    speech: str,
    forbidden: list[str],
    search,
    max_searches: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Search every distinct stored query before any shortened fallback."""
    found: list[dict] = []
    requests: list[dict] = []
    seen: set[str] = set()
    banned = [item.lower() for item in forbidden if item]
    used = 0
    stored: list[str] = []
    for query in queries:
        text = " ".join(str(query).split())
        if text and text not in stored:
            stored.append(text)
    sent: list[str] = []

    def once(query: str):
        nonlocal used
        if max_searches is not None and used >= max_searches:
            return []
        used += 1
        return search(query)

    for query in stored:
        if max_searches is not None and used >= max_searches:
            _skipped(requests, query, "stored")
            break
        _absorb(query, speech, banned, once, found, seen, requests, "stored")
        sent.append(query)

    if not _downloadable(found):
        for query in stored:
            shorter = _trim_query(query)
            if not _distinct_fallback(shorter, stored, sent):
                continue
            if max_searches is not None and used >= max_searches:
                _skipped(requests, shorter, "fallback")
                break
            _absorb(shorter, speech, banned, once, found, seen, requests, "fallback")
            sent.append(shorter)
            if _downloadable(found):
                break
    return found, requests


def _absorb(
    query: str,
    speech: str,
    banned: list[str],
    search,
    found: list[dict],
    seen: set[str],
    requests: list[dict],
    role: str,
) -> None:
    try:
        raw = search(query)
        if raw is None:
            raw = []
    except Exception as error:  # noqa: BLE001
        found.append({"query": query, "error": _public_text(error)[:240], "metadata_ok": False, "license_ok": False})
        requests.append(_request_record(requests, query, role, error=error))
        return
    meta = getattr(raw, "search_meta", None) or {}
    before = len(found)
    for hit in raw:
        page = hit.get("page_url") or ""
        if page and page in seen:
            continue
        if page:
            seen.add(page)
        reason = accept_video_candidate(hit, query, speech)
        blob = f"{hit.get('title') or ''} {hit.get('description') or ''}".lower()
        if reason is None and any(phrase in blob for phrase in banned):
            reason = "description contains a forbidden scene"
        soft = reason == _SOFT
        found.append(
            {
                "query": query,
                "page_url": page,
                "title": hit.get("title"),
                "license": hit.get("license"),
                "license_url": hit.get("license_url"),
                "license_url_note": hit.get("license_url_note") or "",
                "creator": hit.get("artist"),
                "download_url": hit.get("download_url"),
                "mime": hit.get("mime"),
                "size": hit.get("size"),
                "duration": hit.get("duration"),
                "description": (hit.get("description") or "")[:400],
                "local_path": hit.get("local_path") or "",
                "metadata_ok": reason is None,
                "license_ok": reason is None or soft,
                "shared_terms": _shared(query, blob),
                "reject_reason": reason,
            }
        )
    added = found[before:]
    requests.append(_request_record(requests, query, role, raw=raw, meta=meta, added=added))


def _skipped(requests: list[dict], query: str, role: str) -> None:
    requests.append(_request_record(requests, query, role, skipped=True))


def _request_record(
    requests: list[dict],
    query: str,
    role: str,
    *,
    error: BaseException | None = None,
    raw=None,
    meta: dict | None = None,
    added: list[dict] | None = None,
    skipped: bool = False,
) -> dict:
    transport = dict(getattr(error, "search_meta", {}) or {}) if error is not None else dict(meta or {})
    sent = transport.get("sent_query") or f"{query} filetype:video"
    if skipped or (error is not None and type(error).__name__ == "BudgetExhausted"):
        outcome = "budget_exhausted"
        succeeded = False
        http_status = None
        raw_count = 0
        candidates: list[dict] = []
    elif error is not None:
        outcome = _error_outcome(error)
        succeeded = False
        http_status = transport.get("http_status")
        if http_status is None and isinstance(error, urllib.error.HTTPError):
            http_status = error.code
        raw_count = 0
        candidates = []
    else:
        raw_count = transport.get("raw_count")
        if raw_count is None:
            raw_count = len(list(raw or []))
        http_status = transport.get("http_status")
        succeeded = True
        candidates = [_candidate_diag(hit) for hit in (added or [])]
        accepted = sum(1 for item in candidates if item["in_candidate_pool"])
        outcome = "zero_results" if raw_count == 0 else "filtered" if accepted == 0 else "candidates"
    if error is None and not skipped:
        accepted_count = sum(1 for item in candidates if item["in_candidate_pool"])
    else:
        accepted_count = 0
    return {
        "sequence": len(requests) + 1,
        "sent_query": sent,
        "query_role": role,
        "http_status": http_status,
        "succeeded": succeeded,
        "outcome": outcome,
        "raw_count": raw_count,
        "accepted_count": accepted_count,
        "rejections": _rejection_counts(candidates),
        "candidates": candidates,
    }


def _candidate_diag(hit: dict) -> dict:
    reason = hit.get("reject_reason") or ""
    pooled = bool(
        hit.get("license_ok")
        and hit.get("page_url")
        and (hit.get("metadata_ok") or int(hit.get("shared_terms") or 0) >= 2)
    )
    return {
        "page_url": hit.get("page_url") or "",
        "license": hit.get("license") or "",
        "license_url": hit.get("license_url") or "",
        "license_url_note": hit.get("license_url_note") or "",
        "mime": hit.get("mime") or "",
        "size": hit.get("size"),
        "duration": hit.get("duration") if isinstance(hit.get("duration"), (int, float)) and not isinstance(hit.get("duration"), bool) else None,
        "reject_reason": _public_text(reason) if reason else "",
        "filter": _filter_bucket(reason) or "",
        "in_candidate_pool": pooled,
    }


def _filter_bucket(reason: str) -> str | None:
    text = reason.lower()
    if not text:
        return None
    if "license url" in text:
        return "license_url"
    if "download url" in text:
        return "download_url"
    if "mime" in text or "not a video" in text:
        return "mime"
    if "40 mb" in text or "too small" in text or "size" in text:
        return "file_size"
    if "description" in text or "forbidden scene" in text or "denied action" in text or "different subject" in text:
        return "description"
    if "license" in text:
        return "license"
    return "other"


def _rejection_counts(candidates: list[dict]) -> dict:
    counts = {"license": 0, "license_url": 0, "download_url": 0, "mime": 0, "file_size": 0, "description": 0, "other": 0}
    for item in candidates:
        if item.get("in_candidate_pool"):
            continue
        bucket = item.get("filter") or "other"
        if bucket not in counts:
            bucket = "other"
        counts[bucket] += 1
    return counts


def _error_outcome(error: BaseException) -> str:
    if type(error).__name__ == "BudgetExhausted":
        return "budget_exhausted"
    if isinstance(error, urllib.error.HTTPError):
        return "http_error"
    if isinstance(error, urllib.error.URLError):
        return "transport_error"
    return "request_failed"


def _public_text(value: object) -> str:
    return _SECRET.sub("[redacted]", str(value))


def _search_diagnosis(requests: list[dict], attempts: list[dict]) -> str:
    if not requests:
        return ""
    outcomes = [item.get("outcome") for item in requests]
    if "candidates" in outcomes:
        if attempts and all(item.get("status") == "download_failed" for item in attempts):
            return "Search produced candidates, but no candidate was downloaded."
        return ""
    if "filtered" in outcomes:
        return "Commons search succeeded but every result was filtered out."
    if "zero_results" in outcomes and "http_error" not in outcomes and "transport_error" not in outcomes:
        return "Commons search succeeded and returned no videos."
    if outcomes and all(item == "budget_exhausted" for item in outcomes):
        return "Search was skipped because the request budget was exhausted."
    if "http_error" in outcomes or "transport_error" in outcomes:
        return "The Commons search request failed."
    return ""


def _downloadable(hits: list[dict]) -> bool:
    return any(
        hit.get("license_ok")
        and hit.get("page_url")
        and (hit.get("metadata_ok") or int(hit.get("shared_terms") or 0) >= 2)
        for hit in hits
    )


def _distinct_fallback(shorter: str, stored: list[str], sent: list[str]) -> bool:
    """A fallback is used only when it is not a repeat of a stored query or a query already sent."""
    if not shorter:
        return False
    if shorter in stored or shorter in sent:
        return False
    shorter_words = _content_words(shorter)
    if not shorter_words:
        return False
    for previous in [*stored, *sent]:
        if _content_words(previous) == shorter_words:
            return False
    return True


def _content_words(text: str) -> list[str]:
    return [word for word in text.lower().replace("-", " ").split() if len(word) >= 4]


def _trim_query(query: str) -> str:
    """Drop a trailing place phrase, or a leading 'person' or 'student', when the full query is too narrow."""
    text = " ".join(query.split())
    for marker in (" on ", " at ", " in "):
        if marker in text:
            head = text.split(marker)[0].strip()
            if len(head.split()) >= 3:
                return head
    words = text.split()
    if words and words[0].lower() in {"person", "student", "close"} and len(words) > 3:
        return " ".join(words[1:])
    return ""


def _prior_hit(hits: list[dict], prior: dict, object_name: str, blocked: set[str], used: set[str]) -> dict | None:
    for hit in hits:
        page = hit.get("page_url") or ""
        cached = prior.get(page) or {}
        if not hit.get("license_ok") or page in blocked or page in used:
            continue
        if cached.get("decision") == "ACCEPT" and cached.get("object") == object_name and cached.get("local_path"):
            if Path(cached["local_path"]).is_file():
                return hit
    return None


def _candidates(hits: list[dict], blocked: set[str], used: set[str]) -> list[dict]:
    pool = []
    for hit in hits:
        page = hit.get("page_url") or ""
        if not hit.get("license_ok") or not page or page in blocked or page in used:
            continue
        if not hit.get("metadata_ok") and int(hit.get("shared_terms") or 0) < 2:
            continue
        size = hit.get("size")
        if isinstance(size, int) and not isinstance(size, bool) and size > 40_000_000:
            continue
        pool.append(hit)
    pool.sort(
        key=lambda hit: (
            bool(hit.get("metadata_ok")),
            int(hit.get("shared_terms") or 0),
        ),
        reverse=True,
    )
    return pool


def _from_prior(outcome: dict, decision: dict, hit: dict, prior: dict) -> dict:
    page = hit["page_url"]
    evidence = prior[page]
    clip_in = float(evidence.get("source_in") or 0.05)
    if clip_in < 0.05:
        clip_in = 0.05
    source_out = evidence.get("source_out")
    outcome["retrieval_status"] = "verified"
    outcome["selected_asset_id"] = page
    outcome["selected_source_in"] = round(clip_in, 3)
    outcome["selected_source_out"] = source_out
    outcome["reason"] = "This search returned a span already accepted for the same object."
    outcome["verification_evidence"] = {
        "page_url": page,
        "found_by_live_search": True,
        "verification_reused": True,
        "query": hit.get("query"),
        "license": hit.get("license") or evidence.get("license"),
        "creator": hit.get("creator"),
        "prior_decision": "ACCEPT",
        "references": outcome["verification_evidence"].get("references") or [],
    }
    outcome["catalogue_row"] = _catalogue_row(decision, hit, evidence["local_path"], clip_in, evidence.get("sha256") or "")
    outcome["attempts"].append({"page_url": page, "status": "reused_accept"})
    return outcome


def _accepted(outcome, decision, hit, gated, score, clip_in, frames, local: str) -> dict:
    page = hit.get("page_url") or ""
    start = float(clip_in)
    if start < 0.05:
        start = 0.05
    speech = max(0.4, float(decision["speech_end"]) - float(decision["speech_start"]))
    source_out = round(start + speech, 3)
    outcome["retrieval_status"] = "verified"
    outcome["selected_asset_id"] = page
    outcome["selected_source_in"] = round(start, 3)
    outcome["selected_source_out"] = source_out
    outcome["reason"] = gated.reason or gated.visual_summary or "The played span shows the requested subject and action."
    outcome["verification_evidence"] = {
        "page_url": page,
        "found_by_live_search": True,
        "verification_reused": False,
        "query": hit.get("query"),
        "license": hit.get("license"),
        "creator": hit.get("creator"),
        "clip_score": round(float(score), 4),
        "frames": [str(frame) for frame in frames],
        "gemini": gated.model_dump(),
        "references": outcome["verification_evidence"].get("references") or [],
    }
    outcome["catalogue_row"] = _catalogue_row(decision, hit, local, start, "")
    return outcome


def _catalogue_row(decision: dict, hit: dict, local: str, clip_in: float, digest: str) -> dict:
    speech_start = float(decision["speech_start"])
    speech_end = float(decision["speech_end"])
    play = max(0.4, speech_end - speech_start)
    return {
        "video_id": decision.get("lesson_id"),
        "kind": "video",
        "match_terms": _match_terms(decision),
        "title": hit.get("title"),
        "page_url": hit.get("page_url"),
        "download_url": hit.get("download_url"),
        "license": hit.get("license"),
        "license_url": hit.get("license_url"),
        "artist": hit.get("creator"),
        "description": hit.get("description") or "",
        "local_path": local,
        "sha256": digest,
        "source_window": [round(speech_start, 3), round(min(speech_end, speech_start + play), 3)],
        "play_limit": round(play, 3),
        "clip_in": round(clip_in, 3),
        "provider": "commons",
        "subject": decision["feasibility"].get("object") or "",
        "graphic": "negation_cross" if decision["feasibility"].get("overlay_instructions") == "negation_cross" else "",
        "rejected": False,
    }


def _match_terms(decision: dict) -> list[str]:
    from transcription.romanize import transliterate

    text = transliterate(decision.get("spoken_text") or "").lower()
    tokens = _WORD.findall(text)
    blocked = {item.lower() for item in decision.get("blocked_terms") or []}
    preferred = _WORD.findall(str(decision["feasibility"].get("object") or "").lower())
    for token in preferred + tokens:
        if token in tokens and token not in blocked:
            return [token]
    return []


def _shared(query: str, blob: str) -> int:
    terms = [term for term in _WORD.findall(query.lower()) if term]
    return sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\b", blob))
