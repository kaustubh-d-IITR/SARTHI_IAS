"""Visual-analysis providers, the model worker, and the per-hash cache.

A provider says whether it really looked at frames. CLIP output is a ranking of
fixed vocabulary labels; it is reported as such and is not presented as a caption.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.config import ENGINE_ROOT
from app.services.visual.sampling import SAMPLER_VERSION, sample_clip

DEFAULT_ENGINE_PYTHON = Path(r"C:\Users\shubh\AppData\Local\Programs\Python\Python313\python.exe")
WORKER = Path(__file__).resolve().parent / "worker.py"
LABEL_FLOOR = 0.12

# Label, and whether it names an action. Phrased for CLIP ("a photo of ...").
VOCABULARY: list[tuple[str, bool]] = [
    ("a health worker wearing protective gear", False),
    ("a person taking off protective clothing", True),
    ("a doctor or nurse in a hospital", False),
    ("a hospital ward", False),
    ("a face mask", False),
    ("a person writing with a pen", True),
    ("a pen on paper", False),
    ("an open book", False),
    ("a newspaper", False),
    ("a student studying at a desk", True),
    ("a classroom with students", False),
    ("a teacher at a whiteboard", False),
    ("a person speaking to the camera", True),
    ("a person using a smartphone", True),
    ("a computer screen", False),
    ("a person typing on a laptop", True),
    ("a parliament or government building", False),
    ("a courtroom", False),
    ("a crowd of people", False),
    ("people walking on a street", True),
    ("a city skyline", False),
    ("a village", False),
    ("farmers working in a field", True),
    ("a factory", False),
    ("a map", False),
    ("a chart or graph", False),
    ("a national flag", False),
    ("soldiers or police", False),
    ("mountains", False),
    ("a river", False),
    ("the sea or a beach", False),
    ("a forest", False),
    ("rain or a storm", False),
    ("a desert", False),
    ("traffic on a road", False),
    ("a train", False),
    ("an aircraft", False),
    ("money or coins", False),
    ("a market with shops", False),
    ("a laboratory", False),
    ("the earth from space", False),
    ("solar panels or wind turbines", False),
    ("animals in the wild", False),
    ("food on a plate", False),
    ("a clock", False),
]
IS_ACTION = dict(VOCABULARY)
DISTRACTOR_CAPTIONS = [
    "a photo",
    "a random object",
    "a blank screen",
    "a person standing indoors",
    "an everyday scene",
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def engine_python() -> Path | None:
    configured = (os.environ.get("STUDIO_ENGINE_PYTHON") or "").strip()
    path = Path(configured) if configured else DEFAULT_ENGINE_PYTHON
    return path if path.is_file() else None


class WorkerRunner:
    """Runs worker.py in a separate process. Tests pass a callable instead."""

    def __init__(self, python: Path | None = None, timeout: int = 900) -> None:
        self.python = python or engine_python()
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        if self.python is None:
            return False, "The engine Python interpreter was not found. Set STUDIO_ENGINE_PYTHON."
        return True, ""

    def __call__(self, request: dict) -> dict:
        ok, reason = self.available()
        if not ok:
            return {"errors": {"worker": reason}}
        with tempfile.TemporaryDirectory(prefix="studio_worker_") as folder:
            req = Path(folder) / "request.json"
            out = Path(folder) / "response.json"
            req.write_text(json.dumps(request), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [str(self.python), str(WORKER), str(req), str(out)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"errors": {"worker": f"The model worker did not finish within {self.timeout} s."}}
            if completed.returncode != 0 or not out.is_file():
                tail = (completed.stderr or "").strip().splitlines()[-1:] or ["no output"]
                return {"errors": {"worker": f"The model worker exited with {completed.returncode}: {tail[0][:300]}"}}
            return json.loads(out.read_text(encoding="utf-8"))


@dataclass
class AssetInput:
    asset_id: str
    sha256: str
    path: Path
    duration: float
    description: str
    filename: str


@dataclass
class AnalysisBatch:
    """What one processing run learned. Vectors are kept in memory for matching only."""

    provider: str
    model: str
    analyses: dict[str, dict] = field(default_factory=dict)
    frame_vectors: dict[str, list[tuple[float, np.ndarray]]] = field(default_factory=dict)
    clip_text: dict[str, np.ndarray] = field(default_factory=dict)
    embed_text: dict[str, np.ndarray] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def _cache_dir(root: Path, sha256: str) -> Path:
    return root / sha256


def _sampling(root: Path, asset: AssetInput) -> dict:
    folder = _cache_dir(root, asset.sha256)
    record_path = folder / "sampling.json"
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("sampler_version") == SAMPLER_VERSION and all(
            (folder / "frames" / item["frame"]).is_file() for item in record.get("frames") or [] if item.get("kept")
        ):
            return record
    folder.mkdir(parents=True, exist_ok=True)
    return sample_clip(asset.path, asset.duration, folder)


def kept_frames(root: Path, sha256: str, sampling: dict) -> list[tuple[float, Path]]:
    folder = _cache_dir(root, sha256) / "frames"
    return [(float(item["timestamp"]), folder / item["frame"]) for item in sampling.get("frames") or [] if item.get("kept")]


def thumbnail_path(root: Path, sha256: str) -> Path | None:
    path = _cache_dir(root, sha256) / "thumb.jpg"
    return path if path.is_file() else None


class VisualAnalysisProvider:
    name = "description_only"
    model = ""
    performs_analysis = False

    def __init__(self, worker=None) -> None:
        self.worker = worker

    def availability(self) -> tuple[bool, str]:
        return False, "Visual analysis is switched off."

    def cache_name(self) -> str:
        return f"{self.name}.json"

    def cached(self, root: Path, asset: AssetInput) -> dict | None:
        path = _cache_dir(root, asset.sha256) / self.cache_name()
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("performed") and record.get("sha256") == asset.sha256 and record.get("sampler_version") == SAMPLER_VERSION and record.get("model") == self.model:
            return record
        return None

    def store(self, root: Path, asset: AssetInput, record: dict) -> None:
        folder = _cache_dir(root, asset.sha256)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / self.cache_name()).write_text(json.dumps(record, indent=2), encoding="utf-8")

    def analyse(self, root: Path, assets: list[AssetInput], clip_texts: list[str], embed_texts: list[str]) -> AnalysisBatch:
        """No frames are examined. Text embeddings are still produced when the worker exists."""
        batch = AnalysisBatch(provider=self.name, model=self.model)
        for asset in assets:
            sampling = _sampling(root, asset)
            batch.analyses[asset.asset_id] = _not_performed(self, asset, sampling, "Visual analysis is switched off for this run.")
        if self.worker is not None and embed_texts:
            response = self.worker({"embed": {"texts": embed_texts}})
            _take_embed(batch, response, embed_texts)
        return batch


def _not_performed(provider: VisualAnalysisProvider, asset: AssetInput, sampling: dict | None, reason: str) -> dict:
    return {
        "asset_id": asset.asset_id,
        "sha256": asset.sha256,
        "performed": False,
        "provider": provider.name,
        "model": provider.model,
        "sampler_version": SAMPLER_VERSION,
        "analysed_at": _now(),
        "reason": reason,
        "frames": [],
        "summary": "",
        "subjects": [],
        "actions": [],
        "sampling": {"kept": (sampling or {}).get("kept", 0), "frames": (sampling or {}).get("frames", [])},
        "thumbnail": bool((sampling or {}).get("thumbnail")),
    }


def _take_embed(batch: AnalysisBatch, response: dict, texts: list[str]) -> None:
    errors = response.get("errors") or {}
    if "embed" in errors or "worker" in errors:
        batch.errors["embed"] = errors.get("embed") or errors.get("worker") or "Text embedding failed."
        return
    section = response.get("embed") or {}
    vectors = section.get("vectors") or []
    if len(vectors) != len(texts):
        batch.errors["embed"] = "The text embedder returned the wrong number of vectors."
        return
    for text, vector in zip(texts, vectors):
        batch.embed_text[text] = np.asarray(vector, dtype=np.float32)
    batch.errors.pop("embed", None)


class DescriptionOnlyProvider(VisualAnalysisProvider):
    pass


class CLIPVisualAnalysisProvider(VisualAnalysisProvider):
    name = "clip"
    model = "openai/clip-vit-base-patch32"
    performs_analysis = True

    def availability(self) -> tuple[bool, str]:
        if self.worker is None:
            return False, "No model worker is configured."
        available = getattr(self.worker, "available", None)
        return available() if callable(available) else (True, "")

    def analyse(self, root: Path, assets: list[AssetInput], clip_texts: list[str], embed_texts: list[str]) -> AnalysisBatch:
        batch = AnalysisBatch(provider=self.name, model=self.model)
        vocabulary = [label for label, _ in VOCABULARY]
        texts = list(dict.fromkeys(clip_texts + vocabulary + DISTRACTOR_CAPTIONS))
        images: list[str] = []
        owners: list[tuple[str, float]] = []
        cached: dict[str, dict] = {}
        samplings: dict[str, dict] = {}
        for asset in assets:
            sampling = _sampling(root, asset)
            samplings[asset.asset_id] = sampling
            hit = self.cached(root, asset)
            if hit is not None and hit.get("image_vectors"):
                cached[asset.asset_id] = hit
                continue
            for stamp, path in kept_frames(root, asset.sha256, sampling):
                images.append(str(path))
                owners.append((asset.asset_id, stamp))
        request: dict = {"clip": {"images": images, "texts": texts}}
        if embed_texts:
            request["embed"] = {"texts": embed_texts}
        response = self.worker(request)
        errors = response.get("errors") or {}
        if embed_texts:
            _take_embed(batch, response, embed_texts)
        clip_error = errors.get("clip") or errors.get("worker")
        section = response.get("clip") or {}
        text_vectors = section.get("text") or []
        image_vectors = section.get("image") or []
        if clip_error or len(text_vectors) != len(texts) or len(image_vectors) != len(images):
            reason = clip_error or "CLIP returned an incomplete result."
            batch.errors["clip"] = reason
            for asset in assets:
                record = _not_performed(self, asset, samplings[asset.asset_id], f"CLIP analysis failed: {reason}")
                self.store(root, asset, record)
                batch.analyses[asset.asset_id] = record
            return batch
        for text, vector in zip(texts, text_vectors):
            batch.clip_text[text] = np.asarray(vector, dtype=np.float32)
        scale = float(section.get("logit_scale") or 100.0)
        label_matrix = np.stack([batch.clip_text[label] for label in vocabulary])
        fresh: dict[str, list[tuple[float, list[float]]]] = {}
        for (asset_id, stamp), vector in zip(owners, image_vectors):
            fresh.setdefault(asset_id, []).append((stamp, vector))
        for asset in assets:
            if asset.asset_id in cached:
                record = cached[asset.asset_id]
                pairs = [(float(item["timestamp"]), item["vector"]) for item in record["image_vectors"]]
            else:
                pairs = fresh.get(asset.asset_id) or []
            if not pairs:
                record = _not_performed(self, asset, samplings[asset.asset_id], "No usable frame was found: every sampled frame was black, blank, or unreadable.")
                self.store(root, asset, record)
                batch.analyses[asset.asset_id] = record
                continue
            vectors = [(stamp, np.asarray(vector, dtype=np.float32)) for stamp, vector in pairs]
            batch.frame_vectors[asset.asset_id] = vectors
            if asset.asset_id in cached:
                record = dict(cached[asset.asset_id])
                record["reused_from_cache"] = True
            else:
                record = self._record(asset, samplings[asset.asset_id], vectors, label_matrix, scale)
                self.store(root, asset, record)
            batch.analyses[asset.asset_id] = record
        return batch

    def _record(self, asset: AssetInput, sampling: dict, vectors, label_matrix: np.ndarray, scale: float) -> dict:
        frames = []
        totals = np.zeros(len(VOCABULARY), dtype=np.float64)
        for stamp, vector in vectors:
            logits = scale * (label_matrix @ vector)
            probs = np.exp(logits - logits.max())
            probs = probs / probs.sum()
            totals += probs
            order = np.argsort(-probs)[:3]
            labels = [{"label": VOCABULARY[i][0], "probability": round(float(probs[i]), 3)} for i in order]
            frame_name = next((item["frame"] for item in sampling.get("frames") or [] if item.get("kept") and abs(float(item["timestamp"]) - stamp) < 1e-6), "")
            frames.append(
                {
                    "timestamp": stamp,
                    "frame": frame_name,
                    "labels": labels,
                    "subjects": [row["label"] for row in labels if row["probability"] >= LABEL_FLOOR and not IS_ACTION[row["label"]]],
                    "actions": [row["label"] for row in labels if row["probability"] >= LABEL_FLOOR and IS_ACTION[row["label"]]],
                    "confidence": labels[0]["probability"],
                    "description": "Closest labels: " + ", ".join(f"{row['label']} ({row['probability']:.2f})" for row in labels),
                }
            )
        mean = totals / max(1, len(vectors))
        order = [int(i) for i in np.argsort(-mean) if mean[i] >= LABEL_FLOOR][:3]
        subjects = [VOCABULARY[i][0] for i in order if not VOCABULARY[i][1]]
        actions = [VOCABULARY[i][0] for i in order if VOCABULARY[i][1]]
        summary = (
            "CLIP ranks these fixed labels highest across the sampled frames: "
            + ", ".join(f"{VOCABULARY[i][0]} ({mean[i]:.2f})" for i in order)
            if order
            else "No label in the fixed CLIP list reached a clear score for these frames."
        )
        return {
            "asset_id": asset.asset_id,
            "sha256": asset.sha256,
            "performed": True,
            "provider": self.name,
            "model": self.model,
            "sampler_version": SAMPLER_VERSION,
            "analysed_at": _now(),
            "reason": "",
            "method": "CLIP image embeddings of sampled frames; labels are a ranking over a fixed vocabulary, not a caption.",
            "frames": frames,
            "summary": summary,
            "subjects": subjects,
            "actions": actions,
            "confidence": round(float(mean[order[0]]), 3) if order else 0.0,
            "sampling": {"kept": sampling.get("kept", 0), "frames": sampling.get("frames", [])},
            "thumbnail": bool(sampling.get("thumbnail")),
            "image_vectors": [{"timestamp": stamp, "vector": [round(float(x), 6) for x in vector]} for stamp, vector in vectors],
        }


class GeminiVisualAnalysisProvider(VisualAnalysisProvider):
    """Opt-in. Each uncached asset costs one Gemini call over up to three frames."""

    name = "gemini"
    model = "gemini (see analysis record)"
    performs_analysis = True

    def availability(self) -> tuple[bool, str]:
        if self.worker is None:
            return False, "No model worker is configured."
        available = getattr(self.worker, "available", None)
        return available() if callable(available) else (True, "")

    def analyse(self, root: Path, assets: list[AssetInput], clip_texts: list[str], embed_texts: list[str]) -> AnalysisBatch:
        batch = AnalysisBatch(provider=self.name, model=self.model)
        samplings = {asset.asset_id: _sampling(root, asset) for asset in assets}
        pending = []
        for asset in assets:
            hit = self.cached(root, asset)
            if hit is not None:
                batch.analyses[asset.asset_id] = dict(hit, reused_from_cache=True)
                continue
            frames = [str(path) for _, path in kept_frames(root, asset.sha256, samplings[asset.asset_id])]
            if not frames:
                record = _not_performed(self, asset, samplings[asset.asset_id], "No usable frame was found.")
                self.store(root, asset, record)
                batch.analyses[asset.asset_id] = record
                continue
            pending.append({"id": asset.asset_id, "frames": _spread(frames, 3)})
        if pending:
            response = self.worker({"gemini": {"assets": pending, "engine_src": str(ENGINE_ROOT / "src")}})
            error = (response.get("errors") or {}).get("gemini") or (response.get("errors") or {}).get("worker")
            results = ((response.get("gemini") or {}).get("assets")) or {}
            model = (response.get("gemini") or {}).get("model") or "gemini"
            by_id = {asset.asset_id: asset for asset in assets}
            for item in pending:
                asset = by_id[item["id"]]
                result = results.get(item["id"]) or {}
                if error or not result.get("ok"):
                    reason = error or result.get("error") or "Gemini returned no result."
                    record = _not_performed(self, asset, samplings[asset.asset_id], f"Gemini analysis failed: {reason}")
                    batch.errors.setdefault("gemini", reason)
                else:
                    record = self._record(asset, samplings[asset.asset_id], item["frames"], result["read"], model)
                self.store(root, asset, record)
                batch.analyses[asset.asset_id] = record
        summaries = [record["summary"] for record in batch.analyses.values() if record.get("performed") and record.get("summary")]
        texts = list(dict.fromkeys(embed_texts + summaries + DISTRACTOR_SENTENCES))
        if texts:
            _take_embed(batch, self.worker({"embed": {"texts": texts}}), texts)
        return batch

    def cached(self, root: Path, asset: AssetInput) -> dict | None:
        path = _cache_dir(root, asset.sha256) / self.cache_name()
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("performed") and record.get("sha256") == asset.sha256 and record.get("sampler_version") == SAMPLER_VERSION:
            return record
        return None

    def _record(self, asset: AssetInput, sampling: dict, frame_paths: list[str], read: dict, model: str) -> dict:
        stamps = {item["frame"]: float(item["timestamp"]) for item in sampling.get("frames") or [] if item.get("kept")}
        frames = []
        for item in read.get("frames") or []:
            index = int(item.get("index") or 0)
            name = Path(frame_paths[index]).name if 0 <= index < len(frame_paths) else ""
            frames.append(
                {
                    "timestamp": stamps.get(name),
                    "frame": name,
                    "description": str(item.get("description") or ""),
                    "subjects": [str(x) for x in item.get("subjects") or []],
                    "actions": [str(x) for x in item.get("actions") or []],
                    "confidence": max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
                }
            )
        return {
            "asset_id": asset.asset_id,
            "sha256": asset.sha256,
            "performed": bool(frames),
            "provider": self.name,
            "model": model,
            "sampler_version": SAMPLER_VERSION,
            "analysed_at": _now(),
            "reason": "" if frames else "Gemini returned no frame descriptions.",
            "method": "Gemini description of up to three sampled frames.",
            "frames": frames,
            "summary": str(read.get("summary") or ""),
            "subjects": sorted({s for frame in frames for s in frame["subjects"]}),
            "actions": sorted({a for frame in frames for a in frame["actions"]}),
            "confidence": round(sum(frame["confidence"] for frame in frames) / len(frames), 3) if frames else 0.0,
            "sampling": {"kept": sampling.get("kept", 0), "frames": sampling.get("frames", [])},
            "thumbnail": bool(sampling.get("thumbnail")),
        }


DISTRACTOR_SENTENCES = [
    "We will discuss this topic in today's lesson.",
    "The exam has several papers.",
    "Thank you for watching.",
    "This is an important point to remember.",
    "Let us move to the next question.",
]


def _spread(items: list[str], count: int) -> list[str]:
    if len(items) <= count:
        return items
    step = (len(items) - 1) / (count - 1)
    return [items[round(index * step)] for index in range(count)]


def choose_provider(worker=None) -> VisualAnalysisProvider:
    """STUDIO_VISUAL_PROVIDER: auto (default) or clip, gemini, description (text model only), none (no models)."""
    choice = (os.environ.get("STUDIO_VISUAL_PROVIDER") or "auto").strip().lower()
    if choice == "none":
        return DescriptionOnlyProvider(None)
    runner = worker if worker is not None else WorkerRunner()
    ok = runner.available()[0] if hasattr(runner, "available") else True
    if choice == "description":
        return DescriptionOnlyProvider(runner if ok else None)
    if choice == "gemini":
        return GeminiVisualAnalysisProvider(runner) if ok else DescriptionOnlyProvider(None)
    return CLIPVisualAnalysisProvider(runner) if ok else DescriptionOnlyProvider(None)


def drop_vectors(record: dict) -> dict:
    """Public copy of an analysis record: no embeddings and no file paths."""
    return {key: value for key, value in record.items() if key not in {"image_vectors"}}


def remove_cache(root: Path, sha256: str) -> None:
    shutil.rmtree(_cache_dir(root, sha256), ignore_errors=True)


__all__ = [
    "AnalysisBatch",
    "AssetInput",
    "CLIPVisualAnalysisProvider",
    "DescriptionOnlyProvider",
    "GeminiVisualAnalysisProvider",
    "VisualAnalysisProvider",
    "WorkerRunner",
    "choose_provider",
    "drop_vectors",
    "kept_frames",
    "thumbnail_path",
]
