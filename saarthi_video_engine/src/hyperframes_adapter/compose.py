"""Turn a validated editing plan into one HyperFrames index.html.

Video elements are muted. The original narration is a single audio clip that
spans the whole plan, so a picture cut does not cut or duplicate the voice.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from render.frame import fit_title, reference_frame
from render.halftone import halftone_background_css
from render.outro import tool
from treatment.reference_look import entrance_seconds, look_from_plan
from .duck import volume_lane

FAMILIES = (
    "bounded_card_with_title",
    "full_bleed_board_or_lecture",
    "split_teacher_and_graphic",
    "screen_recording_or_product_ui",
)


def write_composition(plan: dict, defaults: dict, destination: Path) -> Path:
    family = plan["style"]["layout_family"]
    if family not in FAMILIES:
        raise ValueError(f"no layout writer for {family}")
    html = render_html(plan, defaults, asset_root=destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    scene_dir = destination.parent / "compositions"
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "title-card.html").write_text(render_title_scene(plan, defaults), encoding="utf-8")
    return destination


def render_html(plan: dict, defaults: dict, asset_root: Path | None = None) -> str:
    family = plan["style"]["layout_family"]
    if defaults.get("studio"):
        from .studio_layout import render_studio_html

        return render_studio_html(plan, defaults, defaults["studio"], _title(plan, family, defaults["bounded_card"]))
    duration = plan["input_media"]["duration_seconds"]
    width = plan["render"]["width"]
    height = plan["render"]["height"]
    card = defaults["bounded_card"]
    audio_defaults = defaults["audio"]
    speech = [
        (segment["start"], segment["end"])
        for segment in plan["input_media"]["transcript"]["segments"]
    ]
    music_meta = plan["audio"]["music"]
    lane = volume_lane(
        duration,
        speech,
        ducked=music_meta.get("gain_under_speech", audio_defaults["music_gain_under_speech"]),
        open_level=music_meta.get("gain_open", audio_defaults["music_gain_open"]),
        attack=audio_defaults["duck_attack_seconds"],
        release=audio_defaults["duck_release_seconds"],
    )
    captions = _captions(plan, family, card, height)
    title = _title(plan, family, card)
    tweens = _caption_tweens(plan)
    automation = json.dumps(lane, separators=(",", ":"))
    music = music_meta["path"]
    narration = plan["audio"]["narration_path"]
    fit = plan.get("render", {}).get("object_fit") or "cover"
    look = look_from_plan(plan)
    teacher_filter = f"filter: contrast({look['contrast']}) saturate({look['saturation']});"
    broll_filter = teacher_filter if look["filter_broll"] else "filter: none;"
    timeline_tag = ""
    if look["timeline"]:
        timeline_tag = (
            '<script type="application/json" id="filter-timeline">'
            + json.dumps(look["timeline"])
            + "</script>"
        )
    music_tag = ""
    if music:
        music_tag = f"""
      <audio
        id="music-bed"
        class="clip"
        src="{music}"
        data-start="0"
        data-duration="{duration}"
        data-track-index="11"
        data-volume="{music_meta.get("gain_under_speech", audio_defaults["music_gain_under_speech"])}"
        data-fade-in="0.2"
        data-fade-out="0.3"
        data-automation='{automation}'
      ></audio>"""
    credit = _credit(plan, duration)
    has_logo = any(item["action"] == "logo" for item in plan["decisions"])
    corner = (plan.get("render") or {}).get("corner_logo") or ""
    frame = reference_frame(width, height) if corner else None
    corner_css = ""
    corner_html = ""
    if frame:
        corner_css = f"""
      #corner-logo {{ position: absolute; top: {frame["logo_top"]}px; left: {frame["logo_left"]}px; width: {frame["logo_size"]}px; height: {frame["logo_size"]}px; z-index: 8; object-fit: contain; }}
"""
        corner_html = f"""
      <img id="corner-logo" class="clip" src="{_esc(corner)}" alt="" data-start="0" data-duration="{duration}" data-track-index="2" data-layout-ignore="true" />"""
    cross_css = ""
    if any(item.get("graphic") == "negation_cross" for item in plan["decisions"]):
        cross_css = """
      .negation-cross { position: absolute; left: 18%; top: 30%; width: 64%; height: 40%; z-index: 4; pointer-events: none; background: linear-gradient(#d01212,#d01212) center/14% 100% no-repeat, linear-gradient(#d01212,#d01212) center/100% 14% no-repeat; transform: rotate(45deg); }
"""
    visual_family = "bounded_card_with_title" if frame else family
    videos = _video_clips(plan, visual_family, card, frame, asset_root)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Sarthi proof { _esc(plan["plan_id"]) }</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      html, body {{
        margin: 0;
        padding: 0;
        width: {width}px;
        height: {height}px;
        overflow: hidden;
        background: {defaults["canvas"]["background"]};
        font-family: "{card["font_family"]}", sans-serif;
      }}
      #root {{
        position: relative;
        width: {width}px;
        height: {height}px;
      }}
      video {{
        position: absolute;
        object-fit: cover;
      }}
      { _family_css(visual_family, card, width, height, fit, 124 if has_logo else 36, frame, _sharp_box(plan, frame), teacher_filter, broll_filter) }
      {corner_css}
      {cross_css}
    </style>
  </head>
  <body>
    {timeline_tag}
    <div
      id="root"
      data-composition-id="main"
      data-layout-family="{family}"
      data-start="0"
      data-duration="{duration}"
      data-width="{width}"
      data-height="{height}"
    >
      <div id="canvas-bg" data-layout-ignore="true"></div>
      {title}
      {videos}
      {captions}
      {credit}
      {corner_html}
      <audio
        id="narration"
        class="clip"
        src="{narration}"
        data-start="0"
        data-duration="{duration}"
        data-track-index="10"
        data-volume="1"
      ></audio>
      {music_tag}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      {tweens}
      {_broll_fades(plan)}
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _family_css(
    family: str,
    card: dict,
    width: int,
    height: int,
    fit: str = "cover",
    title_left: int = 124,
    frame: dict | None = None,
    sharp_box: str = "",
    teacher_filter: str = "filter: contrast(1.05) saturate(1.04);",
    broll_filter: str = "filter: contrast(1.05) saturate(1.04);",
) -> str:
    if family == "bounded_card_with_title" and frame:
        return f"""
      __HALFTONE__
      #card {{
        position: absolute; top: {frame["card_top"]}px; left: {frame["card_left"]}px;
        width: {frame["card_width"]}px; height: {frame["card_height"]}px;
        border-radius: {frame["radius_px"]}px; overflow: hidden; z-index: 2; background: transparent;
      }}
      #card video.teacher.landscape {{
        object-fit: contain; object-position: center center; z-index: 2; background: transparent;
        {teacher_filter}
      }}
      #card video.teacher.portrait {{
        left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; object-position: center 38%; z-index: 2;
        {teacher_filter}
      }}
      #title-card {{ position: absolute; inset: 0; z-index: 9; }}
      #card video.broll {{
        left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; object-position: center center; z-index: 3;
        background: transparent;
        {broll_filter}
      }}
      #credit {{ position: absolute; left: 36px; right: 36px; bottom: 10px; z-index: 7; text-align: center; }}
      #credit span {{ color: #f2f2f2; font-size: 14px; line-height: 1.2; }}
      .caption {{
        position: absolute; left: 48px; width: {width - 96}px; bottom: {frame["caption_bottom"]}px; z-index: 6;
        text-align: center;
      }}
      .caption span {{
        display: inline-block; color: #ffffff; background: #6d2430;
        border-radius: 8px; padding: 6px 14px; font-size: 26px; font-weight: 700;
      }}
    """.replace("__HALFTONE__", halftone_background_css(width, height))
    if family == "bounded_card_with_title":
        return f"""
      __HALFTONE__
      #logo-mark {{ position: absolute; top: 48px; left: 36px; width: 72px; height: 72px; z-index: 6; }}
      #title-block {{ position: absolute; top: 56px; left: {title_left}px; right: 36px; z-index: 5; }}
      #title-block h1 {{ margin: 0; color: {card["title_color"]}; font-size: 28px; line-height: 1.2; font-weight: 700; }}
      #card {{
        position: absolute; top: 168px; left: 36px; width: {width - 72}px; height: 900px;
        border-radius: {card["radius_px"]}px; overflow: hidden; z-index: 2; background: #000;
      }}
      #card video.teacher {{ width: 100%; height: 100%; left: 0; top: 0; object-fit: {fit}; }}
      #card video.broll, #card img {{ width: 100%; height: 100%; left: 0; top: 0; object-fit: contain; background: #000; }}
      #credit {{ position: absolute; left: 36px; right: 36px; bottom: 28px; z-index: 7; text-align: center; }}
      #credit span {{ color: #f2f2f2; font-size: 20px; line-height: 1.3; }}
      .caption {{
        position: absolute; left: 72px; width: {width - 144}px; bottom: 250px; z-index: 6;
        text-align: center;
      }}
      .caption span {{
        display: inline-block; color: {card["caption_color"]}; background: {card["caption_chip"]};
        border-radius: 12px; padding: 10px 16px; font-size: 28px; font-weight: 700;
      }}
    """.replace("__HALFTONE__", halftone_background_css(width, height))
    if family == "full_bleed_board_or_lecture":
        return f"""
      #canvas-bg {{ position: absolute; inset: 0; background: #111; z-index: 0; }}
      #title-block {{ position: absolute; top: 36px; left: 32px; right: 32px; z-index: 5; }}
      #title-block h1 {{ margin: 0; color: #fff; font-size: 32px; }}
      #stage video.teacher {{ position: absolute; inset: 0; width: {width}px; height: {height}px; z-index: 1; object-fit: {fit}; }}
      #stage video.broll, #stage img {{ position: absolute; inset: 0; width: {width}px; height: {height}px; z-index: 1; object-fit: contain; background: #000; }}
      #credit {{ position: absolute; left: 36px; right: 36px; bottom: 28px; z-index: 7; text-align: center; }}
      #credit span {{ color: #f2f2f2; font-size: 20px; line-height: 1.3; }}
      .caption {{ position: absolute; left: 48px; width: {width - 96}px; bottom: 180px; z-index: 6; text-align: center; }}
      .caption span {{ display: inline-block; color: #fff; background: rgba(0,0,0,0.55); padding: 8px 14px; font-size: 30px; font-weight: 700; }}
    """
    if family == "split_teacher_and_graphic":
        return f"""
      #canvas-bg {{ position: absolute; inset: 0; background: #0e0e0e; z-index: 0; }}
      #title-block {{ position: absolute; top: 28px; left: 24px; right: 24px; z-index: 5; }}
      #title-block h1 {{ margin: 0; color: #fff; font-size: 26px; }}
      #stage {{ position: absolute; top: 110px; left: 0; right: 0; height: 1000px; z-index: 2; }}
      .pane-teacher {{ position: absolute; left: 0; top: 0; width: 50%; height: 100%; overflow: hidden; }}
      .pane-support {{ position: absolute; right: 0; top: 0; width: 50%; height: 100%; overflow: hidden; }}
      #stage video {{ width: 100%; height: 100%; left: 0; top: 0; }}
      .caption {{ position: absolute; left: 24px; width: {width - 48}px; bottom: 80px; z-index: 6; text-align: center; }}
      .caption span {{ display: inline-block; color: #111; background: #fff; padding: 8px 12px; font-size: 24px; font-weight: 700; }}
    """
    return f"""
      #canvas-bg {{ position: absolute; inset: 0; background: #151515; z-index: 0; }}
      #title-block {{ position: absolute; top: 24px; left: 24px; right: 24px; z-index: 5; }}
      #title-block h1 {{ margin: 0; color: #d7ffd0; font-size: 22px; font-family: "IBM Plex Mono", monospace; }}
      #stage video {{ position: absolute; inset: 24px; width: {width - 48}px; height: {height - 48}px; z-index: 1; border: 1px solid #333; }}
      .caption {{ position: absolute; left: 40px; width: {width - 80}px; bottom: 64px; z-index: 6; }}
      .caption span {{ color: #d7ffd0; font-family: "IBM Plex Mono", monospace; font-size: 22px; }}
    """


def _video_clips(
    plan: dict,
    family: str,
    card: dict,
    frame: dict | None = None,
    asset_root: Path | None = None,
) -> str:
    teacher = plan["input_media"]["video_path"]
    shown = [
        decision
        for decision in plan["decisions"]
        if decision["action"] in {"show_teacher", "replace_with_broll", "overlay_broll"}
    ]
    clips = []
    for index, decision in enumerate(shown):
        src = teacher if decision["action"] == "show_teacher" else _asset_src(plan, decision)
        media_start = decision["start"] if decision["action"] == "show_teacher" else 0
        hold = 0.0
        nxt = shown[index + 1] if index + 1 < len(shown) else None
        if decision["action"] == "show_teacher" and nxt and nxt["action"] == "replace_with_broll":
            hold = look_from_plan(plan)["fade_seconds"]
        if frame and decision["action"] == "show_teacher":
            source_w = int(plan["input_media"].get("width") or 0)
            source_h = int(plan["input_media"].get("height") or 0)
            portrait = source_h > source_w
            role = "teacher portrait" if portrait else "teacher landscape"
            fit = (
                "left:0;top:0;width:100%;height:100%;object-fit:cover;object-position:center 38%;"
                if portrait
                else _fitted_box(frame, source_w or 1280, source_h or 720) + "z-index:2;background:transparent;border-radius:22px;"
            )
            clips.append(_media_tag(decision, src, media_start, role=role, style=fit, duration_extra=hold))
            continue
        if frame:
            clips.append(
                _media_tag(
                    decision,
                    src,
                    media_start,
                    role="broll",
                    style="left:0;top:0;width:100%;height:100%;object-fit:cover;object-position:center center;",
                )
            )
            if decision.get("graphic") == "negation_cross":
                clips.append(_negation_cross(decision))
            continue
        clips.append(_media_tag(decision, src, media_start, duration_extra=hold))
    body = "\n".join(clips)
    if family == "bounded_card_with_title":
        return f'<div id="card" data-layout-allow-overflow="true">{body}</div>'
    if family == "split_teacher_and_graphic":
        teacher_clips = []
        support_clips = []
        for decision in plan["decisions"]:
            if decision["action"] == "show_teacher":
                teacher_clips.append(_split_video(decision, teacher, decision["start"]))
            elif decision["action"] in {"replace_with_broll", "overlay_broll"}:
                support_clips.append(_split_video(decision, _asset_src(plan, decision), 0))
        return (
            '<div id="stage">'
            f'<div class="pane-teacher">{"".join(teacher_clips)}</div>'
            f'<div class="pane-support">{"".join(support_clips)}</div>'
            "</div>"
        )
    return f'<div id="stage">{body}</div>'


def _negation_cross(decision: dict) -> str:
    duration = round(_dur(decision), 3)
    return (
        f'<div id="{_esc(decision["id"])}-cross" class="negation-cross clip" '
        f'data-start="{decision["start"]}" data-duration="{duration}" data-track-index="3"></div>'
    )


def _split_video(decision: dict, src: str, media_start: float) -> str:
    return _media_tag(decision, src, media_start)


def _media_tag(
    decision: dict,
    src: str,
    media_start: float,
    role: str | None = None,
    style: str = "",
    duration_extra: float = 0.0,
) -> str:
    if role is None:
        role = "teacher" if decision["action"] == "show_teacher" else "broll"
    tag_id = decision["id"] if role != "backdrop" else f'{decision["id"]}-blur'
    painted = f' style="{style}"' if style else ""
    duration = round(_dur(decision) + duration_extra, 3)
    common = (
        f'id="{_esc(tag_id)}" class="clip {role}" src="{src}"{painted} '
        f'data-start="{decision["start"]}" data-duration="{duration}" data-track-index="1"'
    )
    if _is_image(decision, src):
        return f"<img {common} />"
    return (
        f'<video {common} muted playsinline data-media-start="{media_start}"></video>'
    )


def _is_image(decision: dict, src: str) -> bool:
    if decision.get("asset_kind") == "image":
        return True
    return src.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"))


def _credit(plan: dict, duration: float) -> str:
    """Licensing stays on the plan. It is not drawn on the lesson or the outro."""
    return ""


def _title(plan: dict, family: str, card: dict) -> str:
    title = next(item for item in plan["decisions"] if item["action"] == "title")
    logo = next((item for item in plan["decisions"] if item["action"] == "logo"), None)
    logo_html = ""
    if logo and family == "bounded_card_with_title":
        logo_html = f"""<img id="logo-mark" class="clip" src="assets/logo.svg" alt="" data-layout-ignore="true"
          data-start="{logo["start"]}" data-duration="{_dur(logo)}" data-track-index="2" />"""
    return f"""
      {logo_html}
      <div
        id="title-card"
        data-composition-id="title-card"
        data-composition-src="compositions/title-card.html"
        data-start="{title["start"]}"
        data-duration="{_dur(title)}"
        data-width="{plan["render"]["width"]}"
        data-height="{plan["render"]["height"]}"
        data-track-index="3"
        data-track-kind="graphics"
      ></div>
    """


def render_title_scene(plan: dict, defaults: dict) -> str:
    title = next(item for item in plan["decisions"] if item["action"] == "title")
    card = defaults["bounded_card"]
    width = plan["render"]["width"]
    height = plan["render"]["height"]
    family = plan["style"]["layout_family"]
    if family == "screen_recording_or_product_ui":
        font = "IBM Plex Mono"
        color = "#d7ffd0"
        top = 24
        left = 24
        size = 22
    elif family == "bounded_card_with_title":
        font = card["font_family"]
        color = card["title_color"]
        top = 56
        left = 124 if any(item["action"] == "logo" for item in plan["decisions"]) else 36
        size = 28
    else:
        font = card["font_family"]
        color = "#ffffff"
        top = 36
        left = 32
        size = 32
    duration = _dur(title)
    corner = bool((plan.get("render") or {}).get("corner_logo"))
    frame = reference_frame(width, height) if corner else None
    align = "left"
    if frame:
        fitted = fit_title(title.get("text") or "", frame, width)
        top = fitted["top"]
        left = frame["title_left"]
        right = frame["title_right"]
        size = fitted["font_size"]
        align = fitted["align"]
        font = "Arial"
        color = "#ffffff"
        title_html = _title_html(fitted["text"])
    else:
        right = 36
        title_html = _title_html(title.get("text") or "")
    accent = frame["accent"] if frame else "#f5c400"
    return f"""<template>
  <style>
    @font-face {{
      font-family: "Sarthi Condensed";
      src: local("Arial Narrow"), local("Arial");
      font-weight: 700;
      font-style: normal;
    }}
    #root {{ position: absolute; inset: 0; }}
    #title-text {{
      margin: 0;
      position: absolute;
      top: {top}px;
      left: {left}px;
      right: {right}px;
      color: {color};
      font-family: "{font}", Arial, sans-serif;
      font-size: {size}px;
      line-height: 0.98;
      font-weight: 800;
      letter-spacing: 0;
      {f"text-align: {align};" if frame else ""}
      {"text-transform: uppercase;" if frame else ""}
      {"white-space: pre-line;" if frame else ""}
    }}
    #title-text .accent {{ color: {accent}; }}
  </style>
  <div id="root" data-composition-id="title-card" data-width="{width}" data-height="{height}">
    <h1 id="title-text" class="clip" data-start="0" data-duration="{duration}" data-track-index="0">{title_html}</h1>
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
    tl.fromTo("#title-text", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2, ease: "power2.out" }}, 0.1);
    window.__timelines = window.__timelines || {{}};
    window.__timelines["title-card"] = tl;
  </script>
</template>
"""


def _captions(plan: dict, family: str, card: dict, height: int) -> str:
    blocks = []
    for decision in plan["decisions"]:
        if decision["action"] != "caption":
            continue
        visible = (decision.get("text") or "").upper()
        blocks.append(
            f"""<div id="{_esc(decision["id"])}" class="clip caption" data-layout-allow-caption-zone="true"
              data-start="{decision["start"]}" data-duration="{_dur(decision)}" data-track-index="4">
              <span>{_esc(visible)}</span>
            </div>"""
        )
    return f'<div id="captions-host" data-track-kind="captions">{"".join(blocks)}</div>'


def _broll_fades(plan: dict) -> str:
    """Ease each insert in over the teacher. The reference opens with that kind of blend."""
    look = look_from_plan(plan)
    seconds = look["fade_seconds"]
    origin = look["from_opacity"]
    lines = []
    for decision in plan["decisions"]:
        if decision["action"] != "replace_with_broll":
            continue
        lines.append(
            f'tl.fromTo("#{decision["id"]}", {{ opacity: {origin} }}, '
            f'{{ opacity: 1, duration: {seconds}, ease: "power1.out" }}, {decision["start"]});'
        )
    return "\n      ".join(lines)


def _caption_tweens(plan: dict) -> str:
    lines = []
    for decision in plan["decisions"]:
        if decision["action"] != "caption":
            continue
        start = decision["start"]
        end = decision["end"]
        target = f"#{decision['id']} span"
        lines.append(
            f'tl.fromTo("{target}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.12, ease: "power2.out" }}, {start});'
        )
        fade_at = max(start, end - 0.12)
        lines.append(
            f'tl.to("{target}", {{ opacity: 0, duration: 0.12, ease: "power2.in" }}, {fade_at});'
        )
        lines.append(f'tl.set("{target}", {{ opacity: 0, visibility: "hidden" }}, {end});')
    return "\n      ".join(lines)


def _asset_src(plan: dict, decision: dict) -> str:
    if decision["action"] == "show_teacher":
        return plan["input_media"]["video_path"]
    return decision.get("asset_path") or "assets/broll.mp4"


def _landscape_box(frame: dict, width: int, height: int) -> str:
    """Full card width, true aspect, centered. A blurred copy fills the rest of the card."""
    card_w = float(frame["card_width"])
    card_h = float(frame["card_height"])
    fitted_h = card_w * height / width
    fitted_w = card_w
    if fitted_h > card_h:
        fitted_h = card_h
        fitted_w = card_h * width / height
    left = (card_w - fitted_w) / 2
    top = (card_h - fitted_h) / 2
    return (
        f"position:absolute;left:{left:.1f}px;top:{top:.1f}px;width:{fitted_w:.1f}px;"
        f"height:{fitted_h:.1f}px;object-fit:fill;z-index:3;"
    )


def _clip_size(asset_root: Path | None, src: str) -> tuple[int, int] | None:
    if asset_root is None:
        return None
    path = asset_root / src
    if not path.is_file():
        return None
    completed = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _sharp_box(plan: dict, frame: dict | None) -> str:
    """Place the sharp lesson picture. Landscape stays full width so board text is not cropped."""
    if not frame:
        return ""
    source_w = int(plan["input_media"]["width"])
    source_h = int(plan["input_media"]["height"])
    return _fitted_box(frame, source_w, source_h)


def _fitted_height(frame: dict, source_w: int, source_h: int) -> float:
    card_w = float(frame["card_width"])
    card_h = float(frame["card_height"])
    fitted_h = card_w * source_h / source_w
    if fitted_h > card_h:
        return card_h
    return fitted_h


def _fitted_box(frame: dict, source_w: int, source_h: int) -> str:
    """Sharp picture at the source aspect. A blurred copy fills the rest of the card."""
    card_w = float(frame["card_width"])
    card_h = float(frame["card_height"])
    fitted_h = card_w * source_h / source_w
    fitted_w = card_w
    if fitted_h > card_h:
        fitted_h = card_h
        fitted_w = card_h * source_w / source_h
    left = (card_w - fitted_w) / 2
    top = (card_h - fitted_h) / 2
    return (
        f"left: {left:.1f}px; top: {top:.1f}px; width: {fitted_w:.1f}px; "
        f"height: {fitted_h:.1f}px; object-fit: fill;"
    )


def _title_html(text: str) -> str:
    """[[word]] becomes a yellow accent. The rest is escaped."""
    parts = []
    rest = text or ""
    while "[[" in rest and "]]" in rest[rest.index("[[") :]:
        before, _, tail = rest.partition("[[")
        word, marker, rest = tail.partition("]]")
        if not marker:
            rest = before + "[[" + tail
            break
        if before:
            parts.append(_esc(before))
        parts.append(f'<span class="accent">{_esc(word)}</span>')
    if rest:
        parts.append(_esc(rest))
    return "".join(parts)


def _dur(decision: dict) -> float:
    return round(decision["end"] - decision["start"], 3)


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
