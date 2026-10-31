"""Calls into the existing Sarthi renderer. Nothing here composes a frame on its own.

Each method wraps one existing engine function. The engine helpers raise
SystemExit on a failed subprocess; that is turned into RuntimeError so a
background job records the failure instead of stopping the worker thread.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.config import ENGINE_ROOT

MUSIC_RECORD = ENGINE_ROOT / "outputs" / "dr_shivin_phase5" / "asset_cache" / "candidates.json"
UNDER_SPEECH = 0.12
OPEN = 0.28


class EngineFailure(RuntimeError):
    """An engine step failed. The detail may name local files and stays in diagnostics."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step} failed")
        self.step = step
        self.detail = detail


def _load():
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    for entry in (str(ENGINE_ROOT), str(ENGINE_ROOT / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import run_phase4 as phase4
    import run_phase5 as phase5
    from editing_plan.phase2 import clip_plan_to_duration
    from hyperframes_adapter.compose import write_composition
    from render.outro import append_outro, tool
    from validation.audio_compare import compare_wavs
    from validation.plan import validate_plan

    return {
        "phase4": phase4,
        "phase5": phase5,
        "clip_plan_to_duration": clip_plan_to_duration,
        "write_composition": write_composition,
        "append_outro": append_outro,
        "tool": tool,
        "compare_wavs": compare_wavs,
        "validate_plan": validate_plan,
        "defaults": json.loads((ENGINE_ROOT / "configs" / "render.defaults.json").read_text(encoding="utf-8")),
    }


HYPERFRAMES = "hyperframes@0.8.186"


def hyperframes_posix(composition: Path, output: Path) -> None:
    """The commands run_phase4._hyperframes runs, with npx found on PATH (Linux containers).

    run_phase4 locates npx-cli.js next to node.exe, which only holds on Windows. The
    package version, lint step, quality, worker count, and log files are the same.
    """
    import shutil
    import subprocess

    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx was not found on PATH")
    output.parent.mkdir(parents=True, exist_ok=True)
    base = [npx, "--yes", HYPERFRAMES]
    lint = subprocess.run(base + ["lint"], cwd=composition, capture_output=True, text=True)
    (composition / "lint.txt").write_text((lint.stdout or "") + (lint.stderr or ""), encoding="utf-8")
    if lint.returncode != 0:
        raise SystemExit(f"lint failed for {composition.name}")
    rendered = subprocess.run(
        base + ["render", "--quality", "draft", "--workers", "1", "--output", str(output)],
        cwd=composition,
        capture_output=True,
        text=True,
    )
    (composition / "render.txt").write_text((rendered.stdout or "") + (rendered.stderr or ""), encoding="utf-8")
    if rendered.returncode != 0 or not output.is_file():
        raise SystemExit(f"render failed for {composition.name}")


class EngineBridge:
    def __init__(self) -> None:
        self._engine = None

    @property
    def engine(self) -> dict:
        if self._engine is None:
            self._engine = _load()
        return self._engine

    def _call(self, step: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SystemExit as exc:
            raise EngineFailure(step, str(exc)) from None
        except Exception as exc:
            raise EngineFailure(step, f"{type(exc).__name__}: {exc}") from exc

    def logo_path(self) -> Path:
        return self._call("logo lookup", self.engine["phase5"]._find_logo)

    def outro_path(self) -> Path:
        return self._call("outro lookup", self.engine["phase5"]._find_outro)

    def music(self) -> tuple[Path, dict]:
        """The licensed instrumental already used by the phase 5 renders. No search or download."""
        page = self.engine["phase5"].MUSIC_PAGE
        if not MUSIC_RECORD.is_file():
            raise EngineFailure("music lookup", "The licensed music record is missing.")
        rows = json.loads(MUSIC_RECORD.read_text(encoding="utf-8"))
        for row in rows:
            if row.get("kind") != "audio" or row.get("rejected") or row.get("page_url") != page:
                continue
            recorded = str(row.get("local_path") or "")
            path = Path(recorded)
            if not path.is_file():
                # The record holds the Windows path it was made with; the deployed copy sits beside it.
                path = MUSIC_RECORD.parent / recorded.replace("\\", "/").rsplit("/", 1)[-1]
            if path.is_file():
                return path, row
        raise EngineFailure("music lookup", "The licensed instrumental file is missing.")

    def validate(self, plan: dict) -> list[str]:
        return list(self.engine["validate_plan"](plan))

    def probe(self, path: Path) -> dict:
        return self._call("ffprobe", self.engine["phase4"]._probe, path)

    def cut_teacher(self, source: Path, cut: dict, dest: Path, video_filter: str | None) -> None:
        phase4 = self.engine["phase4"]
        self._call("teacher cut", phase4._cut_teacher, source, cut, dest, video_filter)
        self._call("letterbox check", self.engine["phase5"]._remove_letterbox, dest)

    def cut_narration(self, source: Path, cut: dict, dest: Path) -> None:
        self._call("narration cut", self.engine["phase4"]._cut_audio, source, cut, dest)

    def trim_logo(self, logo: Path, dest: Path) -> None:
        self._call("logo trim", self.engine["phase5"]._trim_logo, logo, dest)

    def trim_music(self, source: Path, dest: Path, duration: float) -> None:
        self._call("music trim", self.engine["phase4"]._trim_music, source, dest, duration)

    def place_broll(self, asset: dict, asset_dir: Path, video_filter: str | None) -> None:
        self._call("b-roll placement", self.engine["phase4"]._place_asset, asset, asset_dir, video_filter)

    def clip_to(self, plan: dict, duration: float) -> dict:
        return self.engine["clip_plan_to_duration"](plan, duration)

    def compose(self, plan: dict, destination: Path, studio: dict | None = None) -> Path:
        defaults = self.engine["defaults"] if studio is None else {**self.engine["defaults"], "studio": studio}
        return self._call("composition", self.engine["write_composition"], plan, defaults, destination)

    def hyperframes(self, composition: Path, output: Path) -> None:
        """One retry: a frame-capture browser can die transiently under memory pressure."""
        run = self.engine["phase4"]._hyperframes if os.name == "nt" else hyperframes_posix
        try:
            self._call("HyperFrames render", run, composition, output)
        except EngineFailure:
            output.unlink(missing_ok=True)
            self._call("HyperFrames render", run, composition, output)

    def append_outro(self, lesson: Path, outro: Path, final: Path, fill: str = "black", audio: Path | None = None) -> dict:
        tool = self.engine["tool"]
        return self._call(
            "outro assembly",
            self.engine["append_outro"],
            lesson,
            outro,
            final,
            ffmpeg=tool("ffmpeg"),
            ffprobe=tool("ffprobe"),
            fill=fill,
            audio=audio,
        )

    def ffmpeg(self) -> str:
        return self.engine["tool"]("ffmpeg")

    def outro_seconds(self, outro: Path) -> float:
        probe = self.probe(outro)
        return float((probe.get("format") or {}).get("duration") or 0.0)

    def compare_audio(self, narration: Path, video: Path, seconds: float, workdir: Path) -> dict:
        """Same measurement as run_phase5._verify: lesson-length audio against the narration cut."""
        phase4 = self.engine["phase4"]
        ffmpeg = self.engine["tool"]("ffmpeg")
        workdir.mkdir(parents=True, exist_ok=True)
        rendered = workdir / f"{video.stem}_audio_16k.wav"
        reference = workdir / "narration_16k.wav"
        self._call(
            "audio extract",
            phase4._run,
            [ffmpeg, "-y", "-i", str(video), "-t", f"{seconds:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(rendered)],
        )
        if not reference.is_file():
            self._call(
                "narration extract",
                phase4._run,
                [ffmpeg, "-y", "-i", str(narration), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(reference)],
            )
        return self._call("audio comparison", self.engine["compare_wavs"], reference, rendered)
