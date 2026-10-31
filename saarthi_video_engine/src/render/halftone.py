"""Static dotted background measured from the NEET-to-UPSC Short.

Frames at 0.4 s and 16.4 s of that file correlate 0.94 on the top margin
with no shift, so the dots are not travelling. A frame-to-frame difference
is compression, not a sparkle cycle. This layer does not animate.
"""

from __future__ import annotations

import urllib.parse


def halftone_svg(width: int = 720, height: int = 1280) -> str:
    """Black field with grey dots that grow toward the bottom, matching the reference margins."""
    circles: list[str] = []
    step_x = 28
    step_y = 26
    row = 0
    y = 16
    while y < height:
        x = 12 + (step_x // 2 if row % 2 else 0)
        while x < width:
            depth = y / height
            radius = 2.1 + 6.2 * (depth ** 1.25)
            grey = int(26 + 28 * depth)
            circles.append(
                f'<circle cx="{x}" cy="{y}" r="{radius:.1f}" fill="rgb({grey},{grey},{grey})"/>'
            )
            x += step_x
        y += step_y
        row += 1
    body = "".join(circles)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="100%" height="100%" fill="rgb(0,0,0)"/>{body}</svg>'
    )


def halftone_background_css(width: int = 720, height: int = 1280) -> str:
    """One CSS rule. No stroke, no animation, no per-frame raster."""
    encoded = urllib.parse.quote(halftone_svg(width, height))
    return (
        "#canvas-bg { position: absolute; inset: 0; z-index: 0; "
        f'background: rgb(0,0,0) url("data:image/svg+xml,{encoded}") center / {width}px {height}px no-repeat; }}'
    )


def pattern_is_animated() -> bool:
    return False


def coverage_bands(width: int = 720, height: int = 1280) -> dict[str, bool]:
    """True when the one canvas pattern paints dots in each vertical third."""
    import re

    found = {"top": False, "middle": False, "bottom": False}
    for raw in re.findall(r'cy="([\d.]+)"', halftone_svg(width, height)):
        y = float(raw)
        if y < height / 3:
            found["top"] = True
        elif y < (2 * height) / 3:
            found["middle"] = True
        else:
            found["bottom"] = True
    return found
