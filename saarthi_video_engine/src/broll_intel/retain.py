"""Keep a previously accepted cutaway when a later selector does not schedule one.

A rejection stays a rejection. An API failure is neither an acceptance nor a
reason to drop an earlier acceptance of a different check. The spoken line is
not rewritten.
"""

from __future__ import annotations

from pathlib import Path


def reusable(row: dict | None) -> tuple[bool, str]:
    """A prior row can be played again only when the file and the acceptance are still present."""
    if not row:
        return False, "no prior decision"
    if row.get("retrieval_status") == "API_UNAVAILABLE":
        return False, "an API failure is not an acceptance"
    if row.get("retrieval_status") == "rejected":
        return False, "the prior visual was rejected"
    gemini = (row.get("verification_evidence") or {}).get("gemini") or {}
    if row.get("retrieval_status") != "verified" or gemini.get("decision") != "ACCEPT":
        return False, "the prior row has no acceptance"
    if row.get("selected_source_in") is None or not row.get("selected_asset_id"):
        return False, "the prior acceptance is missing a source interval"
    local = (row.get("verification_evidence") or {}).get("local_path") or ""
    if not local or not Path(local).is_file():
        return False, "the accepted file is no longer on disk"
    return True, "prior acceptance is still valid"


def retain_verified(current: dict, prior: dict | None) -> dict:
    """Return the row to schedule. The current transcript text and speech bounds stay."""
    updated = dict(current)
    ok, why = reusable(prior)
    same_page_rejected = _rejected_same_page(current, prior)
    if (
        current.get("retrieval_status") == "verified"
        and current.get("selected_asset_id")
        and current.get("selected_source_in") is not None
    ):
        updated["retain"] = {"action": "kept_current", "reason": "this plan already has a verified span"}
        return updated
    if same_page_rejected:
        updated["retain"] = {"action": "not_reused", "reason": "this page was rejected by a later check"}
        return updated
    if not ok:
        updated["retain"] = {
            "action": "not_reused",
            "reason": why,
            "prior_status": None if not prior else prior.get("retrieval_status"),
        }
        return updated
    if abs(float(current.get("speech_start") or 0) - float(prior.get("speech_start") or 0)) > 0.05:
        updated["retain"] = {"action": "not_reused", "reason": "the speech bounds no longer match the accepted interval"}
        return updated
    if abs(float(current.get("speech_end") or 0) - float(prior.get("speech_end") or 0)) > 0.05:
        updated["retain"] = {"action": "not_reused", "reason": "the speech bounds no longer match the accepted interval"}
        return updated
    evidence = dict(prior.get("verification_evidence") or {})
    evidence["retained_from"] = "phase18"
    evidence["selector_schedule"] = current.get("schedule")
    updated["schedule"] = "broll"
    updated["retrieval_status"] = "verified"
    updated["selected_asset_id"] = prior.get("selected_asset_id") or ""
    updated["selected_source_in"] = prior.get("selected_source_in")
    updated["selected_source_out"] = prior.get("selected_source_out")
    updated["exact_transcript"] = current.get("exact_transcript") or prior.get("exact_transcript") or ""
    updated["verification_evidence"] = evidence
    updated["decision_reason"] = (
        "Retained the Phase 18 acceptance. The later selector did not schedule this phrase. "
        + str(prior.get("decision_reason") or "")
    ).strip()
    updated["retain"] = {
        "action": "restored",
        "page_url": updated["selected_asset_id"],
        "source_in": updated["selected_source_in"],
        "source_out": updated["selected_source_out"],
        "speech_start": current.get("speech_start"),
        "speech_end": current.get("speech_end"),
    }
    return updated


def _rejected_same_page(current: dict, prior: dict | None) -> bool:
    if not prior or current.get("retrieval_status") != "rejected":
        return False
    current_page = current.get("selected_asset_id") or (current.get("verification_evidence") or {}).get("page_url") or ""
    prior_page = prior.get("selected_asset_id") or ""
    return bool(current_page and current_page == prior_page)
