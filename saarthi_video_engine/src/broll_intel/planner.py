"""One editorial decision per spoken sentence, merged when the visual idea continues."""

from __future__ import annotations

from broll_intel.queries import build_queries
from broll_intel.schema import EditorialDecision
from broll_intel.taxonomy import classify_sentence


def plan_lesson(transcript: dict, lesson_id: str) -> list[EditorialDecision]:
    language = str(transcript.get("language") or "und")
    decisions: list[EditorialDecision] = []
    for index, segment in enumerate(transcript.get("segments") or []):
        text = str(segment.get("text") or segment.get("original_text") or "")
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        classified = classify_sentence(text, start, end, language)
        queries = build_queries(classified)
        schedule = "teacher" if classified["visual_type"] == "none" else "broll"
        reason = classified["schedule_reason"] or classified["semantic_intent"]
        if schedule == "teacher":
            reason = reason or "Left on the teacher. Stock footage would impersonate someone or hide the point."
        decisions.append(
            EditorialDecision(
                lesson_id=lesson_id,
                transcript_segment_id=str(segment.get("id") or f"s{index}"),
                speech_start=round(start, 3),
                speech_end=round(end, 3),
                exact_transcript=text,
                language=language,
                primary_subject=classified["primary_subject"],
                entities=classified["entities"],
                action=classified["action"],
                semantic_intent=classified["semantic_intent"],
                visual_type=classified["visual_type"],
                visual_brief=classified["visual_brief"],
                required_objects=classified["required_objects"],
                required_actions=classified["required_actions"],
                forbidden_visuals=classified["forbidden_visuals"],
                negation=classified["negation"],
                negation_kind=classified["negation_kind"],
                contrast_type=classified["contrast_type"],
                overlay_type=classified["overlay_type"],
                search_queries=queries,
                retrieval_status="not_searched" if schedule == "broll" else "teacher",
                generation_required=False,
                review_required=False,
                decision_reason=reason,
                schedule=schedule,
            )
        )
    return _merge(decisions)


def _merge(rows: list[EditorialDecision]) -> list[EditorialDecision]:
    if not rows:
        return rows
    merged = [rows[0]]
    for row in rows[1:]:
        previous = merged[-1]
        same = (
            previous.schedule == "broll"
            and row.schedule == "broll"
            and previous.primary_subject
            and previous.primary_subject == row.primary_subject
            and previous.action == row.action
            and previous.overlay_type == row.overlay_type
            and row.speech_start - previous.speech_end <= 0.6
        )
        if not same:
            merged.append(row)
            continue
        merged[-1] = previous.model_copy(
            update={
                "speech_end": row.speech_end,
                "exact_transcript": f"{previous.exact_transcript} {row.exact_transcript}".strip(),
                "transcript_segment_id": f"{previous.transcript_segment_id}+{row.transcript_segment_id}",
            }
        )
    return merged
