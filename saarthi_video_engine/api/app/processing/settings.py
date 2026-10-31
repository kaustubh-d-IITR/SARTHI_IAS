"""Studio look and sound for one plan version: framing, captions, transitions, music, effects.

Settings are stored on each plan version. A version without settings (made before this
existed) renders with the original card layout, so older videos can be reproduced.
Every value here changes the rendered MP4; nothing is decorative.
"""

from __future__ import annotations

import copy
import re

from app.errors import StudioError

EFFECTS = {
    # name: (kind, scopes, default intensity, lowest, highest)
    "glitter": ("overlay", ("all", "intro", "broll"), 0.4, 0.0, 1.0),
    "sparkle": ("overlay", ("all", "intro", "broll"), 0.4, 0.0, 1.0),
    "cinematic": ("filter", ("all", "teacher", "broll"), 0.5, 0.0, 1.0),
    "warm": ("filter", ("all", "teacher", "broll"), 0.4, 0.0, 1.0),
    "cool": ("filter", ("all", "teacher", "broll"), 0.4, 0.0, 1.0),
    "black_and_white": ("filter", ("all", "teacher", "broll"), 1.0, 0.0, 1.0),
    "contrast": ("filter", ("all", "teacher", "broll"), 0.3, -1.0, 1.0),
    "saturation": ("filter", ("all", "teacher", "broll"), 0.3, -1.0, 1.0),
    "brightness": ("filter", ("all", "teacher", "broll"), 0.2, -1.0, 1.0),
    "vignette": ("overlay", ("all", "intro", "broll"), 0.4, 0.0, 1.0),
    "background_blur": ("backdrop", ("all",), 0.5, 0.0, 1.0),
    "zoom": ("motion", ("teacher",), 0.3, 0.0, 1.0),
}
FRAMING_MODES = ("fill", "balanced", "fit")
CAPTION_STYLES = ("box", "outline", "shadow")
BROLL_FITS = ("cover", "contain_blur")
TRANSITIONS = ("cut", "fade")
MUSIC_SOURCES = ("default", "library", "upload", "none")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_settings() -> dict:
    return {
        "version": 1,
        "framing": {"mode": "balanced", "teacher_height": 0.62, "focus_x": None, "focus_y": 0.4},
        "broll": {"fit": "cover"},
        "transitions": {"style": "cut", "seconds": 0.25},
        "captions": {"size": 34, "position": 0.8, "highlight": "#f5c400", "color": "#ffffff", "style": "box", "max_words": 3, "uppercase": True},
        "music": {
            "source": "default",
            "track_id": None,
            "upload_id": None,
            "volume": 1.0,
            "under_speech": 0.12,
            "in_pauses": 0.28,
            "fade_in": 0.5,
            "fade_out": 1.5,
            "ducking": True,
            "into_outro": True,
        },
        "effects": {name: {"enabled": False, "intensity": spec[2], "scope": spec[1][0]} for name, spec in EFFECTS.items()},
        "outro": {"fill": "blur"},
        "intro_seconds": 3.0,
    }


def _num(value, low: float, high: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "invalid_settings", f"{label} must be a number.") from exc
    if number != number or number < low - 1e-9 or number > high + 1e-9:
        raise StudioError(422, "invalid_settings", f"{label} must be between {low:g} and {high:g}.")
    return round(number, 3)


def _choice(value, options: tuple, label: str) -> str:
    if value not in options:
        raise StudioError(422, "invalid_settings", f"{label} must be one of: {', '.join(options)}.")
    return value


def _color(value, label: str) -> str:
    if not isinstance(value, str) or not _COLOR.match(value):
        raise StudioError(422, "invalid_settings", f"{label} must be a colour like #f5c400.")
    return value.lower()


def merge(base: dict | None, changes: dict | None) -> dict:
    """Apply a partial change to settings and validate the result. Unknown keys are refused."""
    current = copy.deepcopy(base) if base else default_settings()
    for key, value in default_settings().items():
        current.setdefault(key, copy.deepcopy(value))
    for name, value in default_settings()["effects"].items():
        current["effects"].setdefault(name, copy.deepcopy(value))
    if changes:
        if not isinstance(changes, dict):
            raise StudioError(422, "invalid_settings", "Settings must be an object.")
        for section, value in changes.items():
            if section not in current or section == "version":
                raise StudioError(422, "invalid_settings", f"Unknown settings section: {section}.")
            if isinstance(current[section], dict):
                if not isinstance(value, dict):
                    raise StudioError(422, "invalid_settings", f"{section} must be an object.")
                for key, item in value.items():
                    if section == "effects":
                        if key not in EFFECTS or not isinstance(item, dict):
                            raise StudioError(422, "invalid_settings", f"Unknown effect: {key}.")
                        unknown = set(item) - {"enabled", "intensity", "scope"}
                        if unknown:
                            raise StudioError(422, "invalid_settings", f"Unknown effect field: {sorted(unknown)[0]}.")
                        current["effects"][key].update(item)
                        continue
                    if key not in current[section]:
                        raise StudioError(422, "invalid_settings", f"Unknown setting: {section}.{key}.")
                    current[section][key] = item
            else:
                current[section] = value
    return validate(current)


def validate(settings: dict) -> dict:
    framing = settings["framing"]
    framing["mode"] = _choice(framing.get("mode"), FRAMING_MODES, "Framing")
    framing["teacher_height"] = _num(framing.get("teacher_height"), 0.3, 1.0, "Teacher height")
    if framing.get("focus_x") is not None:
        framing["focus_x"] = _num(framing["focus_x"], 0.0, 1.0, "Horizontal focus")
    framing["focus_y"] = _num(framing.get("focus_y"), 0.0, 1.0, "Vertical focus")
    settings["broll"]["fit"] = _choice(settings["broll"].get("fit"), BROLL_FITS, "B-roll fit")
    transitions = settings["transitions"]
    transitions["style"] = _choice(transitions.get("style"), TRANSITIONS, "Transition")
    transitions["seconds"] = _num(transitions.get("seconds"), 0.1, 0.6, "Transition length")
    captions = settings["captions"]
    captions["size"] = int(_num(captions.get("size"), 20, 56, "Caption size"))
    captions["position"] = _num(captions.get("position"), 0.55, 0.9, "Caption position")
    captions["highlight"] = _color(captions.get("highlight"), "Highlight colour")
    captions["color"] = _color(captions.get("color"), "Caption colour")
    captions["style"] = _choice(captions.get("style"), CAPTION_STYLES, "Caption style")
    captions["max_words"] = int(_num(captions.get("max_words"), 1, 8, "Words per caption"))
    captions["uppercase"] = bool(captions.get("uppercase"))
    music = settings["music"]
    music["source"] = _choice(music.get("source"), MUSIC_SOURCES, "Music source")
    for key, low, high, label in (
        ("volume", 0.0, 2.0, "Music volume"),
        ("under_speech", 0.0, 0.6, "Music during speech"),
        ("in_pauses", 0.0, 1.0, "Music during pauses"),
        ("fade_in", 0.0, 5.0, "Fade-in"),
        ("fade_out", 0.0, 8.0, "Fade-out"),
    ):
        music[key] = _num(music.get(key), low, high, label)
    music["ducking"] = bool(music.get("ducking"))
    music["into_outro"] = bool(music.get("into_outro"))
    if music["source"] == "library" and not music.get("track_id"):
        raise StudioError(422, "invalid_settings", "Choose a track from the music library.")
    if music["source"] == "upload" and not music.get("upload_id"):
        raise StudioError(422, "invalid_settings", "Upload a music file first.")
    for name, effect in settings["effects"].items():
        kind, scopes, _default, low, high = EFFECTS[name]
        effect["enabled"] = bool(effect.get("enabled"))
        effect["intensity"] = _num(effect.get("intensity"), low, high, f"{name} intensity")
        effect["scope"] = _choice(effect.get("scope"), scopes, f"{name} scope")
    settings["outro"]["fill"] = _choice(settings["outro"].get("fill"), ("blur", "black"), "Outro fill")
    settings["intro_seconds"] = _num(settings.get("intro_seconds"), 1.0, 10.0, "Intro length")
    settings["version"] = 1
    return settings
