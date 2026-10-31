"""Portrait frame measured from the supplied reference short.

The reference is 720x1280. The logo sits on the left of the title, above a
rounded card. The picture fills that card. Sizes below are those measurements
scaled to the canvas.
"""

from __future__ import annotations


def reference_frame(width: int = 720, height: int = 1280) -> dict:
    logo = round(width * 0.112)
    logo_left = round(width * 0.139)
    logo_top = round(height * 0.145)
    title_size = round(height * 0.025)
    title_left = logo_left + logo + round(width * 0.02)
    title_right = round(width * 0.044)
    title_top = logo_top + max(0, round((logo - (title_size * 2)) / 2))
    card_left = round(width * 0.056)
    card_right = round(width * 0.071)
    card_top = round(height * 0.219)
    card_width = width - card_left - card_right
    card_height = round(height * 0.593)
    card_bottom = height - card_top - card_height
    return {
        "logo_size": logo,
        "logo_top": logo_top,
        "logo_left": logo_left,
        "logo_right": width - logo_left - logo,
        "title_top": title_top,
        "title_left": title_left,
        "title_right": title_right,
        "title_size": title_size,
        "title_align": "center",
        "card_top": card_top,
        "card_left": card_left,
        "card_width": card_width,
        "card_height": card_height,
        "card_bottom": card_bottom,
        "radius_px": 28,
        "border_px": 0,
        "border_color": "",
        "accent": "#f5c400",
        "caption_bottom": card_bottom + round(height * 0.045),
    }


def layout_issues(frame: dict, width: int = 720, height: int = 1280) -> list[str]:
    """Geometry problems. An empty list means the frame fits the reference rules."""
    issues = []
    logo_left = frame.get("logo_left", width - frame["logo_right"] - frame["logo_size"])
    if frame["logo_size"] > round(width * 0.16):
        issues.append("logo is oversized")
    if frame["logo_size"] < round(width * 0.10):
        issues.append("logo is smaller than the reference")
    if frame["logo_top"] < 0 or logo_left < 0 or logo_left + frame["logo_size"] > width:
        issues.append("logo is outside the canvas")
    if frame["logo_top"] + frame["logo_size"] > frame["card_top"]:
        issues.append("logo overlaps the content frame")
    if frame["title_left"] < logo_left + frame["logo_size"]:
        issues.append("title overlaps the logo")
    if frame["title_top"] < 0 or frame["title_left"] < 0:
        issues.append("title is outside the canvas")
    if frame["title_top"] >= frame["card_top"]:
        issues.append("title is not above the content frame")
    if frame["card_left"] < 0 or frame["card_top"] < 0:
        issues.append("content frame is outside the canvas")
    if frame["card_left"] + frame["card_width"] > width:
        issues.append("content frame is wider than the canvas")
    if frame["card_top"] + frame["card_height"] > height:
        issues.append("content frame is taller than the canvas")
    area = frame["card_width"] * frame["card_height"]
    if area < 0.50 * width * height:
        issues.append("content frame is too small")
    if frame.get("border_px"):
        issues.append("a frame stroke is drawn")
    if str(frame.get("border_color") or "").lower() in {"#d01212", "#e10600", "red"}:
        issues.append("a red outline is set")
    if frame["caption_bottom"] < frame["card_bottom"]:
        issues.append("caption sits below the content frame")
    return issues


def _em_width(text: str) -> float:
    """Uppercase Arial at weight 800, in em. Marks and extra spaces are ignored."""
    plain = " ".join(text.replace("[[", "").replace("]]", "").split())
    width = 0.0
    for char in plain.upper():
        if char == " ":
            width += 0.34
        elif char in {"I", "J", "L"}:
            width += 0.42
        elif char in {"M", "W"}:
            width += 0.92
        else:
            width += 0.74
    return width


def fit_title(text: str, frame: dict, canvas_width: int = 720) -> dict:
    """One line when the words fit beside the logo. Wrap only below a readable size."""
    single = " ".join((text or "").replace("\n", " ").split())
    available = canvas_width - frame["title_left"] - frame["title_right"]
    minimum = 28
    maximum = max(int(frame["title_size"]), 36)
    for size in range(maximum, minimum - 1, -1):
        if _em_width(single) * size <= available:
            top = frame["logo_top"] + max(0, round((frame["logo_size"] - size) / 2))
            return {
                "text": single,
                "font_size": size,
                "lines": 1,
                "align": "center",
                "top": top,
                "width": round(_em_width(single) * size, 1),
            }
    lines = [part.strip() for part in (text or "").split("\n") if part.strip()]
    if len(lines) < 2:
        words = single.split()
        mid = max(1, len(words) // 2)
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    size = int(frame["title_size"])
    return {
        "text": "\n".join(lines),
        "font_size": size,
        "lines": len(lines),
        "align": "center",
        "top": frame["title_top"],
        "width": round(max(_em_width(line) for line in lines) * size, 1),
    }
