"""Full-canvas Studio layout for plans that carry render.studio settings.

Used only when the plan has a "studio" block; every other plan keeps the original
layouts in compose.py. The narration, music lane, title scene and corner logo are the
same as before. What changes:

- the teacher fills a configurable share of the 9:16 canvas, cropped around the point
  where the teacher actually is, over a blurred frame of the lesson (no black bars);
- B-roll is full screen (cover, or contained over its own blurred copy);
- cuts are hard cuts unless a short fade is chosen, so nothing is double-exposed;
- captions follow the user's size, position, colours, style and words per line, with
  the spoken word highlighted;
- effects are CSS filters, overlays and GSAP tweens drawn by the renderer itself.
"""

from __future__ import annotations

import json
import random

from render.frame import reference_frame
from .duck import volume_lane

SAFE_SIDE = 40
SAFE_BOTTOM = 56


def _esc(value) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _dur(decision: dict) -> float:
    return round(float(decision["end"]) - float(decision["start"]), 3)


def cover_position(src_w: int, src_h: int, box_w: float, box_h: float, focus_x: float, focus_y: float) -> tuple[float, float]:
    """object-position percentages that put the focus point in the middle of the box when the picture overflows it."""
    scale = max(box_w / src_w, box_h / src_h)
    img_w, img_h = src_w * scale, src_h * scale

    def axis(img: float, box: float, focus: float) -> float:
        if img - box < 1.0:
            return 50.0
        value = (box / 2 - focus * img) / (box - img)
        return round(max(0.0, min(1.0, value)) * 100, 2)

    return axis(img_w, box_w, focus_x), axis(img_h, box_h, focus_y)


def teacher_box(studio: dict, width: int, height: int) -> dict:
    teacher = studio["teacher"]
    sw, sh = int(teacher["src_width"]), int(teacher["src_height"])
    if teacher.get("box_height") is None:
        box_h = round(width * sh / sw)
    else:
        box_h = round(height * float(teacher["box_height"]))
    box_h = min(height, max(2, box_h))
    top = 0 if box_h >= height else round((height - box_h) * 0.42)
    pos_x, pos_y = cover_position(sw, sh, width, box_h, float(teacher["focus_x"]), float(teacher["focus_y"]))
    return {"top": top, "height": box_h, "pos_x": pos_x, "pos_y": pos_y, "full": box_h >= height}


FILTERS = {
    "cinematic": lambda i: f"contrast({1 + 0.15 * i:.3f}) saturate({1 - 0.15 * i:.3f}) sepia({0.12 * i:.3f}) brightness({1 - 0.03 * i:.3f})",
    "warm": lambda i: f"sepia({0.30 * i:.3f}) saturate({1 + 0.15 * i:.3f}) hue-rotate({-8 * i:.1f}deg)",
    "cool": lambda i: f"saturate({1 - 0.10 * i:.3f}) hue-rotate({14 * i:.1f}deg) brightness({1 + 0.02 * i:.3f})",
    "black_and_white": lambda i: f"grayscale({i:.3f})",
    "contrast": lambda i: f"contrast({1 + 0.5 * i:.3f})",
    "saturation": lambda i: f"saturate({max(0.0, 1 + 0.8 * i):.3f})",
    "brightness": lambda i: f"brightness({1 + 0.35 * i:.3f})",
}


def filter_css(effects: dict, scope: str) -> str:
    parts = [FILTERS[name](float(effect["intensity"])) for name, effect in effects.items() if name in FILTERS and effect.get("scope") == scope]
    return " ".join(parts)


def _windows(scope: str, duration: float, intro: float, brolls: list[dict]) -> list[tuple[float, float]]:
    if scope == "intro":
        return [(0.0, min(duration, intro))]
    if scope == "broll":
        return [(float(item["start"]), float(item["end"])) for item in brolls]
    return [(0.0, duration)]


def _particles(kind: str, effect: dict, windows: list[tuple[float, float]], width: int, height: int, caption_top: float, seed: int) -> tuple[str, list[str]]:
    """Twinkling points around the edges, away from the face and the caption line."""
    intensity = float(effect["intensity"])
    rng = random.Random(seed)
    count = round((10 + 26 * intensity) if kind == "glitter" else (4 + 10 * intensity))
    total = sum(b - a for a, b in windows)
    period = 1.6 if kind == "glitter" else 2.2
    while count > 4 and count * max(1.0, total / period) > 1400:
        count -= 2
    html, tweens = [], []
    for k in range(count):
        for _ in range(40):
            x = rng.uniform(0.04, 0.96) * width
            y = rng.uniform(0.04, 0.96) * height
            in_face = 0.18 * width < x < 0.82 * width and 0.2 * height < y < 0.7 * height
            in_caption = abs(y - caption_top) < 90
            if not in_face and not in_caption:
                break
        size = rng.uniform(7, 13) if kind == "glitter" else rng.uniform(16, 28)
        pid = f"fx-{kind}-{k}"
        if kind == "glitter":
            colour = rng.choice(["#ffd76a", "#fff4c2", "#ffe9a0", "#f5c400"])
            style = (
                f"width:{size:.1f}px;height:{size:.1f}px;border-radius:50%;"
                f"background:radial-gradient(circle,#ffffff 0%,{colour} 35%,{colour}00 72%);box-shadow:0 0 {size:.0f}px {size / 4:.1f}px {colour}aa;"
            )
        else:
            style = (
                f"width:{size:.1f}px;height:{size:.1f}px;background:#ffffff;"
                "clip-path:polygon(50% 0,58% 42%,100% 50%,58% 58%,50% 100%,42% 58%,0 50%,42% 42%);filter:drop-shadow(0 0 6px #fffc);"
            )
        html.append(f'<div id="{pid}" class="fx-particle" style="left:{x:.1f}px;top:{y:.1f}px;{style}"></div>')
        peak = round(min(1.0, 0.7 + 0.3 * intensity), 3)
        for start, end in windows:
            t = start + rng.uniform(0, period)
            while t + 0.8 <= end:
                tweens.append(f'tl.fromTo("#{pid}", {{opacity:0, scale:0.3}}, {{opacity:{peak}, scale:1, duration:0.35, ease:"power2.out"}}, {t:.3f});')
                tweens.append(f'tl.to("#{pid}", {{opacity:0, scale:0.3, y:-6, duration:0.45, ease:"power2.in"}}, {t + 0.4:.3f});')
                tweens.append(f'tl.set("#{pid}", {{y:0}}, {t + 0.86:.3f});')
                t += period * rng.uniform(0.8, 1.25)
    return "".join(html), tweens


def _caption_css(captions: dict, width: int, height: int) -> tuple[str, float]:
    size = int(captions["size"])
    center = float(captions["position"]) * height
    center = min(center, height - SAFE_BOTTOM - size * 1.6)
    style = captions["style"]
    if style == "box":
        span = f"background: rgba(10,10,10,0.68); border-radius: 12px; padding: 8px 16px; box-decoration-break: clone; -webkit-box-decoration-break: clone;"
        text_effect = "text-shadow: none;"
    elif style == "outline":
        span = "background: transparent; padding: 0 4px;"
        stroke = max(2, round(size / 12))
        text_effect = f"-webkit-text-stroke: {stroke}px #000; paint-order: stroke fill; text-shadow: 0 2px 6px rgba(0,0,0,0.6);"
    else:
        span = "background: transparent; padding: 0 4px;"
        text_effect = "text-shadow: 0 3px 10px rgba(0,0,0,0.85), 0 0 3px rgba(0,0,0,0.9);"
    transform = "uppercase" if captions.get("uppercase") else "none"
    css = f"""
      .caption {{
        position: absolute; left: {SAFE_SIDE}px; width: {width - 2 * SAFE_SIDE}px; top: {center:.1f}px; transform: translateY(-50%);
        z-index: 6; text-align: center; line-height: 1.25;
      }}
      .caption .line {{
        display: inline; color: {captions["color"]}; font-size: {size}px; font-weight: 800; letter-spacing: 0.01em;
        text-transform: {transform}; white-space: normal; word-break: keep-all; overflow-wrap: normal; hyphens: none; {span} {text_effect}
      }}
      .caption .w {{ display: inline-block; white-space: nowrap; }}
"""
    return css, center


def _captions_html(plan: dict, uppercase: bool) -> tuple[str, list[dict]]:
    blocks, timed = [], []
    for decision in plan["decisions"]:
        if decision["action"] != "caption":
            continue
        text = decision.get("text") or ""
        words = text.split()
        spans = "".join(f'<span class="w" id="{_esc(decision["id"])}-w{k}">{_esc(word)}</span>{" " if k < len(words) - 1 else ""}' for k, word in enumerate(words))
        shown = text.upper() if uppercase else text
        blocks.append(
            f'<div id="{_esc(decision["id"])}" class="clip caption" data-text="{_esc(shown)}" data-layout-allow-caption-zone="true" '
            f'data-start="{decision["start"]}" data-duration="{_dur(decision)}" data-track-index="4"><span class="line">{spans}</span></div>'
        )
        timed.append({"id": decision["id"], "start": float(decision["start"]), "end": float(decision["end"]), "words": words})
    return f'<div id="captions-host" data-track-kind="captions">{"".join(blocks)}</div>', timed


def _caption_tweens(timed: list[dict], captions: dict) -> list[str]:
    """Fade each line in and out; colour the word being spoken (time shared by word length inside the line)."""
    lines = []
    base, accent = captions["color"], captions["highlight"]
    for item in timed:
        target = f'#{item["id"]} .line'
        lines.append(f'tl.fromTo("{target}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.1, ease: "power2.out" }}, {item["start"]});')
        lines.append(f'tl.to("{target}", {{ opacity: 0, duration: 0.1, ease: "power2.in" }}, {max(item["start"], item["end"] - 0.1)});')
        lines.append(f'tl.set("{target}", {{ opacity: 0, visibility: "hidden" }}, {item["end"]});')
        words = item["words"]
        if len(words) < 2 or base == accent:
            continue
        total = sum(len(word) for word in words)
        cursor = item["start"]
        span = item["end"] - item["start"]
        for k, word in enumerate(words):
            wid = f'#{item["id"]}-w{k}'
            lines.append(f'tl.set("{wid}", {{ color: "{accent}" }}, {round(cursor, 3)});')
            cursor += span * len(word) / total
            if k < len(words) - 1:
                lines.append(f'tl.set("{wid}", {{ color: "{base}" }}, {round(cursor, 3)});')
    return lines


def _media(decision: dict, src: str, media_start: float, role: str, style: str, extra: float = 0.0, track: int = 1, suffix: str = "") -> str:
    tag_id = f'{decision["id"]}{suffix}'
    common = (
        f'id="{_esc(tag_id)}" class="clip {role}" src="{_esc(src)}" style="{style}" '
        f'data-start="{decision["start"]}" data-duration="{round(_dur(decision) + extra, 3)}" data-track-index="{track}"'
    )
    if decision.get("asset_kind") == "image" or src.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return f"<img {common} />"
    return f'<video {common} muted playsinline data-media-start="{media_start}"></video>'


def render_studio_html(plan: dict, defaults: dict, studio: dict, title_html: str) -> str:
    width, height = int(plan["render"]["width"]), int(plan["render"]["height"])
    duration = float(plan["input_media"]["duration_seconds"])
    effects = studio.get("effects") or {}
    intro = float(studio.get("intro_seconds") or 3.0)
    box = teacher_box(studio, width, height)
    teacher_src = plan["input_media"]["video_path"]
    transition = studio.get("transition") or {"style": "cut", "seconds": 0.25}
    fade = float(transition["seconds"]) if transition.get("style") == "fade" else 0.0

    teacher_filter = filter_css(effects, "teacher")
    broll_filter = filter_css(effects, "broll")
    stage_filter = filter_css(effects, "all")
    blur = effects.get("background_blur")
    backdrop_blur = 6 + 22 * float(blur["intensity"]) if blur else 8

    shown = [item for item in plan["decisions"] if item["action"] in {"show_teacher", "replace_with_broll", "overlay_broll"}]
    brolls = [item for item in shown if item["action"] != "show_teacher"]
    clips, tweens = [], []
    teacher_style = (
        f"position:absolute;left:0;top:{box['top']}px;width:{width}px;height:{box['height']}px;object-fit:cover;"
        f"object-position:{box['pos_x']}% {box['pos_y']}%;transform-origin:{studio['teacher']['focus_x'] * 100:.1f}% {studio['teacher']['focus_y'] * 100:.1f}%;"
        + (f"filter:{teacher_filter};" if teacher_filter else "")
    )
    zoom = effects.get("zoom")
    for index, decision in enumerate(shown):
        nxt = shown[index + 1] if index + 1 < len(shown) else None
        if decision["action"] == "show_teacher":
            hold = fade if nxt is not None and nxt["action"] != "show_teacher" else 0.0
            clips.append(_media(decision, teacher_src, decision["start"], "teacher", teacher_style, extra=hold))
            if zoom:
                scale = 1 + 0.08 * float(zoom["intensity"])
                tweens.append(f'tl.fromTo("#{decision["id"]}", {{ scale: 1 }}, {{ scale: {scale:.3f}, duration: {_dur(decision)}, ease: "none" }}, {decision["start"]});')
            continue
        src = decision.get("asset_path") or "assets/broll.mp4"
        size = (studio.get("broll_sizes") or {}).get(src)
        landscape = bool(size) and size[0] > size[1] * 1.05
        filt = f"filter:{broll_filter};" if broll_filter else ""
        if studio.get("broll_fit") == "contain_blur" and landscape:
            fitted_h = round(width * size[1] / size[0])
            clips.append(_media(decision, src, 0, "broll-backdrop", f"position:absolute;inset:0;width:{width}px;height:{height}px;object-fit:cover;filter:blur({backdrop_blur:.0f}px) brightness(0.7);transform:scale(1.08);", track=5, suffix="-bg"))
            clips.append(_media(decision, src, 0, "broll", f"position:absolute;left:0;top:{round((height - fitted_h) / 2)}px;width:{width}px;height:{fitted_h}px;object-fit:cover;{filt}", track=6))
        else:
            clips.append(_media(decision, src, 0, "broll", f"position:absolute;inset:0;width:{width}px;height:{height}px;object-fit:cover;object-position:center center;{filt}", track=6))
        if fade:
            tweens.append(f'tl.fromTo("#{decision["id"]}", {{ opacity: 0 }}, {{ opacity: 1, duration: {fade}, ease: "power1.out" }}, {decision["start"]});')

    captions = studio["captions"]
    caption_css, caption_center = _caption_css(captions, width, height)
    caption_html, timed = _captions_html(plan, bool(captions.get("uppercase")))
    tweens += _caption_tweens(timed, captions)

    overlays = []
    vignette = effects.get("vignette")
    if vignette:
        alpha = 0.25 + 0.45 * float(vignette["intensity"])
        for k, (start, end) in enumerate(_windows(vignette["scope"], duration, intro, brolls)):
            overlays.append(
                f'<div id="fx-vignette-{k}" class="clip fx-vignette" data-start="{start}" data-duration="{round(end - start, 3)}" data-track-index="7" '
                f'style="position:absolute;inset:0;z-index:5;pointer-events:none;background:radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 52%, rgba(0,0,0,{alpha:.2f}) 100%);"></div>'
            )
    particles = []
    for seed, kind in enumerate(("glitter", "sparkle")):
        effect = effects.get(kind)
        if not effect:
            continue
        html, lines = _particles(kind, effect, _windows(effect["scope"], duration, intro, brolls), width, height, caption_center, 101 + seed)
        particles.append(html)
        tweens += lines

    frame = reference_frame(width, height)
    corner = plan["render"].get("corner_logo") or ""
    corner_html = ""
    if corner:
        corner_html = f'<img id="corner-logo" class="clip" src="{_esc(corner)}" alt="" data-start="0" data-duration="{duration}" data-track-index="2" data-layout-ignore="true" />'
    backdrop = studio["teacher"].get("backdrop") or ""
    backdrop_html = ""
    if backdrop and not box["full"]:
        backdrop_html = (
            f'<img id="teacher-backdrop" class="clip" src="{_esc(backdrop)}" alt="" data-start="0" data-duration="{duration}" data-track-index="0" '
            f'style="position:absolute;inset:0;width:{width}px;height:{height}px;object-fit:cover;filter:blur({backdrop_blur:.0f}px);transform:scale(1.06);" />'
        )

    music_meta = {**plan["audio"]["music"], **{key: value for key, value in (studio.get("music") or {}).items() if key in {"fade_in", "fade_out"}}}
    audio_defaults = defaults["audio"]
    music_tag = ""
    if music_meta.get("path"):
        speech = [(segment["start"], segment["end"]) for segment in plan["input_media"]["transcript"]["segments"]]
        under = float(music_meta.get("gain_under_speech", audio_defaults["music_gain_under_speech"]))
        open_level = float(music_meta.get("gain_open", audio_defaults["music_gain_open"]))
        lane = volume_lane(duration, speech, ducked=under, open_level=open_level, attack=audio_defaults["duck_attack_seconds"], release=audio_defaults["duck_release_seconds"])
        music_tag = f"""
      <audio id="music-bed" class="clip" src="{_esc(music_meta["path"])}" data-start="0" data-duration="{duration}" data-track-index="11"
        data-volume="{under}" data-fade-in="{music_meta.get("fade_in", 0.2)}" data-fade-out="{music_meta.get("fade_out", 0.3)}"
        data-automation='{json.dumps(lane, separators=(",", ":"))}'></audio>"""

    stage_style = f"filter:{stage_filter};" if stage_filter else ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Sarthi studio {_esc(plan["plan_id"])}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      html, body {{ margin: 0; padding: 0; width: {width}px; height: {height}px; overflow: hidden; background: #0b0b0d; font-family: "Inter", Arial, sans-serif; }}
      #root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; }}
      #canvas-bg {{ position: absolute; inset: 0; background: #0b0b0d; z-index: 0; }}
      #stage {{ position: absolute; inset: 0; z-index: 1; {stage_style} }}
      video, img.clip {{ position: absolute; }}
      #top-shade {{ position: absolute; left: 0; top: 0; width: {width}px; height: {round(height * 0.26)}px; z-index: 2; pointer-events: none;
        background: linear-gradient(to bottom, rgba(0,0,0,0.55), rgba(0,0,0,0)); }}
      #title-card {{ position: absolute; inset: 0; z-index: 9; }}
      #corner-logo {{ position: absolute; top: {frame["logo_top"]}px; left: {frame["logo_left"]}px; width: {frame["logo_size"]}px; height: {frame["logo_size"]}px; z-index: 8; object-fit: contain; }}
      #fx-layer {{ position: absolute; inset: 0; z-index: 7; pointer-events: none; }}
      .fx-particle {{ position: absolute; opacity: 0; }}
      {caption_css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-layout-family="studio_fill" data-start="0" data-duration="{duration}" data-width="{width}" data-height="{height}">
      <div id="canvas-bg" data-layout-ignore="true"></div>
      <div id="stage" data-layout-allow-overflow="true">
        {backdrop_html}
        {"".join(clips)}
      </div>
      <div id="top-shade" data-layout-ignore="true"></div>
      {"".join(overlays)}
      <div id="fx-layer" data-layout-ignore="true">{"".join(particles)}</div>
      {title_html}
      {caption_html}
      {corner_html}
      <audio id="narration" class="clip" src="{_esc(plan["audio"]["narration_path"])}" data-start="0" data-duration="{duration}" data-track-index="10" data-volume="1"></audio>
      {music_tag}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      {chr(10).join("      " + line for line in tweens)}
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""
