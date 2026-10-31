"""Optional editing assistant: plain English or Hinglish instructions become validated plan edits.

Nothing the assistant produces is executed as code. An instruction is turned into a short
list of structured operations (move_broll, remove_broll, adjust_music_volume, ...). Each
operation is checked against the session's real clips, transcript and settings, then
applied through the normal review path, so every change is a new plan version that can be
undone. Ambiguous instructions get a question back instead of a guess.

The rules below cover the common instructions without any external service. When
GEMINI_API_KEY is configured on the API service, Gemini may propose operations for
instructions the rules do not understand; its output goes through the same validation.
"""

from __future__ import annotations

import copy
import json
import os
import re

from app.errors import StudioError
from app.processing.director import CONCEPTS, _norm, concepts_in, describe, find_phrase, spelling
from app.processing.settings import EFFECTS

OPERATIONS = {
    "move_broll", "insert_broll", "remove_broll", "change_broll_duration", "shift_broll", "replace_broll",
    "select_music", "adjust_music_volume", "enable_effect", "disable_effect", "adjust_effect_intensity",
    "adjust_caption_style", "adjust_video_framing",
}
REMOVE = {"remove", "delete", "hata", "hatao", "hatado", "nikal", "nikalo", "drop", "mat"}
PLACE = {"move", "place", "put", "insert", "lagao", "laga", "lagado", "daalo", "dalo", "rakho", "shift", "set"}
LATER = {"aage", "later", "forward", "baad"}
EARLIER = {"peeche", "pichhe", "earlier", "back", "pehle", "pahle"}
LOUDER = {"louder", "loud", "zyada", "jyada", "badhao", "increase", "up", "tez", "high"}
QUIETER = {"subtle", "softer", "quieter", "kam", "lower", "dheere", "dheema", "low", "halka", "decrease", "down"}
BIGGER = {"bada", "badi", "bade", "bigger", "larger", "large", "increase", "badhao"}
SMALLER = {"chhota", "chota", "chhoti", "choti", "smaller", "small", "decrease"}
UP = {"upar", "higher", "up", "above"}
DOWN = {"neeche", "niche", "lower", "down", "below"}
EFFECT_WORDS = {
    "glitter": "glitter", "sparkle": "sparkle", "sparkles": "sparkle", "cinematic": "cinematic", "warm": "warm", "cool": "cool",
    "vignette": "vignette", "zoom": "zoom", "contrast": "contrast", "brightness": "brightness", "saturation": "saturation",
    "blur": "background_blur", "bw": "black_and_white", "grayscale": "black_and_white", "greyscale": "black_and_white",
}
REFERENCE_FILLER = {"wala", "wali", "wale", "b-roll", "broll", "b", "roll", "clip", "video", "the", "is", "ye", "yeh", "isko", "usko", "ko", "ka", "ki", "ke"}
DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|sec|s\b|second)", re.I)
PHRASE_MARKERS = (
    r"jab\s+(?:sir|teacher)?\s*(?:bolte|bolta|kehte|kehta|kahte)\s+(?:hain|hai)\s+",
    r"where\s+(?:sir|the teacher)\s+says\s+",
    r"when\s+(?:sir|the teacher)\s+says\s+",
    r"sentence\s+where\s+(?:sir|the teacher)\s+says\s+",
    r"(?:sir|teacher)\s+(?:says|said)\s+",
    r"jab\s+(?:sir|teacher)\s+",
)


def _words(text: str) -> list[str]:
    return _norm(text)


def resolve_asset(instruction: str, placements: list[dict], catalogue: list[dict]) -> tuple[dict | None, list[dict]]:
    """The clip the instruction is about, by its description's concepts and words. Several equal matches = ambiguous."""
    tokens = set(_words(instruction))
    concepts = concepts_in(tokens)
    scored = []
    for item in catalogue:
        reading = describe(item)
        hits = len(set(reading["concepts"]) & concepts)
        hits += len({token for token in reading["raw_tokens"] if token in tokens and token not in REFERENCE_FILLER and not concepts_in({token})})
        name_tokens = {token for token in _words(item.get("filename") or "") if len(token) > 3}
        hits += len(name_tokens & tokens) * 0.5
        if hits:
            scored.append((hits, item))
    if not scored:
        return None, []
    scored.sort(key=lambda pair: -pair[0])
    best = [item for hits, item in scored if hits == scored[0][0]]
    if len(best) > 1:
        return None, best
    return best[0], []


def _target_phrase(instruction: str) -> str | None:
    text = instruction.strip().strip(".")
    quoted = re.search(r"[\"“”']([^\"“”']{4,})[\"“”']", text)
    if quoted:
        return quoted.group(1)
    for marker in PHRASE_MARKERS:
        found = re.search(marker, text, flags=re.I)
        if found:
            rest = text[found.end():]
            rest = re.split(r"\b(?:and keep|aur|and)\b.*?\b(?:seconds?|sec)\b", rest, maxsplit=1, flags=re.I)[0]
            return rest.strip(" ,.")
    return None


def parse(instruction: str, plan: dict, transcript: dict) -> dict:
    """Instruction to operations, with what was understood, or a clarifying question."""
    text = " ".join((instruction or "").split())[:500]
    if not text:
        raise StudioError(422, "empty_instruction", "Type an editing instruction.")
    tokens = set(_words(text))
    lowered = text.lower()
    placements = plan.get("placements") or []
    rejected = plan.get("rejected_placements") or []
    catalogue = plan.get("catalogue") or []
    if not catalogue:
        catalogue = [{"asset_id": item["asset_id"], "filename": item.get("filename"), "description": item.get("description")} for item in placements + rejected]
    ops: list[dict] = []
    notes: list[str] = []

    # music
    if tokens & {"music", "gaana", "song", "bgm", "background"} and not tokens & {"broll", "b-roll", "clip"}:
        if tokens & REMOVE or "no music" in lowered or "bina music" in lowered:
            ops.append({"operation": "select_music", "source": "none", "reason": "Explicit user instruction"})
            notes.append("turn the background music off")
        elif tokens & QUIETER:
            factor = 0.6 if tokens & {"bahut", "much", "very"} else 0.75
            ops.append({"operation": "adjust_music_volume", "factor": factor, "reason": "Explicit user instruction"})
            notes.append(f"lower the music to {int(factor * 100)}% of its current level")
        elif tokens & LOUDER:
            ops.append({"operation": "adjust_music_volume", "factor": 1.3, "reason": "Explicit user instruction"})
            notes.append("raise the music to 130% of its current level")
    # captions
    if tokens & {"caption", "captions", "subtitle", "subtitles", "text"}:
        if tokens & BIGGER:
            ops.append({"operation": "adjust_caption_style", "change": {"size_delta": 4}, "reason": "Explicit user instruction"})
            notes.append("make captions 4 px larger")
        elif tokens & SMALLER:
            ops.append({"operation": "adjust_caption_style", "change": {"size_delta": -4}, "reason": "Explicit user instruction"})
            notes.append("make captions 4 px smaller")
        if tokens & UP:
            ops.append({"operation": "adjust_caption_style", "change": {"position_delta": -0.05}, "reason": "Explicit user instruction"})
            notes.append("move captions up")
        elif tokens & DOWN and not tokens & SMALLER:
            ops.append({"operation": "adjust_caption_style", "change": {"position_delta": 0.05}, "reason": "Explicit user instruction"})
            notes.append("move captions down")
    # framing
    if tokens & {"teacher", "sir"} and tokens & {"screen", "space", "jagah", "bada", "bigger", "zoom", "frame", "chhota", "smaller"} and not tokens & {"bolte", "says", "said", "kehte"}:
        delta = -0.12 if tokens & SMALLER else 0.12
        ops.append({"operation": "adjust_video_framing", "teacher_height_delta": delta, "reason": "Explicit user instruction"})
        notes.append("give the teacher more of the screen" if delta > 0 else "give the teacher less of the screen")
    # effects
    named_effects = {EFFECT_WORDS[token] for token in tokens if token in EFFECT_WORDS}
    if "black and white" in lowered or "black & white" in lowered:
        named_effects.add("black_and_white")
    for effect in sorted(named_effects):
        if effect == "contrast" and tokens & {"caption", "captions"}:
            continue
        if tokens & REMOVE or tokens & {"off", "band", "disable"}:
            ops.append({"operation": "disable_effect", "effect": effect, "reason": "Explicit user instruction"})
            notes.append(f"turn {effect.replace('_', ' ')} off")
            continue
        scope = "intro" if tokens & {"intro", "start", "shuru", "beginning"} else ("broll" if tokens & {"broll", "b-roll", "cutaway", "cutaways"} else None)
        op = {"operation": "enable_effect", "effect": effect, "reason": "Explicit user instruction"}
        if scope and scope in EFFECTS[effect][1]:
            op["scope"] = scope
        if tokens & QUIETER or tokens & {"light", "halka", "thoda"}:
            op["intensity"] = 0.3
        elif tokens & LOUDER or tokens & {"strong", "zyada"}:
            op["intensity"] = 0.8
        ops.append(op)
        notes.append(f"turn {effect.replace('_', ' ')} on" + (f" only in the {scope}" if scope else ""))

    # B-roll
    broll_talk = bool(tokens & {"broll", "b-roll", "clip", "cutaway"} or "b roll" in lowered) or any(item for item in catalogue if resolve_asset(text, [], [item])[0])
    if broll_talk and not ops or (broll_talk and tokens & (REMOVE | PLACE | LATER | EARLIER)):
        asset, ambiguous = resolve_asset(text, placements, catalogue)
        if ambiguous:
            names = ", ".join(f"“{item.get('description') or item.get('filename')}”" for item in ambiguous[:4])
            return {"status": "clarify", "question": f"Which clip do you mean: {names}?", "operations": []}
        if asset is None and tokens & {"broll", "b-roll", "clip"} or (asset is None and not ops):
            descriptions = ", ".join(f"“{item.get('description') or item.get('filename')}”" for item in catalogue[:5])
            return {"status": "clarify", "question": f"Which clip should I change? This session has: {descriptions}.", "operations": []}
        if asset is not None:
            asset_id = asset["asset_id"]
            label = asset.get("description") or asset.get("filename")
            duration = DURATION.search(text)
            if tokens & REMOVE:
                ops.append({"operation": "remove_broll", "asset_id": asset_id, "reason": "Explicit user instruction"})
                notes.append(f"remove “{label}” and show the teacher instead")
            elif (tokens & LATER or tokens & EARLIER) and duration and not _target_phrase(text):
                seconds = float(duration.group(1)) * (1 if tokens & LATER else -1)
                ops.append({"operation": "shift_broll", "asset_id": asset_id, "seconds": seconds, "reason": "Explicit user instruction"})
                notes.append(f"move “{label}” {abs(seconds):g} s {'later' if seconds > 0 else 'earlier'}")
            else:
                target = _target_phrase(text)
                if target is None and not duration and tokens & PLACE:
                    target = text
                if target:
                    found = find_phrase(transcript, target)
                    if found is None:
                        return {"status": "clarify", "question": f"I could not find “{target}” in the transcript. Which words does the teacher say there?", "operations": []}
                    op = {"operation": "move_broll", "asset_id": asset_id, "target_phrase": target, "matched_phrase": found["phrase"], "target_start": found["start"], "target_segment_ids": found["segment_ids"], "reason": "Explicit user instruction"}
                    if duration:
                        op["duration_seconds"] = float(duration.group(1))
                    ops.append(op)
                    notes.append(f"place “{label}” at {found['start']:.2f} s, on “{found['phrase']}”" + (f", for {float(duration.group(1)):g} s" if duration else ""))
                elif duration:
                    ops.append({"operation": "change_broll_duration", "asset_id": asset_id, "duration_seconds": float(duration.group(1)), "reason": "Explicit user instruction"})
                    notes.append(f"keep “{label}” on screen for {float(duration.group(1)):g} s")
    if not ops:
        llm = gemini_operations(text, plan, transcript)
        if llm:
            return llm
        return {"status": "clarify", "question": "I did not understand which change to make. Try for example: “Move the newspaper B-roll to where sir says na hi maine newspaper padha”, “Phone wala B-roll hata do”, or “Background music thoda subtle kar do”.", "operations": []}
    ops, more = _make_room(ops, placements)
    notes += more
    return {"status": "proposal", "understood": "I will " + "; ".join(notes) + ".", "operations": ops}


def _make_room(ops: list[dict], placements: list[dict]) -> tuple[list[dict], list[str]]:
    """A moved or lengthened clip that would run into the next cutaway pushes that one later, as a visible extra step."""
    by_id = {item["asset_id"]: item for item in placements if item.get("approval") != "rejected"}
    touched = {op.get("asset_id") for op in ops}
    extra, notes = [], []
    for op in ops:
        if op["operation"] not in {"move_broll", "change_broll_duration"} or op.get("asset_id") not in by_id:
            continue
        current = by_id[op["asset_id"]]
        start = float(op.get("target_start", current["speech_start"]))
        length = float(op.get("duration_seconds") or (float(current["speech_end"]) - float(current["speech_start"])))
        end = start + length
        for other in sorted(by_id.values(), key=lambda item: float(item["speech_start"])):
            if other["asset_id"] in touched or other["asset_id"] == op["asset_id"]:
                continue
            if start < float(other["speech_start"]) < end - 1e-3:
                shift = round(end - float(other["speech_start"]), 3)
                extra.append({"operation": "shift_broll", "asset_id": other["asset_id"], "seconds": shift, "reason": "Makes room for the clip you moved"})
                notes.append(f"push “{other.get('description') or other.get('filename')}” {shift:g} s later, to {end:.2f} s, so the two do not overlap")
                touched.add(other["asset_id"])
    return ops + extra, notes


def gemini_operations(instruction: str, plan: dict, transcript: dict) -> dict | None:
    """Optional: ask Gemini for operations when GEMINI_API_KEY is set on the API. Output is validated like any other."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key or os.environ.get("STUDIO_ASSISTANT_LLM", "auto").lower() == "off":
        return None
    import httpx

    model = os.environ.get("STUDIO_ASSISTANT_MODEL", "gemini-2.5-flash")
    context = {
        "clips": [{"asset_id": item["asset_id"], "description": item.get("description")} for item in plan.get("catalogue") or plan.get("placements") or []],
        "placements": [{key_: item.get(key_) for key_ in ("asset_id", "speech_start", "speech_end", "matched_phrase")} for item in plan.get("placements") or []],
        "operations": sorted(OPERATIONS),
    }
    prompt = (
        "You turn a video editor's instruction into JSON. Reply with only {\"operations\": [...]} using these operation names: "
        + ", ".join(sorted(OPERATIONS))
        + ". Fields: asset_id, target_phrase (words the teacher says), duration_seconds, seconds, factor, effect, scope, change. "
        + "If unsure, reply {\"question\": \"...\"}.\nContext: " + json.dumps(context, ensure_ascii=False) + "\nInstruction: " + instruction
    )
    try:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        payload = json.loads(text)
    except Exception:  # noqa: BLE001 - the assistant is optional; fall back to asking the user
        return None
    if payload.get("question"):
        return {"status": "clarify", "question": str(payload["question"])[:300], "operations": []}
    ops = [op for op in payload.get("operations") or [] if isinstance(op, dict) and op.get("operation") in OPERATIONS]
    for op in ops:
        if op.get("target_phrase"):
            found = find_phrase(transcript, str(op["target_phrase"]))
            if found is None:
                return {"status": "clarify", "question": f"I could not find “{op['target_phrase']}” in the transcript.", "operations": []}
            op.update({"matched_phrase": found["phrase"], "target_start": found["start"], "target_segment_ids": found["segment_ids"]})
        op["reason"] = "Suggested by the assistant from the user's instruction"
    if not ops:
        return None
    return {"status": "proposal", "understood": "I will apply: " + ", ".join(op["operation"] for op in ops) + ".", "operations": ops, "source": "gemini"}


def _current(store, session_id: str, owner: str) -> tuple[dict, dict, dict]:
    from app.processing.versions import load_version

    record = store.get_session(session_id, owner)
    if record.get("status") != "plan_ready" or not record.get("plan_version"):
        raise StudioError(409, "plan_not_ready", "The assistant can edit the plan once processing has finished.")
    work = store.pull_work(session_id)
    version = load_version(work, record["plan_version"])
    transcript = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
    return record, version, transcript


def propose(store, session_id: str, owner: str, message: str) -> dict:
    _record, version, transcript = _current(store, session_id, owner)
    result = parse(message, version["studio"], transcript)
    result["base_version_id"] = version["version_id"]
    return result


def apply(store, session_id: str, owner: str, base_version_id: str, operations: list[dict], lock) -> dict:
    from app.processing.editing import apply_edits

    _record, version, transcript = _current(store, session_id, owner)
    if base_version_id != version["version_id"]:
        raise StudioError(409, "stale_plan", "The plan changed since the assistant answered. Ask again.")
    body = to_edit(operations, version["studio"], version.get("settings"), transcript)
    body.update({"base_version_id": base_version_id, "origin": "assistant", "assistant_operations": operations})
    return apply_edits(store, session_id, owner, body, lock)


def undo(store, session_id: str, owner: str, base_version_id: str, lock) -> dict:
    """Restore the parent version's clips, settings and title as a new version. Nothing is deleted."""
    from app.processing.editing import apply_edits
    from app.processing.versions import load_version

    record, version, _transcript = _current(store, session_id, owner)
    if base_version_id != version["version_id"]:
        raise StudioError(409, "stale_plan", "The plan changed. Reload it and try again.")
    parent_id = version.get("parent_version_id")
    if not parent_id:
        raise StudioError(409, "nothing_to_undo", "This is the first version of the plan; there is nothing to undo.")
    parent = load_version(store.work_dir(session_id), parent_id)
    rows = [_row(item) for item in (parent["studio"].get("placements") or []) + (parent["studio"].get("rejected_placements") or [])]
    body = {"base_version_id": base_version_id, "placements": rows, "origin": "undo", "title": (parent.get("title") or {}).get("text", "")}
    if parent.get("settings") is not None:
        body["settings"] = {key: value for key, value in parent["settings"].items() if key != "version"}
    elif version.get("settings") is not None:
        body["clear_settings"] = True
    return apply_edits(store, session_id, owner, body, lock)


def _row(item: dict) -> dict:
    return {key: item.get(key) for key in ("asset_id", "speech_start", "speech_end", "source_in", "source_out", "approval")}


def to_edit(operations: list[dict], plan: dict, settings: dict | None, transcript: dict) -> dict:
    """Operations to the body of an ordinary review edit. Every value is checked here or by apply_edits."""
    if not operations or not isinstance(operations, list) or len(operations) > 12:
        raise StudioError(422, "invalid_operations", "Send between one and twelve operations.")
    placements = {item["asset_id"]: _row(item) for item in plan.get("placements") or []}
    for item in plan.get("rejected_placements") or []:
        placements.setdefault(item["asset_id"], _row(item))
    clip_seconds = {item["asset_id"]: float(item.get("clip_duration") or item.get("duration_seconds") or 0) for item in (plan.get("placements") or []) + (plan.get("rejected_placements") or [])}
    for item in plan.get("catalogue") or []:
        clip_seconds.setdefault(item["asset_id"], float(item.get("duration_seconds") or 0))
    changes: dict = {}
    current = copy.deepcopy(settings) if settings else None

    def setting(section: str, key: str):
        from app.processing.settings import default_settings

        base = current or default_settings()
        return (changes.get(section) or {}).get(key, base[section][key])

    for op in operations:
        if not isinstance(op, dict) or op.get("operation") not in OPERATIONS:
            raise StudioError(422, "invalid_operations", "Unknown operation.")
        name = op["operation"]
        if name in {"move_broll", "insert_broll", "remove_broll", "change_broll_duration", "shift_broll"}:
            asset_id = str(op.get("asset_id") or "")
            if asset_id not in clip_seconds:
                raise StudioError(422, "invalid_operations", "The operation names a clip that is not in this session.")
            was_placed = asset_id in placements and placements[asset_id].get("approval") != "rejected"
            row = placements.get(asset_id) or {"asset_id": asset_id, "speech_start": 0.0, "speech_end": 2.0, "source_in": 0.0, "source_out": 2.0, "approval": "approved"}
            length = float(row["speech_end"]) - float(row["speech_start"])
            if name == "remove_broll":
                row["approval"] = "rejected"
            elif name in {"move_broll", "insert_broll"}:
                row["approval"] = "approved"
                row["target_phrase"] = str(op.get("target_phrase") or "")[:200]
                if not row["target_phrase"]:
                    raise StudioError(422, "invalid_operations", "Say where the clip should go.")
                if op.get("duration_seconds") is not None:
                    row["duration_seconds"] = float(op["duration_seconds"])
                elif not was_placed:
                    row["duration_seconds"] = 2.0
            elif name == "change_broll_duration":
                seconds = float(op.get("duration_seconds") or 0)
                if seconds <= 0:
                    raise StudioError(422, "invalid_operations", "The duration must be positive.")
                row["speech_end"] = round(float(row["speech_start"]) + seconds, 3)
                row["source_out"] = round(min(float(row["source_in"]) + seconds, clip_seconds[asset_id]), 3)
                row["source_in"] = round(max(0.0, row["source_out"] - seconds), 3)
            elif name == "shift_broll":
                seconds = float(op.get("seconds") or 0)
                row["speech_start"] = round(float(row["speech_start"]) + seconds, 3)
                row["speech_end"] = round(float(row["speech_end"]) + seconds, 3)
            placements[asset_id] = row
        elif name == "select_music":
            source = op.get("source") or ("library" if op.get("track_id") else None)
            changes.setdefault("music", {}).update({"source": source, **({"track_id": op["track_id"]} if op.get("track_id") else {}), **({"upload_id": op["upload_id"]} if op.get("upload_id") else {})})
        elif name == "upload_music":
            changes.setdefault("music", {}).update({"source": "upload", "upload_id": op.get("upload_id")})
        elif name == "adjust_music_volume":
            factor = float(op.get("factor") or 1.0)
            changes.setdefault("music", {})["volume"] = round(min(2.0, max(0.0, float(setting("music", "volume")) * factor)), 3)
        elif name in {"enable_effect", "disable_effect", "adjust_effect_intensity"}:
            effect = op.get("effect")
            if effect not in EFFECTS:
                raise StudioError(422, "invalid_operations", "Unknown effect.")
            update = {"enabled": name != "disable_effect"}
            if op.get("scope"):
                update["scope"] = op["scope"]
            if op.get("intensity") is not None:
                update["intensity"] = float(op["intensity"])
            changes.setdefault("effects", {})[effect] = update
        elif name == "adjust_caption_style":
            change = op.get("change") or {}
            caption = changes.setdefault("captions", {})
            if "size_delta" in change:
                caption["size"] = int(min(56, max(20, int(setting("captions", "size")) + int(change["size_delta"]))))
            if "position_delta" in change:
                caption["position"] = round(min(0.9, max(0.55, float(setting("captions", "position")) + float(change["position_delta"]))), 3)
            for key in ("size", "position", "highlight", "style", "max_words", "uppercase", "color"):
                if key in change:
                    caption[key] = change[key]
        elif name == "adjust_video_framing":
            framing = changes.setdefault("framing", {})
            if "teacher_height_delta" in op:
                framing["teacher_height"] = round(min(1.0, max(0.3, float(setting("framing", "teacher_height")) + float(op["teacher_height_delta"]))), 3)
                if framing["teacher_height"] >= 0.99:
                    framing["mode"] = "fill"
                elif setting("framing", "mode") == "fit":
                    framing["mode"] = "balanced"
            for key in ("mode", "focus_x", "focus_y"):
                if key in op:
                    framing[key] = op[key]
        elif name == "replace_broll":
            raise StudioError(422, "invalid_operations", "Replace the clip by uploading the new one in My B-Roll, then ask me to place it.")
    body = {"placements": list(placements.values())}
    if changes:
        body["settings"] = changes
    return body
