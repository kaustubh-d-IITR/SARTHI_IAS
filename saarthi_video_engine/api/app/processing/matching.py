"""Mode B matcher: speech windows against user clips, using frames when they were analysed.

Scores are read relative to the rest of the same lesson, because raw CLIP and
text-embedding similarities sit in narrow bands. Thresholds come from the
real-clip calibration in PHASE4_ARCHITECTURE_AUDIT.md. A clip that does not
clear them is not used, and the reason is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.processing.user_broll import content_tokens

MIN_CUTAWAY = 2.0
MAX_PAIR_SPAN = 9.0
MIN_WINDOWS_FOR_MEDIAN = 5
CLIP_BASELINE = 0.20
EMBED_BASELINE = 0.75
VIS_SCALE = 0.08
SEM_SCALE = 0.12
ACCEPT = 0.60
MIN_VISUAL = 0.45
MIN_DESCRIPTION = 0.20
CONTRADICTION = 0.0
DESC_ONLY_MIN = 0.85
BROLL_CAP = 0.58

CONFIG = {
    "matcher": "phase4-evidence-v1",
    "pair_windows": "kept only when they score higher than each of their single sentences",
    "visual_scale": VIS_SCALE,
    "semantic_scale": SEM_SCALE,
    "accept": ACCEPT,
    "min_visual": MIN_VISUAL,
    "min_description": MIN_DESCRIPTION,
    "contradiction_below": CONTRADICTION,
    "description_only_min": DESC_ONLY_MIN,
    "min_cutaway_seconds": MIN_CUTAWAY,
    "broll_cap": BROLL_CAP,
    "baseline": "median over this lesson's speech windows",
}


@dataclass
class Window:
    index: int
    segment_ids: list[str]
    start: float
    end: float
    text: str

    @property
    def span(self) -> float:
        return self.end - self.start


def build_windows(transcript: dict) -> list[Window]:
    """Each segment, plus each segment with the next one when the pair stays short."""
    segments = [item for item in transcript.get("segments") or [] if item.get("end") is not None]
    windows: list[Window] = []

    def text_of(segment: dict) -> str:
        return (segment.get("original_text") or segment.get("text") or "").strip()

    for i, segment in enumerate(segments):
        start, end = float(segment["start"]), float(segment["end"])
        if end - start >= MIN_CUTAWAY:
            windows.append(Window(len(windows), [str(segment.get("id"))], start, end, text_of(segment)))
        if i + 1 < len(segments):
            nxt = segments[i + 1]
            pair_end = float(nxt["end"])
            if pair_end - start <= MAX_PAIR_SPAN and pair_end - start >= MIN_CUTAWAY:
                windows.append(Window(len(windows), [str(segment.get("id")), str(nxt.get("id"))], start, pair_end, f"{text_of(segment)} {text_of(nxt)}".strip()))
    return [window for window in windows if window.text]


def _unit(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _relative(raw: np.ndarray, fixed: float) -> np.ndarray:
    baseline = float(np.median(raw)) if raw.size >= MIN_WINDOWS_FOR_MEDIAN else fixed
    return raw - baseline


def lexical(description: str, speech: str) -> tuple[int, float]:
    described = content_tokens(description)
    spoken = set(content_tokens(speech))
    shared = [token for token in dict.fromkeys(described) if token in spoken]
    coverage = len(shared) / len(set(described)) if described else 0.0
    return len(shared), round(coverage, 3)


def _vectors(table: dict, texts: list[str]) -> np.ndarray | None:
    if not texts or any(text not in table for text in texts):
        return None
    return np.stack([table[text] for text in texts])


def evaluate_asset(asset: dict, windows: list[Window], batch) -> dict:
    """Score one clip against every window. Returns ranked rows and the asset-level evidence."""
    asset_id = asset["asset_id"]
    description = asset.get("description") or ""
    analysis = (batch.analyses.get(asset_id) if batch else None) or {}
    texts = [window.text for window in windows]
    sem = None
    embed_windows = _vectors(batch.embed_text, texts) if batch else None
    if embed_windows is not None and batch is not None and description in batch.embed_text:
        sem = _relative(embed_windows @ batch.embed_text[description], EMBED_BASELINE)
    vis = None
    best_frame: list[float | None] = [None] * len(windows)
    consistency = None
    frames = batch.frame_vectors.get(asset_id) if batch else None
    if analysis.get("performed") and frames:
        clip_windows = _vectors(batch.clip_text, texts)
        if clip_windows is not None:
            matrix = np.stack([vector for _, vector in frames])
            sims = matrix @ clip_windows.T
            vis = _relative(sims.max(axis=0), CLIP_BASELINE)
            stamps = [stamp for stamp, _ in frames]
            best_frame = [stamps[int(i)] for i in sims.argmax(axis=0)]
            from app.services.visual.analysis import DISTRACTOR_CAPTIONS

            distractors = _vectors(batch.clip_text, DISTRACTOR_CAPTIONS)
            if description in batch.clip_text and distractors is not None:
                consistency = float((matrix @ batch.clip_text[description]).mean() - (matrix @ distractors.T).mean())
    elif analysis.get("performed") and analysis.get("summary") and batch is not None and analysis["summary"] in batch.embed_text and embed_windows is not None:
        summary = batch.embed_text[analysis["summary"]]
        vis = _relative(embed_windows @ summary, EMBED_BASELINE)
        from app.services.visual.analysis import DISTRACTOR_SENTENCES

        distractors = _vectors(batch.embed_text, DISTRACTOR_SENTENCES)
        if description in batch.embed_text and distractors is not None:
            consistency = float(summary @ batch.embed_text[description] - (distractors @ summary).mean())

    if vis is not None:
        mode = "combined"
    elif sem is not None:
        mode = "description"
    else:
        mode = "description_lexical"
    contradicted = consistency is not None and consistency < CONTRADICTION
    rows = []
    for i, window in enumerate(windows):
        shared, coverage = lexical(description, window.text)
        d_sem = _unit(float(sem[i]) / SEM_SCALE) if sem is not None else None
        d_lex = 1.0 if shared >= 2 else (0.5 if shared == 1 else 0.0)
        description_relevance = d_sem if d_sem is not None else d_lex
        visual_relevance = _unit(float(vis[i]) / VIS_SCALE) if vis is not None else None
        if mode == "combined":
            combined = 0.55 * visual_relevance + 0.45 * description_relevance
            accepted = combined >= ACCEPT and visual_relevance >= MIN_VISUAL and description_relevance >= MIN_DESCRIPTION and not contradicted
            if contradicted:
                reason = "The sampled frames do not resemble the clip's description."
            elif visual_relevance < MIN_VISUAL:
                reason = f"The frames do not show what this speech is about (visual {visual_relevance:.2f} < {MIN_VISUAL})."
            elif description_relevance < MIN_DESCRIPTION:
                reason = f"The description is unrelated to this speech (description {description_relevance:.2f} < {MIN_DESCRIPTION})."
            elif combined < ACCEPT:
                reason = f"Combined relevance {combined:.2f} is under {ACCEPT}."
            else:
                reason = ""
            confidence = combined
        elif mode == "description":
            combined = description_relevance
            accepted = description_relevance >= DESC_ONLY_MIN and shared >= 1
            reason = "" if accepted else (
                f"Without analysed frames the description must be very close to the speech (needs {DESC_ONLY_MIN}, got {description_relevance:.2f}) and share a word with it."
            )
            confidence = 0.6 * combined
        else:
            combined = coverage if shared >= 2 else 0.0
            accepted = shared >= 2 and coverage >= 0.5
            reason = "" if accepted else "Without a text model, at least two description words must appear in the speech and cover half the description."
            confidence = 0.4 * combined
        rows.append(
            {
                "window": window,
                "description_relevance": round(description_relevance, 3),
                "visual_relevance": None if visual_relevance is None else round(visual_relevance, 3),
                "combined": round(combined, 3),
                "confidence": round(_unit(confidence), 3),
                "shared_words": shared,
                "best_frame": best_frame[i],
                "accepted": accepted,
                "reason": reason,
            }
        )
    singles = {row["window"].segment_ids[0]: row["combined"] for row in rows if len(row["window"].segment_ids) == 1}
    for row in rows:
        ids = row["window"].segment_ids
        if len(ids) < 2 or not row["accepted"]:
            continue
        best_single = max(singles.get(segment, 0.0) for segment in ids)
        if row["combined"] <= best_single + 1e-6:
            row["accepted"] = False
            row["reason"] = "A single sentence inside this window matches at least as well, so the shorter window is preferred."
    rows.sort(key=lambda row: (-row["combined"], row["window"].span, row["window"].start))
    return {"mode": mode, "consistency": consistency, "contradicted": contradicted, "rows": rows, "analysis": analysis}


def _explanation(asset: dict, row: dict, mode: str, consistency: float | None) -> str:
    window = row["window"]
    if mode == "combined":
        agree = f" The frames also agree with the description ({consistency:+.3f})." if consistency is not None else ""
        return (
            f"The sampled frames match this speech (visual relevance {row['visual_relevance']:.2f}) and the description "
            f"\u201c{asset.get('description')}\u201d is related to it (description relevance {row['description_relevance']:.2f}).{agree}"
        )
    if mode == "description":
        return (
            f"The description \u201c{asset.get('description')}\u201d is semantically close to this speech "
            f"(description relevance {row['description_relevance']:.2f}). No frames were analysed, so the picture itself was not checked."
        )
    return (
        f"{row['shared_words']} words of the description \u201c{asset.get('description')}\u201d appear in this speech. "
        "No text model or frame analysis was available, so this is a keyword match only."
    )


def _evidence_sources(mode: str) -> list[str]:
    return {"combined": ["description", "visual_analysis"], "description": ["description"], "description_lexical": ["description_keywords"]}[mode]


def match_assets(transcript: dict, catalogue: list[dict], batch, lesson_seconds: float) -> tuple[list[dict], list[dict]]:
    """Pick at most one placement per clip. Prefer no cutaway over a weak one."""
    windows = build_windows(transcript)
    budget = lesson_seconds * BROLL_CAP
    evaluations = []
    offers = []
    for asset in catalogue:
        result = evaluate_asset(asset, windows, batch)
        analysis = result["analysis"]
        clip_seconds = float(asset.get("duration_seconds") or 0.0)
        top = result["rows"][:3]
        evaluations.append(
            {
                "asset_id": asset["asset_id"],
                "filename": asset.get("filename"),
                "evidence_mode": result["mode"],
                "visual_analysis_performed": bool(analysis.get("performed")),
                "consistency": None if result["consistency"] is None else round(result["consistency"], 3),
                "contradicted": result["contradicted"],
                "status": "abstained",
                "reason": "",
                "top_windows": [
                    {
                        "segment_ids": row["window"].segment_ids,
                        "speech_text": row["window"].text,
                        "start": row["window"].start,
                        "end": row["window"].end,
                        "description_relevance": row["description_relevance"],
                        "visual_relevance": row["visual_relevance"],
                        "combined": row["combined"],
                        "accepted": row["accepted"],
                        "reason": row["reason"],
                    }
                    for row in top
                ],
            }
        )
        if clip_seconds < MIN_CUTAWAY:
            evaluations[-1]["reason"] = f"The clip is shorter than {MIN_CUTAWAY:.0f} s."
            continue
        accepted = [row for row in result["rows"] if row["accepted"]]
        if not accepted:
            evaluations[-1]["reason"] = (top[0]["reason"] if top else "There is no speech window to match.") or "No window cleared the thresholds."
            continue
        for row in accepted:
            offers.append((row, asset, result))

    offers.sort(key=lambda item: (-item[0]["combined"], item[0]["window"].span, item[0]["window"].start))
    chosen: list[dict] = []
    used_assets: set[str] = set()
    used_segments: set[str] = set()
    taken: list[tuple[float, float]] = []
    total = 0.0
    notes: dict[str, str] = {}
    for row, asset, result in offers:
        asset_id = asset["asset_id"]
        if asset_id in used_assets:
            continue
        window = row["window"]
        clip_seconds = float(asset.get("duration_seconds") or 0.0)
        play = round(min(clip_seconds, window.span), 3)
        if play < MIN_CUTAWAY:
            notes.setdefault(asset_id, f"The clip or speech window is shorter than {MIN_CUTAWAY:.0f} s.")
            continue
        start, end = window.start, round(window.start + play, 3)
        if any(start < b and end > a for a, b in taken) or used_segments.intersection(window.segment_ids):
            notes.setdefault(asset_id, "Its best speech already has a stronger cutaway.")
            continue
        if total + play > budget + 1e-6:
            notes.setdefault(asset_id, "Using it would exceed the 58% B-roll cap.")
            continue
        best = row["best_frame"]
        source_in = 0.0 if best is None else max(0.0, min(clip_seconds - play, float(best) - play / 2))
        source_in = round(max(0.0, source_in), 3)
        analysis = result["analysis"]
        chosen.append(
            {
                "asset_id": asset_id,
                "filename": asset.get("filename"),
                "description": asset.get("description"),
                "visual_summary": analysis.get("summary") or "",
                "segment_ids": window.segment_ids,
                "speech_text": window.text,
                "speech_start": round(start, 3),
                "speech_end": end,
                "source_in": source_in,
                "source_out": round(source_in + play, 3),
                "description_relevance": row["description_relevance"],
                "visual_relevance": row["visual_relevance"],
                "score": row["combined"],
                "confidence": row["confidence"],
                "evidence": result["mode"],
                "evidence_sources": _evidence_sources(result["mode"]),
                "explanation": _explanation(asset, row, result["mode"], result["consistency"]),
                "approval": "approved",
                "approved_by": "matcher",
                "origin": "user",
                "clip_duration": clip_seconds,
                "duration_seconds": clip_seconds,
            }
        )
        used_assets.add(asset_id)
        used_segments.update(window.segment_ids)
        taken.append((start, end))
        total += play
    for item in evaluations:
        if item["asset_id"] in used_assets:
            item["status"] = "selected"
            item["reason"] = ""
        elif not item["reason"]:
            item["reason"] = notes.get(item["asset_id"], "No window cleared the thresholds.")
    chosen.sort(key=lambda item: item["speech_start"])
    return chosen, evaluations
