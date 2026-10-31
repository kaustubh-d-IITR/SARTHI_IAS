"""Assemble the Step-0 reference dataset from raw extracts and visual notes.

Re-runnable. Does not download media. Visual notes that are missing produce
partial records rather than invented timelines.
"""

from __future__ import annotations

import json
import shutil
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"c:\Users\shubh\OneDrive\Desktop\SAARTHI IAS")
DATASET = ROOT / "reference_dataset"
RAW = DATASET / "raw"
NOTES = DATASET / "visual_notes"
DIFFS = DATASET / "frame_diffs"
TRANS = DATASET / "translations.json"
REPORTS = DATASET / "reports"
ASSETS = DATASET / "assets" / "contact_sheets"
LOGS = DATASET / "analysis_logs"

CREATED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def caption_track(raw: dict) -> tuple[str, list[dict], str | None]:
    files = raw.get("caption_files") or {}
    for key in ("hi-orig", "en-orig", "hi", "en"):
        value = files.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("text"):
            lang = "hi" if key.startswith("hi") else "en"
            return key, value, lang
    errors = {k: v for k, v in files.items() if k.endswith("_error")}
    return "", [], json.dumps(errors, ensure_ascii=False) if errors else "no caption track recovered"


def join_caption(segments: list[dict]) -> str:
    parts = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def fmt_date(value: str | None) -> str | None:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def pct(part: float, whole: float) -> float | None:
    if not whole:
        return None
    return round(max(0.0, min(100.0, 100.0 * part / whole)), 1)


def audio_block(raw: dict, caption_text: str) -> dict:
    audio = raw.get("audio") or {}
    rms_rows = [row for row in (audio.get("rms_1s") or []) if row.get("rms_db") is not None]
    levels = [row["rms_db"] for row in rms_rows]
    silence = audio.get("silence_intervals") or []
    music_tag = "संगीत" in caption_text or "[music]" in caption_text.lower()
    presence = "insufficient"
    if music_tag:
        presence = "asr_contains_music_tag"
    elif silence and audio.get("mean_rms_during_detected_silence_db") is not None:
        bed = audio["mean_rms_during_detected_silence_db"]
        presence = "audible_bed_during_pauses" if bed > -45 else "pauses_near_silence"
    elif levels and min(levels) > -35:
        presence = "no_silence_detected_continuous_loudness"
    dips = [row for row in rms_rows if row["rms_db"] <= (statistics.median(levels) - 6 if levels else -99)]
    return {
        "listened_by_analyst": False,
        "measurement": "ffmpeg silencedetect -32dB/0.20s, volumedetect, and 1-second RMS on the mixed stereo track",
        "speech_continuity": "Automatic captions provide the speech timeline. The analyst did not listen to the audio.",
        "music_track_identity": None,
        "music_track_status": "unknown",
        "music_presence_evidence": presence,
        "asr_music_tag": music_tag,
        "silence_intervals": silence,
        "volume_db": audio.get("volume"),
        "mean_rms_during_detected_silence_db": audio.get("mean_rms_during_detected_silence_db"),
        "rms_median_db": round(statistics.median(levels), 2) if levels else None,
        "rms_min_db": round(min(levels), 2) if levels else None,
        "rms_max_db": round(max(levels), 2) if levels else None,
        "notable_rms_dips": dips[:12],
        "sound_effects": "not separately identified; the file is a single mixed track",
    }


def layout_group(layout: dict) -> str:
    """Analyst bucket for free-text template labels. The original label is kept."""
    text = f"{layout.get('template_family') or ''} {layout.get('canvas') or ''}".lower()
    if not text.strip():
        return "not_inspected"
    product = any(word in text for word in ("website", "desktop", "practice website", "screen recording of a practice"))
    if product:
        return "screen_recording_or_product_ui"
    split = (
        "split" in text
        or ("upper" in text and "lower" in text)
        or "stacked" in text
        or "upper_insert" in text
        or "upper insert" in text
    )
    if split:
        return "split_teacher_and_graphic"
    full = any(
        phrase in text
        for phrase in (
            "full-bleed",
            "full bleed",
            "full_bleed",
            "fullbleed",
            "full-frame",
            "full frame",
            "fullframe",
            "fills the frame",
            "green_board",
            "whiteboard",
            "chalkboard",
        )
    )
    if full:
        return "full_bleed_board_or_lecture"
    if "rounded" in text or "card" in text:
        return "bounded_card_with_title"
    return "other_or_mixed"


def visibility_from_notes(notes: dict | None, duration: float | None) -> dict:
    if not notes:
        return {
            "status": "unavailable",
            "method": "visual notes not yet written",
            "duration_seconds": duration,
        }
    count = notes.get("teacher_visibility_count") or {}
    sampled = count.get("sampled_frames") or 0
    primary = count.get("frames_teacher_primary") or 0
    overlay = count.get("frames_teacher_with_overlay") or 0
    hidden = count.get("frames_teacher_hidden") or 0
    visible_any = primary + overlay
    accounted = primary + overlay + hidden
    gap = abs(accounted - sampled) if sampled else 0
    return {
        "status": "estimated",
        "method": count.get("method") or "1fps sample from contact sheets",
        "duration_seconds": duration,
        "sampled_frames": sampled,
        "sample_interval_seconds": 1,
        "frames_teacher_primary": primary,
        "frames_teacher_with_overlay": overlay,
        "frames_teacher_hidden_by_broll": hidden,
        "teacher_visible_any_percent_of_samples": pct(visible_any, sampled),
        "teacher_primary_percent_of_samples": pct(primary, sampled),
        "broll_replaces_teacher_percent_of_samples": pct(hidden, sampled),
        "simultaneous_overlay_percent_of_samples": pct(overlay, sampled),
        "frames_accounted": accounted,
        "count_gap": gap,
        "counts_match_sample": bool(sampled) and gap == 0,
        "counting_note": "A frame can show the teacher and an overlay together. Those frames are counted as visible_with_overlay, not as replacement. Percentages are of sampled 1fps frames, not of decoded 30fps frames. If frames_accounted is below sampled_frames, the three buckets do not partition the sample and the percentages are incomplete.",
    }


def timeline_coverage(timeline: list[dict], duration: float | None) -> dict:
    if not timeline:
        return {"covers_full_duration": False, "gaps": [{"start": 0, "end": duration, "reason": "no visual timeline"}]}
    ordered = sorted(timeline, key=lambda row: row.get("start") or 0)
    gaps = []
    cursor = 0.0
    for row in ordered:
        start = float(row.get("start") or 0)
        end = float(row.get("end") or start)
        if start - cursor > 0.75:
            gaps.append({"start": round(cursor, 3), "end": round(start, 3)})
        cursor = max(cursor, end)
    if duration and duration - cursor > 0.75:
        gaps.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return {
        "covers_full_duration": not gaps,
        "timeline_start": ordered[0].get("start"),
        "timeline_end": ordered[-1].get("end"),
        "gaps": gaps,
    }


def copy_sheets(ref_id: str, raw: dict) -> list[str]:
    dest_dir = ASSETS / ref_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for sheet in (raw.get("frames") or {}).get("contact_sheets") or []:
        source = Path(sheet["path"])
        if not source.exists():
            continue
        target = dest_dir / source.name
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        copied.append(str(target.relative_to(DATASET)).replace("\\", "/"))
    return copied


def md_cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ")


def render_report(record: dict) -> str:
    meta = record["metadata"]
    cat = record["category"]
    tr = record["transcript"]
    vis = record["teacher_visibility"]
    quality = record["quality"]
    layout = record["visual_style"]
    lines = [
        f"# {record['reference_id']} — {meta.get('title') or 'Title unavailable'}",
        "",
        "## 1. Reference metadata",
        "",
        f"- Reference ID: {record['reference_id']}",
        f"- Original URL: {record['url']}",
        f"- YouTube video ID: {record['video_id']}",
        f"- Verified channel name: {meta.get('channel')}",
        f"- Actual title: {meta.get('title')}",
        f"- User-provided topic description: {meta.get('user_topic_description')}",
        f"- Publication date: {meta.get('publication_date')}",
        f"- Duration: {meta.get('duration_seconds')} seconds (YouTube metadata). Probed file duration: {meta.get('probed_duration_seconds')} seconds.",
        f"- Resolution: {meta.get('analyzed_width')}x{meta.get('analyzed_height')} in the analyzed file. YouTube reported {meta.get('source_resolution')}.",
        f"- Aspect ratio: {meta.get('aspect_ratio_label')}",
        f"- Frame rate: {meta.get('fps')}",
        f"- Analysis status: {quality.get('status')}",
        f"- Sources inspected: {', '.join(record.get('sources_inspected') or [])}",
        f"- Confidence and limitations: {quality.get('summary')}",
        "",
        "## 2. Category and semantic understanding",
        "",
        f"- Primary category: {cat.get('primary')}",
        f"- Secondary categories: {', '.join(cat.get('secondary') or []) or 'none recorded'}",
        f"- Main topic: {cat.get('main_topic')}",
        f"- Subtopics: {', '.join(cat.get('subtopics') or []) or 'see transcript'}",
        f"- Intended audience: {cat.get('intended_audience')}",
        f"- Purpose of the Short: {cat.get('purpose')}",
        f"- Core message: {cat.get('core_message')}",
        f"- Emotional tone: {cat.get('emotional_tone')}",
        f"- Narrative structure: {cat.get('narrative_structure')}",
        f"- Main hook: {cat.get('hook')}",
        f"- Key takeaway: {cat.get('takeaway')}",
        f"- Call to action: {cat.get('call_to_action')}",
        f"- Important entities: {cat.get('entities')}",
        f"- Why this classification was selected: {cat.get('rationale')}",
        f"- Confidence: {cat.get('confidence')}",
        "",
        "## 3. Complete transcript",
        "",
        "### Original-language transcript",
        "",
        f"Source: `{tr.get('caption_track')}` ({tr.get('caption_status')}). "
        "This is the YouTube automatic caption track, not a human transcript. "
        "Overlapping caption windows were concatenated in time order. "
        "Hindi is in Devanagari when the caption track used Devanagari. "
        "English words spoken inside Hindi sentences are preserved as the caption track wrote them.",
        "",
        tr.get("original_text") or "_No caption track recovered._",
        "",
        "Timestamped segments:",
        "",
    ]
    for seg in tr.get("segments") or []:
        lines.append(f"- {seg.get('start')}–{seg.get('end')} s: {seg.get('text')}")
    lines += [
        "",
        "### Complete English translation",
        "",
        tr.get("english_translation") or "_English translation not yet written. The original caption text above is the evidence. Nothing has been invented to fill this gap._",
        "",
        "### On-screen text",
        "",
        f"- Title on the video: {layout.get('title_text')}",
        f"- Logo as read from frames: {layout.get('logo_text_as_read')}",
        f"- Caption behavior: {json.dumps(record.get('captions') or {}, ensure_ascii=False)}",
        "",
        "Spoken captions and burned-in captions are kept separate. Burned-in caption wording in the timeline is a vision reading of frames and can contain OCR mistakes. Those cells are marked observed, not verified character-by-character.",
        "",
        "## 4. Global visual composition",
        "",
    ]
    if layout:
        for key, value in layout.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- Visual composition has not been inspected yet.")
    lines += [
        "",
        "## 5. Detailed chronological editing timeline",
        "",
        "Times are in seconds. One-frame-per-second contact sheets are the visual evidence. Scene-detection timestamps are supporting evidence and were not treated as a complete cut list.",
        "",
        "| Start time | End time | Spoken wording | Teacher visibility | Visual content | B-roll or asset | Transition/effect | Captions and emphasis | Audio/music | Evidence and confidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in record.get("timeline") or []:
        lines.append(
            "| "
            + " | ".join(
                md_cell(row.get(key))
                for key in (
                    "start",
                    "end",
                    "spoken_wording",
                    "teacher_visibility",
                    "visual_content",
                    "broll_or_asset",
                    "transition_or_effect",
                    "captions_and_emphasis",
                    "audio_note",
                    "evidence",
                )
            )
            + " |"
        )
    if not record.get("timeline"):
        lines.append("| | | | | No visual timeline recorded | | | | | unavailable |")
    coverage = record.get("timeline_coverage") or {}
    lines += [
        "",
        f"Coverage: {json.dumps(coverage, ensure_ascii=False)}",
        "",
        "## 6. B-roll and supporting footage analysis",
        "",
    ]
    broll = record.get("broll_segments") or []
    if not broll:
        lines.append("No supporting-footage segment was recorded. That can mean the teacher stayed on screen, or that the visual pass is incomplete.")
    for item in broll:
        lines += [
            f"### {item.get('id')}",
            "",
            f"- Start–end: {item.get('start')}–{item.get('end')} seconds",
            f"- Visible content: {item.get('visible')}",
            f"- Asset type: {item.get('asset_type')}",
            f"- Spoken phrase: {item.get('spoken_phrase')}",
            f"- Relation to speech: {item.get('relation_to_speech')}",
            f"- Source: {item.get('source')}",
            f"- Placement: {item.get('placement')}",
            f"- Enter/exit: {item.get('enter_exit')}",
            f"- Confidence: {item.get('confidence')}",
            "",
        ]
    lines += [
        "## 7. Teacher visibility and visual balance",
        "",
        "```json",
        json.dumps(vis, indent=2, ensure_ascii=False),
        "```",
        "",
        "## 8. Caption and typography analysis",
        "",
        "```json",
        json.dumps(record.get("captions") or {}, indent=2, ensure_ascii=False),
        "```",
        "",
        "Font name was not identified from the rendered frames. Observations describe color, placement, and chunk size only.",
        "",
        "## 9. Audio and music analysis",
        "",
        "The analyst did not listen to the soundtrack. The notes below come from the mixed-track measurements and from music tags inside automatic captions. No track title, artist, or music URL is claimed.",
        "",
        "```json",
        json.dumps(record.get("audio_music") or {}, indent=2, ensure_ascii=False),
        "```",
        "",
        "## 10. Visual effects and transitions",
        "",
    ]
    effects = record.get("effects_transitions") or []
    if not effects:
        lines.append("No separate effect list was recorded beyond the timeline.")
    for effect in effects:
        lines.append(f"- {effect.get('t')} s: {effect.get('type')} — {effect.get('evidence')}")
    lines += [
        "",
        "## 11. Editing style interpretation",
        "",
        record.get("style_interpretation") or "_Pending visual notes._",
        "",
        "## 12. Reusable lessons for the future AI editor",
        "",
    ]
    principles = record.get("editing_principles") or []
    if not principles:
        lines.append("No principle was promoted from this Short yet. A single uncertain observation is not turned into a rule.")
    for rule in principles:
        lines.append(
            f"- {rule.get('rule_id')}: {rule.get('description')} "
            f"Category: {rule.get('applicable_category')}. "
            f"Conditions: {rule.get('conditions')}. "
            f"Evidence: {rule.get('evidence')}. "
            f"Confidence: {rule.get('confidence')}. "
            f"Scope: {rule.get('scope')}."
        )
    lines += [
        "",
        "## 13. Additional observations",
        "",
    ]
    for item in record.get("additional_observations") or ["None recorded."]:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 14. Final evidence and quality summary",
        "",
        f"- Percentage of duration successfully inspected: {quality.get('duration_inspected_percent')}",
        f"- Transcript completeness: {quality.get('transcript_completeness')}",
        f"- Metadata completeness: {quality.get('metadata_completeness')}",
        f"- Visual timeline completeness: {quality.get('visual_timeline_completeness')}",
        f"- Audio analysis completeness: {quality.get('audio_analysis_completeness')}",
        f"- Teacher visibility estimate confidence: {quality.get('teacher_visibility_confidence')}",
        f"- B-roll identification confidence: {quality.get('broll_confidence')}",
        f"- Missing evidence: {', '.join(quality.get('limitations') or [])}",
        f"- Manual review needed: {quality.get('manual_review_required')}",
        f"- Overall analysis quality: {quality.get('status')}",
        "",
    ]
    return "\n".join(lines)


def normalize_effects(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        return [{"t": None, "type": "observed_note", "evidence": value}]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return [{"t": None, "type": "observed_note", "evidence": str(value)}]
    rows = []
    for item in value:
        if isinstance(item, str):
            rows.append({"t": None, "type": "observed_note", "evidence": item})
        elif isinstance(item, dict):
            rows.append(
                {
                    "t": item.get("t", item.get("time")),
                    "type": item.get("type") or item.get("name") or "observed",
                    "evidence": item.get("evidence") or item.get("description") or "",
                }
            )
    return rows


def principle_from_broll(ref_id: str, notes: dict, category: str) -> list[dict]:
    segments = notes.get("broll_segments") or []
    if not segments:
        return []
    evidence = ", ".join(
        f"{seg.get('id')} {seg.get('start')}-{seg.get('end')}s" for seg in segments[:4]
    )
    return [
        {
            "rule_id": f"{ref_id}_BROLL_OBSERVATION",
            "description": "In this Short, supporting imagery appears while the spoken sentence continues and burned-in captions remain. Treat this as a single-video observation until the same behavior is counted across the set.",
            "applicable_category": category,
            "conditions": "The speaker refers to a concrete person, place, object, number, or event that can be shown.",
            "evidence": evidence,
            "confidence": "single_reference_observation",
            "scope": "single_video",
        }
    ]


def style_paragraph(notes: dict | None) -> str:
    if not notes:
        return "Visual style has not been inspected from frames yet."
    layout = notes.get("layout") or {}
    count = notes.get("teacher_visibility_count") or {}
    return (
        f"Observed template family: {layout.get('template_family')}. "
        f"Title as read: {layout.get('title_text')}. "
        f"The 1fps sample counted {count.get('frames_teacher_primary', 0)} primary-teacher frames, "
        f"{count.get('frames_teacher_with_overlay', 0)} overlay frames, and "
        f"{count.get('frames_teacher_hidden', 0)} frames where B-roll hid the teacher, "
        f"out of {count.get('sampled_frames', 0)} sampled frames. "
        "These counts describe the sample. They are not a claim about the editor's intention. "
        "Direct observations are the timeline rows. Any reason a B-roll insert was chosen is a hypothesis tied to the spoken phrase in that row."
    )


def build_one(ref_id: str, manifest_row: dict, translations: dict) -> dict:
    raw = load_json(RAW / f"{ref_id}.raw.json") or {}
    notes = load_json(NOTES / f"{ref_id}.json")
    diffs = load_json(DIFFS / f"{ref_id}.json")
    meta_in = raw.get("metadata") or {}
    probe = raw.get("probe") or {}
    failed = raw.get("status") == "failed" or not meta_in
    track_name, segments, track_lang_or_error = caption_track(raw)
    original = join_caption(segments)
    translation_row = translations.get(ref_id) or {}
    duration = meta_in.get("duration") or probe.get("duration_seconds")
    width = probe.get("width") or meta_in.get("width")
    height = probe.get("height") or meta_in.get("height")
    aspect = None
    if width and height:
        aspect = "9:16" if height > width else "16:9" if width > height else "1:1"
    sheets = copy_sheets(ref_id, raw) if raw.get("frames") else []
    layout = (notes or {}).get("layout") or {}
    timeline = []
    for row in (notes or {}).get("timeline") or []:
        timeline.append(
            {
                "start": row.get("start"),
                "end": row.get("end"),
                "spoken_wording": row.get("spoken_wording"),
                "teacher_visibility": row.get("teacher_visibility"),
                "visual_content": row.get("visual_content"),
                "broll_or_asset": row.get("broll_or_asset"),
                "transition_or_effect": row.get("transition_or_effect"),
                "captions_and_emphasis": row.get("captions_and_emphasis"),
                "audio_note": "mixed-track measurement only; not a listened description of this interval",
                "evidence": row.get("evidence"),
                "confidence": row.get("confidence") or "observed",
            }
        )
    coverage = timeline_coverage(timeline, duration if isinstance(duration, (int, float)) else None)
    category = translation_row.get("category") or {
        "primary": "unclassified_pending_review",
        "secondary": [],
        "main_topic": meta_in.get("title"),
        "subtopics": [],
        "intended_audience": "UPSC aspirants, based on channel and title; confirm against the transcript",
        "purpose": manifest_row.get("user_topic_description"),
        "core_message": None,
        "emotional_tone": None,
        "narrative_structure": None,
        "hook": meta_in.get("title"),
        "takeaway": None,
        "call_to_action": None,
        "entities": None,
        "rationale": "Category fields beyond the title and the user topic line are not filled until the transcript translation pass.",
        "confidence": "low",
    }
    vis = visibility_from_notes(notes, duration if isinstance(duration, (int, float)) else None)
    audio = audio_block(raw, original)
    limitations = []
    if failed:
        limitations.append(raw.get("error") or "extract failed")
    if not original:
        limitations.append(track_lang_or_error or "transcript missing")
    if not translation_row.get("english"):
        limitations.append("english translation not yet written")
    if not notes:
        limitations.append("frame-by-frame visual notes not yet written")
    elif not coverage.get("covers_full_duration"):
        limitations.append("visual timeline has gaps")
    vis_gap = vis.get("count_gap") or 0
    vis_sampled = vis.get("sampled_frames") or 0
    if vis_sampled and vis_gap:
        limitations.append(
            f"teacher-visibility buckets account for {vis.get('frames_accounted')} of {vis_sampled} sampled frames"
        )
    if ref_id == "SHORT_01":
        limitations.append("analyzed file is 360x640; a format listing before the download fix showed 720x1280 was available")
    limitations.append("audio was not listened to; music identity is unknown")
    limitations.append("on-screen text comes from a vision reading of frames and may misread letters")
    if not meta_in.get("fps") and not probe.get("fps"):
        limitations.append("frame rate unavailable")

    transcript_status = "verified_automatic_caption" if original else "unavailable"
    visual_status = "observed_1fps_and_scene_frames" if notes and coverage.get("covers_full_duration") else (
        "partially_observed" if notes else "unavailable"
    )
    unreliable = translation_row.get("transcript_quality") == "unreliable_asr"
    if unreliable:
        limitations.append("automatic transcript is repetitive or incoherent and was not repaired")
    if failed and not meta_in:
        status = "failed"
    elif (
        notes
        and original
        and translation_row.get("english")
        and coverage.get("covers_full_duration")
        and not unreliable
        and not (vis_sampled and vis_gap / vis_sampled > 0.15)
    ):
        # Metadata, caption track, English rendering, and a full visual timeline.
        # Music identity remains unknown, which is recorded rather than invented.
        status = "completed"
    elif notes or original:
        status = "partial"
    else:
        status = "failed" if failed else "partial"
    if unreliable:
        transcript_status = "unreliable_automatic_caption"

    record = {
        "reference_id": ref_id,
        "url": manifest_row.get("url") or raw.get("source_url"),
        "video_id": meta_in.get("id") or manifest_row.get("video_id"),
        "metadata": {
            "channel": meta_in.get("channel"),
            "channel_id": meta_in.get("channel_id"),
            "title": meta_in.get("title"),
            "user_topic_description": manifest_row.get("user_topic_description") or raw.get("user_topic_description"),
            "publication_date": fmt_date(meta_in.get("upload_date")),
            "duration_seconds": duration,
            "probed_duration_seconds": probe.get("duration_seconds"),
            "source_resolution": meta_in.get("resolution"),
            "analyzed_width": width,
            "analyzed_height": height,
            "aspect_ratio_label": aspect,
            "fps": probe.get("fps") or meta_in.get("fps"),
            "video_codec": probe.get("video_codec") or meta_in.get("vcodec"),
            "audio_codec": probe.get("audio_codec") or meta_in.get("acodec"),
            "language_metadata": meta_in.get("language"),
            "availability": meta_in.get("availability"),
            "view_count": meta_in.get("view_count"),
            "like_count": meta_in.get("like_count"),
            "format_id": meta_in.get("format_id"),
            "categories_youtube": meta_in.get("categories"),
            "description": meta_in.get("description"),
            "scene_change_times_seconds": raw.get("scene_change_times_seconds"),
            "scene_threshold": 0.28,
            "one_fps_cut_seconds": (diffs or {}).get("one_fps_cut_seconds"),
        },
        "category": category,
        "transcript": {
            "original_language": track_lang_or_error if isinstance(track_lang_or_error, str) and track_name else None,
            "caption_track": track_name or None,
            "caption_status": transcript_status,
            "original_text": original or None,
            "english_translation": translation_row.get("english"),
            "english_translation_status": "analyst_translation" if translation_row.get("english") else "unavailable",
            "segments": segments,
        },
        "visual_style": layout,
        "layout_group": layout_group(layout),
        "timeline": timeline,
        "timeline_coverage": coverage,
        "broll_segments": (notes or {}).get("broll_segments") or [],
        "teacher_visibility": vis,
        "captions": (notes or {}).get("caption_observations") or {},
        "audio_music": audio,
        "effects_transitions": normalize_effects((notes or {}).get("effects")),
        "editing_principles": principle_from_broll(ref_id, notes or {}, category.get("primary")),
        "style_interpretation": style_paragraph(notes),
        "additional_observations": (notes or {}).get("uncertainties") or [],
        "evidence": [
            {"type": "youtube_metadata", "status": "verified" if meta_in else "unavailable"},
            {"type": "automatic_captions", "track": track_name or None, "status": transcript_status},
            {"type": "contact_sheets", "paths": sheets, "status": "copied" if sheets else "unavailable"},
            {"type": "ffmpeg_scene_detect", "threshold": 0.28, "times": raw.get("scene_change_times_seconds")},
            {"type": "one_fps_frame_diff", "times": (diffs or {}).get("one_fps_cut_seconds"), "status": "estimated"},
        ],
        "sources_inspected": [
            item
            for item, ok in (
                ("YouTube metadata via yt-dlp", bool(meta_in)),
                ("YouTube automatic captions", bool(original)),
                ("Downloaded progressive analysis file", bool(raw.get("media_path"))),
                ("1fps contact sheets", bool(sheets)),
                ("Visual notes from frame inspection", bool(notes)),
                ("ffmpeg scene detection and audio meters", bool(raw.get("audio"))),
            )
            if ok
        ],
        "quality": {
            "status": status,
            "summary": "Metadata and captions come from YouTube. Visual rows come from 1fps frames. Audio identity was not heard.",
            "duration_inspected_percent": 100 if notes and coverage.get("covers_full_duration") else (None if not notes else "incomplete"),
            "transcript_completeness": transcript_status,
            "metadata_completeness": "verified" if meta_in.get("title") and duration else "partial",
            "visual_timeline_completeness": visual_status,
            "audio_analysis_completeness": "measured_not_listened" if raw.get("audio") else "unavailable",
            "teacher_visibility_confidence": (
                "unavailable" if not notes else "estimated_incomplete_count" if vis_gap else "estimated"
            ),
            "broll_confidence": "observed_source_unknown" if (notes or {}).get("broll_segments") else "not_applicable" if notes else "unavailable",
            "limitations": limitations,
            "manual_review_required": True,
        },
    }
    return record


def master_report(dataset: dict) -> str:
    shorts = dataset["shorts"]
    counts = Counter(item["quality"]["status"] for item in shorts)
    categories = Counter((item["category"].get("primary") or "unclassified") for item in shorts)
    templates = Counter(item.get("layout_group") or "not_inspected" for item in shorts)
    channels = Counter((item["metadata"].get("channel") or "unknown") for item in shorts)
    lines = [
        "# Master analysis report — Sarthi IAS reference Shorts",
        "",
        f"Generated: {dataset['created_at']}",
        "",
        "## 1. Executive summary",
        "",
        "This is Step 0 only. No editor, website, or rendering pipeline was built.",
        "Each reference was fetched as public YouTube metadata plus the original-language automatic caption track, then downloaded at up to 720x1280 for frame and audio-meter analysis.",
        "Visual timelines come from 1-frame-per-second contact sheets. They are observations of those frames, not a claim that every 30fps cut was measured.",
        "The soundtrack was not listened to. Music titles are not identified.",
        "",
        "## 2. Counts",
        "",
        f"- References in links.txt: {dataset['total_references']}",
        f"- Records in the dataset: {len(shorts)}",
        f"- Completed (metadata, caption track, and a visual timeline that covers the duration): {counts.get('completed', 0)}",
        f"- Partial: {counts.get('partial', 0)}",
        f"- Failed: {counts.get('failed', 0)}",
        f"- Blocked: {counts.get('blocked', 0)}",
        "- Manual review is required on every record because on-screen text was read from frames and captions are automatic.",
        "",
        "## 3. Channels",
        "",
    ]
    for name, count in channels.most_common():
        lines.append(f"- {name}: {count}")
    lines += ["", "## 4. Category distribution", ""]
    for name, count in categories.most_common():
        lines.append(f"- {name}: {count}")
    lines += [
        "",
        "Categories are content labels. A count of 1 is an isolated observation. Do not promote it to a house style.",
        "",
        "## 5. Visual template families",
        "",
        "The frame notes used many different names for similar layouts. The counts below are a second-pass grouping. Each report still keeps the original wording in `visual_style.template_family`.",
        "",
    ]
    for name, count in templates.most_common():
        lines.append(f"- {name}: {count}")
    lines += [
        "",
        "Template names were assigned during frame inspection. They are descriptive labels, not the channel's internal names.",
        "",
        "## 6. Patterns supported by more than one Short",
        "",
        "These statements are only as strong as the visual notes that exist when this report is generated. Re-read the per-Short timelines before treating a pattern as a rule.",
        "",
        "- The channel on the verified metadata is Clarity for UPSC by Dr.Shivin on all 50 records.",
        "- Spoken wording in the timelines continues across B-roll intervals, and each file is one continuous mixed track. The audio was not listened to, so a mute was not confirmed by ear. The caption timing does not show the speech stopping in order to make room for B-roll.",
        "- Burned-in captions are short chunks, updated about once a second, and they remain visible over both the teacher and supporting footage in the Shorts that were frame-inspected.",
        "- Supporting footage is used when the sentence names something that can be shown: a sibling, a map, a number, a website, a historical photograph. It is not a constant background.",
        "- More than one layout exists. Section 5 groups them. A bounded card with a title, a full-bleed board, a split teacher-and-graphic frame, and a product screen recording were all observed. One template must not be hardcoded for every category.",
        "",
        "## 7. Isolated observations",
        "",
        "- SHORT_01 uses a light classroom card, a maroon caption pill, and stock or illustrative footage that fully replaces the teacher for a few seconds at a time.",
        "- SHORT_02 uses a green board, archival photographs, large numerals, and a map. Several of those changes did not cross the 0.28 scene-detect threshold and were seen on the 1fps frames instead.",
        "- SHORT_05 places graphic objects (a coin, question marks, a warning symbol) over or instead of the teacher while defining a term.",
        "- SHORT_08 cuts to a screen recording of a website while the teacher is selling a practice tool.",
        "",
        "## 8. Caption, visibility, audio, and effects",
        "",
        "Caption language in the recovered tracks is Hindi in Devanagari with English words written phonetically or in Latin, matching code-mixed speech. On-screen captions in the inspected frames are short Roman or English chunks, not a full English translation of the Hindi.",
        "Teacher-visibility percentages in each report are estimates from 1fps samples. Overlapping teacher-plus-overlay frames are not counted as replacement.",
        "Audio: no silence at -32 dB often means a bed or continuous speech. That measurement cannot name the music. Where the caption track contains the tag संगीत, that is recorded as an ASR music tag.",
        "Transitions observed in frames are mostly hard cuts and graphic overlays. Exact easing curves were not measured.",
        "",
        "## 9. Contradictions and exceptions",
        "",
        "- Scene detection at threshold 0.28 missed obvious full-frame swaps in SHORT_02. Cut lists in the dataset therefore combine scene detection with 1fps frame differences, and the timeline follows the frames.",
        "- Automatic captions can garble a word that the burned-in caption renders more clearly. SHORT_01's soundtrack caption ends in a phrase the frames read as 'it is important that you improve.' The report keeps both and does not silently pick one as the official transcript.",
        "- SHORT_01's analyzed file is 360x640 because the first format filter treated vertical 1280-height video as too tall. Later files were selected at 720x1280.",
        "",
        "## 10. Reusable editing principles",
        "",
        "Promoted only where the mechanism was seen, and marked single-video until a later count confirms it:",
        "",
        "- When the line names a concrete visual subject, replace or overlay the teacher with a relevant image for the length of that phrase, then return to the teacher for opinion or explanation.",
        "- Keep the original narration and the burned-in caption during that replacement.",
        "- Put the persistent title outside or above the changing picture so the topic survives B-roll.",
        "- Do not use the same picture layout for a motivational card, a board lecture, and a product demo.",
        "",
        "## 11. Data quality",
        "",
        "Verified: public title, channel, upload date, duration, and the caption track text when the download succeeded.",
        "Estimated: teacher-visibility percentages, B-roll in and out times at 1-second resolution, on-screen spelling.",
        "Unavailable: music title, exact font file, rights holder of B-roll, sub-second caption animation curves.",
        "",
        "## 12. Recommended next steps",
        "",
        "- Human pass on the automatic captions and the English translations before any model is trained on them.",
        "- Human pass on B-roll rights. Sources were not identified and must not be scraped from a guessed website.",
        "- Decide caption policy: Roman Hindi as now burned in, Devanagari, or English.",
        "- Only after that, design the editor. This dataset does not implement it.",
        "",
        "## 13. Per-reference status",
        "",
        "| ID | Status | Title | Duration | Template | Transcript |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in shorts:
        lines.append(
            "| {id} | {status} | {title} | {dur} | {tmpl} | {tr} |".format(
                id=item["reference_id"],
                status=item["quality"]["status"],
                title=md_cell(item["metadata"].get("title")),
                dur=item["metadata"].get("duration_seconds"),
                tmpl=md_cell(item.get("layout_group")),
                tr=item["transcript"].get("caption_status"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    manifest = load_json(DATASET / "manifest.json") or {"references": []}
    translations = load_json(TRANS) or {}
    rows = {row["reference_id"]: row for row in manifest.get("references") or []}
    REPORTS.mkdir(parents=True, exist_ok=True)
    shorts = []
    for index in range(1, 51):
        ref_id = f"SHORT_{index:02d}"
        record = build_one(ref_id, rows.get(ref_id) or {"reference_id": ref_id}, translations)
        shorts.append(record)
        (REPORTS / f"{ref_id}.md").write_text(render_report(record), encoding="utf-8")
        log_path = LOGS / f"{ref_id}.log"
        extra = (
            f"\ndataset_status: {record['quality']['status']}\n"
            f"transcript: {record['transcript'].get('caption_status')}\n"
            f"visual: {record['quality'].get('visual_timeline_completeness')}\n"
        )
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else f"reference_id: {ref_id}\n"
        if "dataset_status:" not in existing:
            log_path.write_text(existing.rstrip() + extra, encoding="utf-8")
    counts = Counter(item["quality"]["status"] for item in shorts)
    dataset = {
        "dataset_name": "Sarthi IAS Reference Shorts Editing Dataset",
        "dataset_version": "0.1.0",
        "created_at": CREATED_AT,
        "source_manifest": "links.txt",
        "total_references": 50,
        "analysis_summary": {
            "completed": counts.get("completed", 0),
            "partial": counts.get("partial", 0),
            "failed": counts.get("failed", 0),
            "blocked": counts.get("blocked", 0),
        },
        "method": {
            "metadata": "yt-dlp 2026.10.21 with the Node.js runtime",
            "captions": "one original-language json3 automatic caption track",
            "media": "H.264 up to 720x1280 plus AAC, stored outside the dataset in the local cache",
            "frames": "1 fps contact sheets plus ffmpeg scene detection at threshold 0.28",
            "audio": "mixed-track meters only; not listened to",
            "visual_timeline": "human/model inspection of contact sheets recorded in visual_notes",
        },
        "shorts": shorts,
    }
    (DATASET / "shorts_dataset.json").write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (DATASET / "MASTER_ANALYSIS_REPORT.md").write_text(master_report(dataset), encoding="utf-8")
    print(json.dumps(dataset["analysis_summary"], indent=2))


if __name__ == "__main__":
    main()
