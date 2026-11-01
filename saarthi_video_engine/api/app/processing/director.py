"""Editing director for Mode B: place each uploaded clip at the phrase it belongs to.

The transcript's segments are fixed-size chunks, not sentences, so the director first
rebuilds phrases from word timestamps (punctuation, pauses, clause starters such as
"na hi", "aur", "jab", "and", "because"). Each clip is then scored against every phrase
with evidence that can be inspected:

- keyword:   description concepts that are spoken in the phrase (with a small
             Hindi/English lexicon, e.g. akhbaar = newspaper, padha = read)
- action:    the description's action (read, open, watch...) said in the phrase
- negation:  a crossed-out clip ("red cross", "no", "not") only belongs to speech that
             negates ("na hi", "nahi", "didn't"); it never illustrates advice to do it
- semantic:  text-model similarity of description and phrase (when available)
- visual:    CLIP similarity of the clip's frames and the phrase (when available)
- timing and duration suitability

The earliest occurrence wins unless a later one is clearly stronger, so the newspaper
clip lands on "na hi maine newspaper padha", not on a later mention of newspaper.
The cutaway starts on the first word of the clause that names the subject. Cutaways on
consecutive phrases may run back to back for less than the usual minimum. Clips that do
not clear the thresholds are not used; the teacher stays on screen.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import numpy as np

from app.config import ENGINE_ROOT
from app.processing.intervals import fit_cutaway, usable_seconds
from app.processing.matching import BROLL_CAP, CLIP_BASELINE, CONTRADICTION, EMBED_BASELINE, MIN_WINDOWS_FOR_MEDIAN, SEM_SCALE, VIS_SCALE

CONFIG = {
    "matcher": "director-v1",
    "min_hold_seconds": 2.0,
    "max_hold_seconds": 5.0,
    "back_to_back_min_seconds": 1.0,
    "tail_seconds": 0.35,
    "lead_in_seconds": 0.0,
    "pause_split_seconds": 0.45,
    "max_phrase_words": 14,
    "accept": 0.5,
    "early_margin": 0.15,
    "weights": {"keyword": 0.40, "semantic": 0.20, "visual": 0.20, "action": 0.10, "negation": 0.10},
    "broll_cap": BROLL_CAP,
}

CONCEPTS = {
    "newspaper": {"newspaper", "newspapers", "akhbaar", "akhbar", "akhabaar", "akhabar", "akhbaron"},
    "ncert": {"ncert", "ncerts"},
    "book": {"book", "books", "textbook", "textbooks", "kitaab", "kitab", "kitaaben", "kitaben", "kitabein", "pustak"},
    "youtube": {"youtube", "you-tube"},
    "phone": {"phone", "phones", "mobile", "smartphone", "cellphone"},
    "video": {"video", "videos"},
    "lecture": {"lecture", "lectures", "class", "classes"},
    "coaching": {"coaching", "institute"},
    "hospital": {"hospital", "hospitals", "aspataal", "aspatal"},
    "mask": {"mask", "masks", "n95"},
    "ppe": {"ppe", "gown", "kit"},
    "exam": {"exam", "exams", "examination", "pariksha", "upsc", "prelims", "mains"},
    "notes": {"notes", "notebook", "notebooks"},
    "laptop": {"laptop", "computer"},
    "clock": {"clock", "time", "samay", "waqt", "watch"},
    "money": {"money", "paisa", "paise", "rupees"},
}
ACTIONS = {
    "read": {"read", "reads", "reading", "padha", "padhi", "padhe", "padh", "padhna", "padhana", "padhate", "padhte", "padhai", "padhaai"},
    "open": {"open", "opens", "opened", "opening", "khola", "kholi", "khol", "kholna", "kholte", "kholaa"},
    "watch": {"watch", "watched", "watching", "dekha", "dekhi", "dekh", "dekhna", "dekhte"},
    "write": {"write", "writes", "wrote", "writing", "likha", "likhi", "likh", "likhna", "likhte"},
    "wear": {"wear", "wears", "wearing", "wore", "pehna", "pahna", "pehan", "pahan", "pahane"},
    "use": {"use", "used", "using", "istemaal", "istemal"},
    "study": {"study", "studied", "studying", "padhai", "padhaai"},
    "scroll": {"scroll", "scrolling", "swipe"},
}
SPEECH_NEGATIONS = {"na", "naa", "nahi", "nahin", "nahiin", "mat", "not", "no", "never", "nor", "neither", "didn't", "didnt", "don't", "dont", "doesn't", "won't", "cannot", "can't", "bina", "without"}
DESCRIPTION_NEGATIONS = (
    r"\bred cross\b", r"\bcross(?:ed)?(?: out| mark| over)?\b", r"\bx mark\b", r"\bno\b", r"\bnot\b", r"\bwithout\b",
    r"\bprohibit", r"\bbann?ed?\b", r"\bwrong\b", r"\breject", r"\bstop sign\b",
)
CLAUSE_STARTERS = {"aur", "lekin", "magar", "par", "jab", "kyonki", "kyunki", "phir", "fir", "and", "but", "because", "so", "then", "when", "while", "still", "however"}
LEADING_CONJUNCTIONS = {"aur", "lekin", "magar", "par", "and", "but", "so", "then"}
LEAD_IN = {"na", "naa", "hi", "bhi", "to", "mainne", "maine", "mene", "main", "mai", "maiṁ", "hamne", "humne", "ham", "hum", "mera", "meri", "mere", "i", "we", "also", "my", "the", "a", "an", "ye", "yah", "yeh", "vo", "wo", "us", "is", "ek", "nahi", "nahin", "not", "never", "did", "didn't"}
STOP = {"the", "a", "an", "of", "with", "and", "or", "on", "in", "at", "to", "for", "is", "are", "was", "being", "its", "it", "over", "showing", "shows", "show", "someone", "person", "clip", "video"}
_TOKEN = re.compile(r"[a-z0-9'\-]+")


def _engine_transliterate():
    src = str(ENGINE_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from transcription.romanize import has_devanagari, transliterate

    return has_devanagari, transliterate


def _norm(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def concepts_in(tokens: list[str] | set[str]) -> set[str]:
    tokens = set(tokens)
    return {name for name, forms in CONCEPTS.items() if tokens & forms}


def actions_in(tokens: list[str] | set[str]) -> set[str]:
    tokens = set(tokens)
    return {name for name, forms in ACTIONS.items() if tokens & forms}


def description_is_negative(description: str) -> bool:
    text = (description or "").lower()
    return any(re.search(pattern, text) for pattern in DESCRIPTION_NEGATIONS)


@dataclass
class Word:
    text: str
    latin: str
    start: float
    end: float
    segment_id: str
    timed: bool = True

    @property
    def tokens(self) -> list[str]:
        return _norm(self.latin) or _norm(self.text)


@dataclass
class Phrase:
    index: int
    words: list[Word]
    tokens: set[str] = field(default_factory=set)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def latin(self) -> str:
        return " ".join(word.latin for word in self.words)

    @property
    def segment_ids(self) -> list[str]:
        return list(dict.fromkeys(word.segment_id for word in self.words))

    @property
    def negated(self) -> bool:
        return bool(self.tokens & SPEECH_NEGATIONS)

    def anchor(self, keywords: set[str]) -> int:
        """Index of the word where the cutaway should start: the clause that names the subject."""
        hit = next((i for i, word in enumerate(self.words) if set(word.tokens) & keywords), None)
        if hit is None:
            first = 0
            while first < len(self.words) - 1 and set(self.words[first].tokens) & LEADING_CONJUNCTIONS:
                first += 1
            return first
        i = hit
        while i > 0 and set(self.words[i - 1].tokens) & LEAD_IN and self.words[hit].start - self.words[i - 1].start <= 1.2:
            i -= 1
        return i


def words_of(transcript: dict) -> list[Word]:
    """Every word with its time. Segments without word timings are split evenly and marked untimed."""
    has_devanagari, transliterate = _engine_transliterate()
    words: list[Word] = []
    for segment in transcript.get("segments") or []:
        if segment.get("start") is None or segment.get("end") is None:
            continue
        sid = str(segment.get("id"))
        timed = [item for item in segment.get("words") or [] if item.get("start") is not None and item.get("end") is not None and str(item.get("text") or "").strip()]
        if timed:
            for item in timed:
                text = str(item["text"]).strip()
                latin = transliterate(text) if has_devanagari(text) else text
                words.append(Word(text, latin, float(item["start"]), float(item["end"]), sid))
            continue
        tokens = str(segment.get("original_text") or segment.get("text") or "").split()
        if not tokens:
            continue
        start, end = float(segment["start"]), float(segment["end"])
        step = (end - start) / len(tokens)
        for k, text in enumerate(tokens):
            latin = transliterate(text) if has_devanagari(text) else text
            words.append(Word(text, latin, round(start + k * step, 3), round(start + (k + 1) * step, 3), sid, timed=False))
    words.sort(key=lambda word: word.start)
    return words


def build_phrases(transcript: dict, config: dict | None = None) -> list[Phrase]:
    config = {**CONFIG, **(config or {})}
    words = words_of(transcript)
    phrases: list[list[Word]] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        tokens = set(word.tokens)
        starts_clause = False
        if current:
            previous = current[-1]
            if re.search(r"[.?!,;:।]$", previous.text.strip()):
                starts_clause = True
            elif word.start - previous.end >= config["pause_split_seconds"]:
                starts_clause = True
            elif tokens & CLAUSE_STARTERS:
                starts_clause = True
            elif tokens & {"na", "naa"} and i + 1 < len(words) and set(words[i + 1].tokens) & {"hi", "to"} and not (set(previous.tokens) & LEADING_CONJUNCTIONS):
                starts_clause = True
            elif len(current) >= config["max_phrase_words"]:
                starts_clause = True
        if starts_clause:
            phrases.append(current)
            current = []
        current.append(word)
    if current:
        phrases.append(current)
    result = []
    for words_in in phrases:
        tokens: set[str] = set()
        for word in words_in:
            tokens.update(word.tokens)
        result.append(Phrase(len(result), words_in, tokens))
    return result


def phrase_texts(transcript: dict) -> list[str]:
    """Texts the model worker should embed so phrases get semantic and visual scores."""
    texts = []
    for phrase in build_phrases(transcript):
        texts.append(phrase.latin)
    return list(dict.fromkeys(texts))


def _relative(raw: np.ndarray, fixed: float) -> np.ndarray:
    baseline = float(np.median(raw)) if raw.size >= MIN_WINDOWS_FOR_MEDIAN else fixed
    return raw - baseline


def _unit(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _vectors(table: dict, texts: list[str]) -> np.ndarray | None:
    if not table or not texts or any(text not in table for text in texts):
        return None
    return np.stack([table[text] for text in texts])


def describe(asset: dict) -> dict:
    """What the director reads from a clip's description."""
    description = asset.get("description") or ""
    tokens = [token for token in _norm(description) if token not in STOP]
    keywords = set(tokens) - STOP
    concept_names = concepts_in(tokens)
    for name in concept_names:
        keywords |= CONCEPTS[name]
    return {
        "concepts": sorted(concept_names),
        "actions": sorted(actions_in(tokens)),
        "negative": description_is_negative(description),
        "keywords": keywords,
        "raw_tokens": [token for token in tokens if len(token) >= 4],
    }


def score_asset(asset: dict, phrases: list[Phrase], batch=None, config: dict | None = None) -> dict:
    """Evidence for one clip against every phrase."""
    config = {**CONFIG, **(config or {})}
    weights = config["weights"]
    asset_id = asset["asset_id"]
    reading = describe(asset)
    description = asset.get("description") or ""
    texts = [phrase.latin for phrase in phrases]
    analysis = (batch.analyses.get(asset_id) if batch is not None else None) or {}
    semantic = None
    if batch is not None and description in (batch.embed_text or {}):
        vectors = _vectors(batch.embed_text, texts)
        if vectors is not None:
            semantic = _relative(vectors @ batch.embed_text[description], EMBED_BASELINE)
    visual = None
    best_frame: list[float | None] = [None] * len(phrases)
    consistency = None
    frames = (batch.frame_vectors.get(asset_id) if batch is not None else None) or []
    if analysis.get("performed") and frames:
        clip_vectors = _vectors(batch.clip_text, texts)
        if clip_vectors is not None:
            matrix = np.stack([vector for _, vector in frames])
            sims = matrix @ clip_vectors.T
            visual = _relative(sims.max(axis=0), CLIP_BASELINE)
            stamps = [stamp for stamp, _ in frames]
            best_frame = [stamps[int(i)] for i in sims.argmax(axis=0)]
            from app.services.visual.analysis import DISTRACTOR_CAPTIONS

            distractors = _vectors(batch.clip_text, DISTRACTOR_CAPTIONS)
            if description in (batch.clip_text or {}) and distractors is not None:
                consistency = float((matrix @ batch.clip_text[description]).mean() - (matrix @ distractors.T).mean())
    contradicted = consistency is not None and consistency < CONTRADICTION
    clip_seconds = usable_seconds(asset) or 0.0
    duration_fit = 1.0 if clip_seconds >= config["min_hold_seconds"] else (0.6 if clip_seconds >= config["back_to_back_min_seconds"] else 0.0)
    rows = []
    for i, phrase in enumerate(phrases):
        spoken_concepts = concepts_in(phrase.tokens)
        shared_concepts = sorted(set(reading["concepts"]) & spoken_concepts)
        shared_raw = sorted({token for token in reading["raw_tokens"] if token in phrase.tokens and not concepts_in({token})})
        hits = len(shared_concepts) + len(shared_raw)
        keyword = 0.0 if hits == 0 else min(1.0, 0.75 + 0.25 * (hits - 1))
        components: dict[str, float | None] = {"keyword": keyword}
        spoken_actions = actions_in(phrase.tokens)
        components["action"] = (1.0 if set(reading["actions"]) & spoken_actions else 0.0) if reading["actions"] else None
        components["negation"] = (1.0 if phrase.negated else 0.0) if reading["negative"] else None
        components["semantic"] = None if semantic is None else round(_unit(float(semantic[i]) / SEM_SCALE), 3)
        components["visual"] = None if visual is None else round(_unit(float(visual[i]) / VIS_SCALE), 3)
        present = {name: value for name, value in components.items() if value is not None}
        total_weight = sum(weights[name] for name in present)
        combined = sum(weights[name] * value for name, value in present.items()) / total_weight if total_weight else 0.0
        timing = 1.0 if all(word.timed for word in phrase.words) else 0.5
        combined *= 0.85 + 0.15 * timing
        reasons = []
        accepted = True
        if contradicted:
            accepted = False
            reasons.append("The sampled frames do not resemble the clip's description, so it is never inserted.")
        if duration_fit == 0.0:
            accepted = False
            reasons.append(f"The clip is shorter than {config['back_to_back_min_seconds']:.0f} s.")
        if reading["negative"] and not phrase.negated:
            accepted = False
            reasons.append("The clip shows a crossed-out or negative image, but this speech does not say 'no' or 'not'.")
        semantic_strong = (components["semantic"] or 0.0) >= 0.6 and (components["visual"] is None or components["visual"] >= 0.45)
        if hits == 0 and not semantic_strong:
            accepted = False
            reasons.append("Nothing in the description is spoken in this phrase.")
        if combined < config["accept"]:
            accepted = False
            reasons.append(f"Combined relevance {combined:.2f} is under {config['accept']}.")
        anchor_keywords = reading["keywords"] | set().union(*(ACTIONS[name] for name in reading["actions"])) if reading["actions"] else reading["keywords"]
        rows.append(
            {
                "phrase": phrase,
                "anchor": phrase.anchor(anchor_keywords if hits else set()),
                "scores": {name: (None if value is None else round(value, 3)) for name, value in components.items()} | {"timestamp": timing, "duration": duration_fit},
                "combined": round(combined, 3),
                "shared_concepts": shared_concepts,
                "shared_words": shared_raw,
                "best_frame": best_frame[i],
                "accepted": accepted,
                "reason": " ".join(reasons),
            }
        )
    return {"reading": reading, "rows": rows, "contradicted": contradicted, "consistency": consistency, "analysis": analysis}


def _choose_occurrence(rows: list[dict], margin: float, taken: set[int]) -> dict | None:
    """The earliest acceptable phrase, unless a later one is clearly stronger."""
    candidates = [row for row in rows if row["accepted"] and row["phrase"].index not in taken]
    if not candidates:
        return None
    best = max(row["combined"] for row in candidates)
    for row in sorted(candidates, key=lambda row: row["phrase"].start):
        if row["combined"] >= best - margin:
            return row
    return None


def _occurrence(rows: list[dict], chosen: dict) -> dict:
    concept_rows = [row for row in rows if row["shared_concepts"] or row["shared_words"]]
    concept_rows.sort(key=lambda row: row["phrase"].start)
    position = next((k for k, row in enumerate(concept_rows) if row is chosen), 0)
    return {"index": position + 1, "count": len(concept_rows), "terms": chosen["shared_concepts"] + chosen["shared_words"]}


def _explanation(asset: dict, row: dict, reading: dict) -> str:
    phrase = row["phrase"]
    parts = [f"The clip \u201c{asset.get('description')}\u201d is placed on \u201c{phrase.latin}\u201d"]
    if row["shared_concepts"] or row["shared_words"]:
        parts.append(f"because this phrase names {', '.join(row['shared_concepts'] + row['shared_words'])}")
    if reading["negative"] and phrase.negated:
        parts.append("and, like the crossed-out picture, the speech says it was not done")
    if reading["actions"] and row["scores"].get("action"):
        parts.append(f"with the same action ({', '.join(reading['actions'])})")
    sentence = " ".join(parts) + "."
    evidence = [f"keyword {row['scores']['keyword']:.2f}"]
    for name in ("semantic", "visual", "action", "negation"):
        if row["scores"].get(name) is not None:
            evidence.append(f"{name} {row['scores'][name]:.2f}")
    return f"{sentence} Evidence: {', '.join(evidence)}; combined {row['combined']:.2f}."


def _snap_end(words: list[Word], end: float, limit: float) -> float:
    """Do not cut back to the teacher in the middle of a word."""
    for word in words:
        if word.start < end < word.end and word.end - end <= 0.3 and word.end <= limit:
            return round(word.end, 3)
    return round(end, 3)


def direct(transcript: dict, catalogue: list[dict], batch, lesson_seconds: float, config: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Placements (at most one per clip) and an evaluation per clip. No clip is used to fill space."""
    config = {**CONFIG, **(config or {})}
    phrases = build_phrases(transcript, config)
    all_words = [word for phrase in phrases for word in phrase.words]
    results = {asset["asset_id"]: score_asset(asset, phrases, batch, config) for asset in catalogue}
    assets = {asset["asset_id"]: asset for asset in catalogue}
    picks: dict[str, dict] = {}
    taken: set[int] = set()
    order = sorted(catalogue, key=lambda asset: -max([row["combined"] for row in results[asset["asset_id"]]["rows"] if row["accepted"]] or [0.0]))
    for asset in order:
        row = _choose_occurrence(results[asset["asset_id"]]["rows"], config["early_margin"], taken)
        if row is not None:
            picks[asset["asset_id"]] = row
            taken.add(row["phrase"].index)

    planned = []
    for asset_id, row in picks.items():
        phrase = row["phrase"]
        anchor = phrase.words[row["anchor"]]
        clip_seconds = usable_seconds(assets[asset_id]) or 0.0
        start = max(0.0, anchor.start - config["lead_in_seconds"])
        natural = max(phrase.end + config["tail_seconds"], start + config["min_hold_seconds"])
        end = min(natural, start + config["max_hold_seconds"], start + clip_seconds, lesson_seconds)
        planned.append({"asset_id": asset_id, "row": row, "start": round(start, 3), "end": round(end, 3)})
    planned.sort(key=lambda item: item["start"])

    notes: dict[str, str] = {}
    kept: list[dict] = []
    for item in planned:
        if kept and item["start"] < kept[-1]["end"]:
            previous = kept[-1]
            if item["start"] - previous["start"] >= config["back_to_back_min_seconds"]:
                previous["end"] = item["start"]
                previous["back_to_back"] = True
            elif item["row"]["combined"] > previous["row"]["combined"]:
                notes[previous["asset_id"]] = "A stronger clip starts too close to it."
                kept[-1] = item
                continue
            else:
                notes[item["asset_id"]] = "A stronger clip already covers this moment."
                continue
        kept.append(item)
    for k, item in enumerate(kept):
        footage = usable_seconds(assets[item["asset_id"]]) or 0.0
        limit = min(kept[k + 1]["start"] if k + 1 < len(kept) else lesson_seconds, item["start"] + footage)
        if not item.get("back_to_back"):
            item["end"] = min(_snap_end(all_words, item["end"], limit), limit)
        item["end"] = round(min(item["end"], item["start"] + footage), 3)
        floor = config["back_to_back_min_seconds"] if item.get("back_to_back") else config["min_hold_seconds"]
        if item["end"] - item["start"] < floor - 1e-6:
            notes[item["asset_id"]] = f"There is not enough room for at least {floor:.1f} s on screen."
            item["drop"] = True
    kept = [item for item in kept if not item.get("drop")]

    budget = lesson_seconds * config["broll_cap"]
    while kept and sum(item["end"] - item["start"] for item in kept) > budget + 1e-6:
        weakest = min(kept, key=lambda item: item["row"]["combined"])
        notes[weakest["asset_id"]] = f"Using it would exceed the {config['broll_cap']:.0%} B-roll cap."
        kept.remove(weakest)

    placements = []
    for item in kept:
        asset = assets[item["asset_id"]]
        row = item["row"]
        phrase = row["phrase"]
        play = round(item["end"] - item["start"], 3)
        clip_seconds = usable_seconds(asset) or 0.0
        best = row["best_frame"]
        source_in = 0.0 if best is None else max(0.0, min(clip_seconds - play, float(best) - play / 2))
        floor = config["back_to_back_min_seconds"] if item.get("back_to_back") else config["min_hold_seconds"]
        fitted = fit_cutaway(item["start"], item["end"], source_in, clip_seconds, floor)
        if not fitted["ok"]:
            notes[item["asset_id"]] = fitted["reason"]
            continue
        item["end"], source_in, play = fitted["speech_end"], fitted["source_in"], round(fitted["speech_end"] - fitted["speech_start"], 3)
        result = results[item["asset_id"]]
        evidence = "combined" if row["scores"].get("visual") is not None else ("description" if row["scores"].get("semantic") is not None else "description_lexical")
        placements.append(
            {
                "asset_id": item["asset_id"],
                "filename": asset.get("filename"),
                "description": asset.get("description"),
                "visual_summary": result["analysis"].get("summary") or "",
                "segment_ids": phrase.segment_ids,
                "speech_text": phrase.text,
                "matched_phrase": phrase.latin,
                "phrase_start": round(phrase.start, 3),
                "phrase_end": round(phrase.end, 3),
                "speech_start": item["start"],
                "speech_end": item["end"],
                "source_in": source_in,
                "source_out": fitted["source_out"],
                "description_relevance": row["scores"].get("semantic") if row["scores"].get("semantic") is not None else row["scores"]["keyword"],
                "visual_relevance": row["scores"].get("visual"),
                "score": row["combined"],
                "confidence": row["combined"],
                "scores": row["scores"],
                "occurrence": _occurrence(result["rows"], row),
                "placement_rule": "back_to_back" if item.get("back_to_back") else "phrase_start",
                "evidence": evidence,
                "evidence_sources": ["description", "transcript_phrase"] + (["visual_analysis"] if evidence == "combined" else []),
                "explanation": _explanation(asset, row, result["reading"]),
                "approval": "approved",
                "approved_by": "director",
                "origin": "user",
                "clip_duration": clip_seconds,
                "duration_seconds": clip_seconds,
            }
        )
    placements.sort(key=lambda item: item["speech_start"])

    used = {item["asset_id"] for item in placements}
    evaluations = []
    for asset in catalogue:
        result = results[asset["asset_id"]]
        ranked = sorted(result["rows"], key=lambda row: (-row["combined"], row["phrase"].start))[:3]
        reason = ""
        if asset["asset_id"] not in used:
            reason = notes.get(asset["asset_id"]) or next((row["reason"] for row in ranked if row["reason"]), "") or "No phrase cleared the thresholds."
        evaluations.append(
            {
                "asset_id": asset["asset_id"],
                "filename": asset.get("filename"),
                "evidence_mode": "director",
                "description_reading": {key: result["reading"][key] for key in ("concepts", "actions", "negative")},
                "visual_analysis_performed": bool(result["analysis"].get("performed")),
                "consistency": None if result["consistency"] is None else round(result["consistency"], 3),
                "contradicted": result["contradicted"],
                "status": "selected" if asset["asset_id"] in used else "abstained",
                "reason": reason,
                "top_windows": [
                    {
                        "segment_ids": row["phrase"].segment_ids,
                        "speech_text": row["phrase"].latin,
                        "start": round(row["phrase"].start, 3),
                        "end": round(row["phrase"].end, 3),
                        "description_relevance": row["scores"]["keyword"],
                        "visual_relevance": row["scores"].get("visual"),
                        "combined": row["combined"],
                        "scores": row["scores"],
                        "accepted": row["accepted"],
                        "reason": row["reason"],
                    }
                    for row in ranked
                ],
            }
        )
    return placements, evaluations


def phrase_at(transcript: dict, seconds: float) -> dict | None:
    """The phrase being spoken at a time on the timeline, for placements moved by hand."""
    for phrase in build_phrases(transcript):
        if phrase.start - 0.05 <= seconds < phrase.end + 0.05:
            return {"phrase": phrase.latin, "speech_text": phrase.text, "segment_ids": phrase.segment_ids, "phrase_start": round(phrase.start, 3), "phrase_end": round(phrase.end, 3)}
    return None


SPELLINGS = {"mene": "maine", "mainy": "maine", "main": "mai", "mein": "men", "nhi": "nahi", "nai": "nahi", "nahin": "nahi", "padhi": "padha", "padhe": "padha", "kholi": "khola"}


def spelling(token: str) -> str:
    """One spelling for common Hinglish variants: naa/na, mainne/maine/mene, padhaa/padha."""
    collapsed = re.sub(r"(.)\1+", r"\1", token.lower())
    return SPELLINGS.get(collapsed, SPELLINGS.get(token.lower(), collapsed))


INSTRUCTION_FILLER = {
    "ka", "ki", "ke", "mention", "mentions", "wala", "wali", "wale", "jab", "sir", "teacher", "bolte", "bolta", "bole", "kehte", "kehta",
    "hain", "hai", "statement", "par", "pe", "line", "sentence", "where", "says", "say", "said", "when", "phrase", "karne", "karte",
    "uske", "baad", "waqt", "time", "us", "bilkul", "exactly", "moment", "part", "talks", "about", "speaks",
    "insert", "karo", "kar", "do", "lagao", "laga", "b-roll", "broll", "clip", "isko", "ko", "move", "shift", "place", "put", "keep", "rakho", "wahan", "there",
}


def _keys(token: str) -> set[str]:
    keys = {spelling(token)}
    keys |= concepts_in({token})
    keys |= {f"action:{name}" for name in actions_in({token})}
    return keys


def find_phrase(transcript: dict, target: str) -> dict | None:
    """The phrase a user names ("na hi mene newspaper padha", "YouTube open karne wala statement")."""
    wanted = [token for token in _norm(target) if token not in STOP and token not in INSTRUCTION_FILLER]
    if not wanted:
        return None
    phrases = build_phrases(transcript)
    best = None
    best_score = 0.0
    for phrase in phrases:
        spoken: set[str] = set()
        for token in phrase.tokens:
            spoken |= _keys(token)
        overlap = sum(1 for token in wanted if _keys(token) & spoken)
        score = overlap / len(wanted)
        if score > best_score + 1e-9:
            best, best_score = phrase, score
    if best is None or best_score < 0.5:
        return None
    wanted_keys = set().union(*(_keys(token) for token in wanted))
    keywords = {token for token in best.tokens if _keys(token) & wanted_keys and token not in LEAD_IN}
    anchor = best.words[best.anchor(keywords)]
    return {
        "phrase": best.latin,
        "speech_text": best.text,
        "segment_ids": best.segment_ids,
        "start": round(anchor.start, 3),
        "phrase_start": round(best.start, 3),
        "phrase_end": round(best.end, 3),
        "match": round(best_score, 3),
    }
