"""Model worker. Run with the engine interpreter, which has torch and the cached weights.

    python worker.py request.json response.json

Each section of the request is optional. A model is loaded once per call and the
process exits afterwards, so its memory is returned to Windows. Nothing is
downloaded: Hugging Face runs offline. A section that fails reports its error and
returns no vectors; it never returns invented values.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

CLIP_MODEL = "openai/clip-vit-base-patch32"
EMBED_MODEL = "Snowflake/snowflake-arctic-embed-xs"
BATCH = 8


def _vectors(tensor) -> list[list[float]]:
    tensor = tensor / tensor.norm(dim=-1, keepdim=True)
    return [[round(float(value), 6) for value in row] for row in tensor.tolist()]


def _features(output):
    if hasattr(output, "shape"):
        return output
    for name in ("text_embeds", "image_embeds", "pooler_output"):
        value = getattr(output, name, None)
        if value is not None:
            return value
    raise RuntimeError("CLIP returned no embedding")


def run_clip(section: dict) -> dict:
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    torch.set_num_threads(2)
    started = time.time()
    model = CLIPModel.from_pretrained(CLIP_MODEL)
    processor = CLIPProcessor.from_pretrained(CLIP_MODEL)
    model.eval()
    images_out: list[list[float]] = []
    texts_out: list[list[float]] = []
    with torch.no_grad():
        paths = section.get("images") or []
        for start in range(0, len(paths), BATCH):
            batch = []
            for path in paths[start : start + BATCH]:
                with Image.open(path) as image:
                    batch.append(image.convert("RGB"))
            inputs = processor(images=batch, return_tensors="pt")
            images_out.extend(_vectors(_features(model.get_image_features(**inputs))))
            for image in batch:
                image.close()
        texts = section.get("texts") or []
        for start in range(0, len(texts), 32):
            inputs = processor(text=texts[start : start + 32], return_tensors="pt", padding=True, truncation=True, max_length=77)
            texts_out.extend(_vectors(_features(model.get_text_features(**inputs))))
        scale = float(model.logit_scale.exp().item())
    return {"model": CLIP_MODEL, "image": images_out, "text": texts_out, "logit_scale": scale, "seconds": round(time.time() - started, 2)}


def run_embed(section: dict) -> dict:
    from sentence_transformers import SentenceTransformer

    started = time.time()
    model = SentenceTransformer(EMBED_MODEL, device="cpu")
    texts = section.get("texts") or []
    vectors = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False) if texts else []
    return {"model": EMBED_MODEL, "vectors": [[round(float(value), 6) for value in row] for row in vectors], "seconds": round(time.time() - started, 2)}


def run_gemini(section: dict) -> dict:
    """One structured Gemini call per asset over at most three of its frames."""
    engine_src = section.get("engine_src")
    if engine_src and engine_src not in sys.path:
        sys.path.insert(0, engine_src)
    from pydantic import BaseModel, Field

    from broll_intel.gemini_api import TEXT_MODEL, generate_structured

    class FrameRead(BaseModel):
        index: int
        description: str
        subjects: list[str] = Field(default_factory=list)
        actions: list[str] = Field(default_factory=list)
        confidence: float

    class ClipRead(BaseModel):
        frames: list[FrameRead]
        summary: str

    prompt = (
        "These are frames sampled from one stock video clip, in time order. For each frame, "
        "describe only what is visible. List subjects and actions only when they are clearly "
        "visible. Give a confidence from 0 to 1. Do not guess the location or the story. "
        "Then give a one-sentence summary of what the clip shows."
    )
    results = {}
    for asset in section.get("assets") or []:
        try:
            read = generate_structured(prompt, ClipRead, images=[Path(path) for path in asset["frames"][:3]])
            results[asset["id"]] = {"ok": True, "read": read.model_dump()}
        except Exception as exc:  # noqa: BLE001
            results[asset["id"]] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return {"model": TEXT_MODEL, "assets": results}


def main(argv: list[str]) -> int:
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    response: dict = {"errors": {}}
    for name, runner in (("clip", run_clip), ("embed", run_embed), ("gemini", run_gemini)):
        section = request.get(name)
        if not section:
            continue
        try:
            response[name] = runner(section)
        except Exception as exc:  # noqa: BLE001
            response["errors"][name] = f"{type(exc).__name__}: {str(exc)[:400]}"
    Path(argv[2]).write_text(json.dumps(response), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
