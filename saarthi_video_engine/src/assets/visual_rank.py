"""Frame samples, a black-field check, and CLIP ranking when the weights load.

CLIP is a local model. A caller can pass a scoring function so tests do not
download weights. A clip is not kept on the score alone: the caller still
checks the license, the anchor word, and whether the file was already used.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from render.outro import tool


def sample_frames(
    path: Path,
    count: int = 3,
    destination: Path | None = None,
    start: float | None = None,
    end: float | None = None,
) -> list[Path]:
    """Grab frames inside the span that would actually play, when that span is known."""
    folder = destination or path.parent / (path.stem + "_frames")
    if start is not None:
        folder = folder / f"w{int(float(start) * 10)}"
    folder.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    duration = 4.0
    if probe.returncode == 0:
        import json

        duration = float((json.loads(probe.stdout).get("format") or {}).get("duration") or 4.0)
    if start is not None and end is not None and float(end) > float(start):
        span_start = max(0.0, float(start))
        span_end = min(duration, float(end))
        width = max(0.3, span_end - span_start)
        stamps = [span_start + width * (index + 1) / (count + 1) for index in range(count)]
    else:
        stamps = [max(0.2, duration * (index + 1) / (count + 1)) for index in range(count)]
    saved: list[Path] = []
    for index, stamp in enumerate(stamps):
        dest = folder / f"f{index}.jpg"
        if not dest.is_file():
            subprocess.run(
                [tool("ffmpeg"), "-y", "-ss", f"{stamp:.2f}", "-i", str(path), "-frames:v", "1", "-update", "1", str(dest)],
                capture_output=True,
                check=False,
            )
        if dest.is_file():
            saved.append(dest)
    return saved


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def black_fraction(frame: np.ndarray, threshold: int = 24) -> float:
    """Share of pixels that are near black. A person cut out on black scores high."""
    if frame.size == 0:
        return 1.0
    dark = frame.max(axis=2) < threshold
    return float(dark.mean())


def content_box(frame: np.ndarray, threshold: int = 24) -> tuple[int, int, int, int] | None:
    """Bounding box of pixels that are not near black. None when the frame is empty."""
    lit = frame.max(axis=2) >= threshold
    ys, xs = np.where(lit)
    if len(xs) < 50:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def framing_decision(frame: np.ndarray) -> str:
    """reject when the subject is isolated on black. crop when a modest matte remains. else keep."""
    fraction = black_fraction(frame)
    if fraction >= 0.42:
        return "reject"
    box = content_box(frame)
    if box is None:
        return "reject"
    height, width = frame.shape[:2]
    area = ((box[2] - box[0]) * (box[3] - box[1])) / float(width * height)
    if fraction >= 0.18 and area < 0.72:
        return "crop"
    return "keep"


def crop_video(source: Path, dest: Path, box: tuple[int, int, int, int], width: int, height: int) -> bool:
    """Cut the dark matte off a clip. The picture itself is not scaled down."""
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - int(width * 0.03))
    y0 = max(0, y0 - int(height * 0.03))
    x1 = min(width, x1 + int(width * 0.03))
    y1 = min(height, y1 + int(height * 0.03))
    crop_w = max(2, (x1 - x0) // 2 * 2)
    crop_h = max(2, (y1 - y0) // 2 * 2)
    if crop_w < 320 or crop_h < 320:
        return False
    if crop_w > width * 0.92 and crop_h > height * 0.92:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            tool("ffmpeg"), "-y", "-i", str(source),
            "-vf", f"crop={crop_w}:{crop_h}:{x0}:{y0}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an",
            str(dest),
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 50_000


def average_hash(frame: np.ndarray) -> str:
    """64-bit average hash as 16 hex characters, for near-duplicate checks."""
    grey = Image.fromarray(frame).convert("L").resize((8, 8), Image.Resampling.BOX)
    pixels = np.asarray(grey, dtype=np.float32).reshape(-1)
    bits = pixels > pixels.mean()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


class ClipRanker:
    """Lazy local CLIP. score() returns a cosine similarity in about [-1, 1]."""

    def __init__(self, scorer=None):
        self._scorer = scorer
        self._model = None
        self._processor = None
        self.loaded = scorer is not None
        self.error = ""

    def score(self, image: Image.Image, prompt: str) -> float:
        if self._scorer is not None:
            return float(self._scorer(image, prompt))
        self._load()
        import torch

        inputs = self._processor(text=[prompt], images=image, return_tensors="pt", padding=True)
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}
        with torch.no_grad():
            output = self._model(**inputs)
        image_features = output.image_embeds
        text_features = output.text_embeds
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        return float((image_features * text_features).sum().item())

    def score_frames(self, frames: list[Path], prompt: str) -> float:
        if not frames:
            return -1.0
        values = [self.score(Image.open(path).convert("RGB"), prompt) for path in frames]
        return float(sum(values) / len(values))

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import os

            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            os.environ.setdefault("MKL_NUM_THREADS", "1")
            import torch
            from transformers import CLIPModel, CLIPProcessor

            torch.set_num_threads(1)
            self._processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            self._model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            self._model.eval()
            self.loaded = True
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
            raise
