"""Build a plan from a real transcript and optional licensed visuals."""

from __future__ import annotations

from dataset_adapter.retrieve import retrieve
from dataset_adapter.style_profile import style_profile
from transcription.captions import caption_pieces, prepare_segment

from .segment import shift_segments

BROLL_CAP = 0.58


def build_phase2_plan(
    dataset: dict,
    *,
    transcript: dict,
    selection: dict,
    media: dict,
    source: dict,
    assets: list[dict],
    exemplar_id: str = "SHORT_03",
    plan_id: str = "phase2-lewin-atwood",
    primary_category: str = "physics_lecture",
    secondary_categories: list[str] | None = None,
    music: dict | None = None,
    grade: dict | None = None,
) -> dict:
    profile = style_profile(dataset, exemplar_id)
    local_segments = shift_segments(
        transcript["segments"], selection["source_in"], selection["source_out"]
    )
    if not local_segments:
        raise ValueError("the selected interval contains no transcript segments")
    duration = round(selection["source_out"] - selection["source_in"], 3)
    language = transcript.get("language") or "und"
    language_source = transcript.get("language_source") or "inferred"
    stored = []
    pieces_by_id: dict[str, list[dict]] = {}
    previous = None
    run = 0
    for segment in local_segments:
        prepared = prepare_segment(segment, language)
        flags = list(prepared["flags"])
        shown = prepared["caption_text"].strip()
        if shown and shown == previous:
            run += 1
        elif shown:
            previous = shown
            run = 1
        else:
            run = 0
        if shown and run > 2:
            flags.append("repeated_recognition")
            shown = ""
            prepared = dict(prepared)
            prepared["caption_text"] = ""
        stored.append(
            {
                "id": segment["id"],
                "text": shown or prepared["original_text"],
                "original_text": prepared["original_text"],
                "romanized_text": prepared["romanized_text"],
                "english_text": prepared["english_text"],
                "caption_flags": flags,
                "start": segment["start"],
                "end": segment["end"],
                "language": language,
                "language_source": language_source,
                "confidence": segment.get("confidence"),
                "timing_precision": segment.get("timing_precision") or "segment",
            }
        )
        pieces_by_id[segment["id"]] = caption_pieces(segment, prepared) if shown else []
    refs = [exemplar_id]
    neighbors = [
        hit["reference_id"]
        for hit in retrieve(
            dataset,
            layout_group=profile["layout_family"],
            query=selection["text"][:240],
            trusted_only=True,
            limit=3,
        )
        if hit["reference_id"] != exemplar_id
    ]
    visual = _visual_decisions(duration, stored, assets, refs)
    title_text = _title(_title_source(stored, selection["text"], language))
    decisions = visual + [
        _decision(
            "title",
            "title",
            0.0,
            duration,
            "The title repeats caption words. It is omitted when the recognition is an English rendering of non-English speech.",
            refs,
            source="observed" if title_text else "default",
            confidence=0.7 if title_text else 0.3,
            text=title_text,
        )
    ]
    for segment in stored:
        for index, piece in enumerate(pieces_by_id.get(segment["id"]) or []):
            piece_id = f"cap-{segment['id']}" if len(pieces_by_id[segment["id"]]) == 1 else f"cap-{segment['id']}-{index}"
            decisions.append(
                _decision(
                    piece_id,
                    "caption",
                    piece["start"],
                    piece["end"],
                    _caption_reason(language),
                    refs,
                    source="observed",
                    confidence=segment["confidence"] if isinstance(segment["confidence"], float) else 0.5,
                    text=piece["text"],
                )
            )
    entities = _entities(_speech_blob(stored))
    broll_share = picture_fraction(
        {"input_media": {"duration_seconds": duration}, "decisions": visual},
        "replace_with_broll",
    )
    limitations = [
        "Teacher-visibility percentages in the style profile are 1fps estimates from other videos.",
        "No font file was identified. Inter is a HyperFrames default.",
        "ASR was not hand-corrected.",
        "Visible captions use the source-language words. Devanagari is transliterated. An English translation is not shown.",
        "No LLM provider is called. Topic, grade, and asset matching are keyword rules.",
        "Reference-record scores are token overlap. They are not a measure of topical or visual similarity.",
        "SHORT_30, SHORT_32, and SHORT_37 were not used to seed the style profile.",
        f"Other completed records in this layout family consulted as neighbors: {', '.join(neighbors) or 'none'}.",
        f"B-roll covers {broll_share:.1%} of the timeline. 30-40% is a target, not a quota.",
    ]
    if music is None:
        limitations.append("No music bed is used. A licensed instrumental was not attached to this plan.")
    render = {
        "width": 720,
        "height": 1280,
        "fps": 30,
        "object_fit": "contain",
    }
    if grade:
        render["grade"] = {
            "name": grade.get("name") or "none",
            "confidence": grade.get("confidence") if isinstance(grade.get("confidence"), (int, float)) else 0.0,
            "reason": grade.get("reason") or "",
            "ffmpeg_video_filter": grade.get("ffmpeg_video_filter"),
        }
        if isinstance(grade.get("timeline"), list):
            render["grade"]["timeline"] = grade["timeline"]
    return {
        "contract_version": "1.0.0",
        "plan_id": plan_id,
        "form": "short",
        "input_media": {
            "video_path": media["teacher"],
            "duration_seconds": duration,
            "width": source["width"],
            "height": source["height"],
            "fps": source["fps"],
            "audio_path": media["narration"],
            "media_origin": "authorized_local",
            "transcript": {
                "language": language,
                "translation_language": None,
                "method": transcript.get("method") or "unknown",
                "model": transcript.get("model"),
                "provenance": transcript.get("provenance") or "",
                "segments": stored,
            },
        },
        "content_analysis": {
            "primary_category": primary_category,
            "secondary_categories": secondary_categories if secondary_categories is not None else ["mechanics"],
            "topic": (title_text or "lesson").lower(),
            "subtopics": entities[:4],
            "main_message": selection["text"][:400],
            "narrative_structure": "one continuous spoken explanation; the picture leaves the teacher only for a verified visual",
            "emotional_tone": "instructional",
            "entities": entities,
            "confidence": 0.6,
            "evidence": "Source-language ASR of the selected interval, plus keyword rules for grade and B-roll. Tiny-model wording can be wrong.",
            "source": "inferred",
        },
        "style": {
            "exemplar_reference_id": exemplar_id,
            "layout_family": profile["layout_family"],
            "template_family": profile["template_family"] or "unspecified",
            "trusted": True,
        },
        "decisions": decisions,
        "audio": {
            "preserve_original": True,
            "narration_path": media["narration"],
            "narration_interval": {
                "start": 0.0,
                "end": duration,
                "source_in": selection["source_in"],
                "source_out": selection["source_out"],
            },
            "music": music if music is not None else _silent_music(),
        },
        "render": render,
        "attribution": {
            "text": source["attribution_line"],
            "license": source["license"],
            "license_url": source["license_url"],
            "changes": source.get("changes") or "",
        },
        "limitations": limitations,
    }


def picture_fraction(plan: dict, action: str) -> float:
    duration = plan["input_media"]["duration_seconds"]
    if duration <= 0:
        return 0.0
    used = sum(item["end"] - item["start"] for item in plan["decisions"] if item["action"] == action)
    return used / duration


def clip_plan_to_duration(plan: dict, duration: float) -> dict:
    """Trim a plan to a narration file without parsing caption ids."""
    plan["input_media"]["duration_seconds"] = duration
    plan["audio"]["narration_interval"]["end"] = duration
    kept_segments = []
    for segment in plan["input_media"]["transcript"]["segments"]:
        if segment["start"] >= duration - 0.05:
            continue
        segment["end"] = round(min(segment["end"], duration), 3)
        if segment["end"] - segment["start"] >= 0.2:
            kept_segments.append(segment)
    plan["input_media"]["transcript"]["segments"] = kept_segments
    decisions = []
    for decision in plan["decisions"]:
        if decision["start"] >= duration:
            continue
        decision["end"] = round(min(decision["end"], duration), 3)
        if decision["end"] > decision["start"]:
            decisions.append(decision)
    plan["decisions"] = decisions
    return plan


def _silent_music() -> dict:
    return {
        "path": None,
        "identity": None,
        "identity_status": "unknown",
        "source": "default",
        "gain_under_speech": 0.0,
        "gain_open": 0.0,
    }


def _caption_reason(language: str) -> str:
    if str(language).lower() in {"en", "english"}:
        return "Caption text is the English transcript for this ASR segment. Timing is the provider timestamp, not an even split."
    return (
        "Caption text is the source-language transcript in Latin letters. "
        "Devanagari was transliterated. This line is not an English translation."
    )


def _visual_decisions(duration: float, segments: list[dict], assets: list[dict], refs: list[str]) -> list[dict]:
    replacements = []
    for asset in assets:
        window = _window_for_asset(segments, asset)
        if window is None:
            continue
        replacements.append((window[0], window[1], asset))
    replacements.sort()
    merged = _cap_windows(_merge_windows(replacements), duration)
    decisions = []
    cursor = 0.0
    for index, (start, end, asset) in enumerate(merged):
        if start - cursor >= 0.35:
            decisions.append(
                _decision(
                    f"teacher-{index}",
                    "show_teacher",
                    round(cursor, 3),
                    round(start, 3),
                    "The teacher stays visible while the speech is not naming a visual we actually retrieved.",
                    refs,
                    source="inferred",
                    confidence=0.6,
                    asset_id="teacher",
                )
            )
        decisions.append(
            _broll_decision(index, start, end, asset, refs)
        )
        cursor = end
    if duration - cursor >= 0.35:
        decisions.append(
            _decision(
                "teacher-end",
                "show_teacher",
                round(cursor, 3),
                duration,
                "The teacher returns when the matched visual's speech interval ends. Narration is a separate continuous clip.",
                refs,
                source="inferred",
                confidence=0.6,
                asset_id="teacher",
            )
        )
    if not decisions:
        decisions.append(
            _decision(
                "teacher-all",
                "show_teacher",
                0.0,
                duration,
                "No licensed visual matched the speech, so the teacher picture stays up for the whole interval.",
                refs,
                source="default",
                confidence=0.5,
                asset_id="teacher",
            )
        )
    return decisions


def _broll_decision(index: int, start: float, end: float, asset: dict, refs: list[str]) -> dict:
    kind = asset.get("kind")
    if kind not in {"image", "video"}:
        path = str(asset.get("path") or "")
        kind = "image" if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) else "video"
    return _decision(
        f"broll-{index}",
        "replace_with_broll",
        round(start, 3),
        round(end, 3),
        asset.get("reason") or "The visual was retrieved for words in this interval.",
        refs,
        source="observed",
        confidence=float(asset.get("confidence") or 0.55),
        asset_id=asset["id"],
        asset_path=asset["path"],
        asset_kind=kind,
        transition=asset.get("transition") or "hard_cut",
        license_name=asset.get("license"),
        license_url=asset.get("license_url"),
        page_url=asset.get("page_url"),
        creator=asset.get("artist") or asset.get("creator"),
        graphic=asset.get("graphic") or "",
    )


def _window_for_asset(segments: list[dict], asset: dict) -> tuple[float, float] | None:
    preset = asset.get("window")
    if isinstance(preset, (list, tuple)) and len(preset) == 2:
        start = float(preset[0])
        end = float(preset[1])
    else:
        terms = [term.lower() for term in asset.get("match_terms") or [] if len(term) >= 4]
        hits = []
        for segment in segments:
            haystack = " ".join(
                [
                    segment.get("original_text") or "",
                    segment.get("romanized_text") or "",
                    segment.get("text") or "",
                ]
            ).lower().replace("-", " ")
            if any(term in haystack for term in terms):
                hits.append(segment)
        if not hits:
            return None
        start = hits[0]["start"]
        end = hits[0]["end"]
        for segment in hits[1:]:
            if segment["start"] - end <= 8:
                end = segment["end"]
            else:
                break
    span = asset.get("duration_seconds")
    if isinstance(span, (int, float)) and span > 0:
        end = min(end, start + float(span))
    if end - start < 2.0:
        return None
    return start, end


def _merge_windows(windows: list[tuple[float, float, dict]]) -> list[tuple[float, float, dict]]:
    merged = []
    for start, end, asset in windows:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end, asset))
    return merged


def _cap_windows(windows: list[tuple[float, float, dict]], duration: float) -> list[tuple[float, float, dict]]:
    budget = duration * BROLL_CAP
    kept = []
    used = 0.0
    for start, end, asset in windows:
        room = budget - used
        if room < 2.0:
            break
        if end - start > room:
            end = start + room
        if end - start < 2.0:
            continue
        kept.append((round(start, 3), round(end, 3), asset))
        used += end - start
    return kept


def _speech_blob(segments: list[dict]) -> str:
    return " ".join(segment.get("romanized_text") or segment.get("original_text") or "" for segment in segments)


def _title_source(segments: list[dict], fallback: str, language: str) -> str:
    """Longest caption line that is not an ASR repetition loop."""
    counts: dict[str, int] = {}
    for segment in segments:
        if "english_tokens_for_non_english_speech" in (segment.get("caption_flags") or []):
            continue
        if "repeated_recognition" in (segment.get("caption_flags") or []):
            continue
        text = (segment.get("romanized_text") or "").strip()
        if text:
            counts[text] = counts.get(text, 0) + 1
    best = ""
    for text, count in counts.items():
        if count > 2:
            continue
        if len(text) > len(best):
            best = text
    if best:
        return best
    if str(language).lower() in {"en", "english"}:
        return fallback
    return ""


def _title(text: str) -> str:
    if not text.strip():
        return ""
    normalized = text.lower().replace("-", " ")
    if "force" in normalized and "pulley" in normalized:
        return "FORCES ON THE PULLEY"
    if "atwood" in normalized and "machine" in normalized:
        return "ATWOOD'S MACHINE"
    clause = text.split(".")[0]
    words = [word.strip(",;:") for word in clause.split() if word.strip(",;:")]
    while words and words[0].lower() in {"so", "and", "but", "then"}:
        words = words[1:]
    return " ".join(words[:5]).upper()[:42]


def _entities(text: str) -> list[str]:
    normalized = text.lower().replace("-", " ")
    found = []
    for name in (
        "atwood machine",
        "pulley",
        "gravitational acceleration",
        "rope",
        "hospital",
        "covid",
        "ppe",
        "mask",
        "ncert",
        "neet",
    ):
        if name in normalized or name.replace(" ", "") in normalized.replace(" ", ""):
            found.append(name)
    return found


def _decision(
    decision_id,
    action,
    start,
    end,
    reason,
    refs,
    source,
    confidence,
    text=None,
    asset_id=None,
    asset_path=None,
    asset_kind=None,
    transition=None,
    license_name=None,
    license_url=None,
    page_url=None,
    creator=None,
    graphic=None,
):
    if end <= start:
        end = round(start + 0.2, 3)
    item = {
        "id": decision_id,
        "action": action,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "reason": reason,
        "reference_ids": list(refs),
        "reference_timestamps": [],
        "confidence": confidence if confidence is not None else 0.5,
        "source": source,
    }
    if text is not None:
        item["text"] = text
    if asset_id is not None:
        item["asset_id"] = asset_id
    if asset_path is not None:
        item["asset_path"] = asset_path
    if asset_kind is not None:
        item["asset_kind"] = asset_kind
    if transition is not None:
        item["transition"] = transition
    if license_name:
        item["license"] = license_name
    if license_url:
        item["license_url"] = license_url
    if page_url:
        item["page_url"] = page_url
    if creator:
        item["creator"] = creator
    if graphic:
        item["graphic"] = graphic
    return item
