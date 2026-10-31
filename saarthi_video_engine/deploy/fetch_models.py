"""Download the two models the visual worker loads, then load them offline once.

Runs at image build time. The worker itself never downloads: it sets
HF_HUB_OFFLINE=1 and fails over to "no visual analysis" if a model is missing.
"""

from __future__ import annotations

import os
import sys

CLIP_MODEL = "openai/clip-vit-base-patch32"
EMBED_MODEL = "Snowflake/snowflake-arctic-embed-xs"
SKIP = (".h5", ".msgpack", ".onnx", ".ot", ".tflite")
SKIP_DIRS = ("onnx/", "openvino/", "coreml/")


def fetch(repo: str) -> None:
    from huggingface_hub import HfApi, snapshot_download

    files = HfApi().list_repo_files(repo)
    keep = [name for name in files if not name.endswith(SKIP) and not name.startswith(SKIP_DIRS)]
    if "model.safetensors" in keep:
        keep = [name for name in keep if name != "pytorch_model.bin"]
    snapshot_download(repo, allow_patterns=keep)
    print(f"downloaded {repo}: {len(keep)} files")


def verify() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer
    from transformers import CLIPModel, CLIPProcessor

    CLIPProcessor.from_pretrained(CLIP_MODEL)
    CLIPModel.from_pretrained(CLIP_MODEL)
    SentenceTransformer(EMBED_MODEL, device="cpu")
    print("both models load offline")


if __name__ == "__main__":
    if "--verify" not in sys.argv:
        for name in (CLIP_MODEL, EMBED_MODEL):
            fetch(name)
    verify()
