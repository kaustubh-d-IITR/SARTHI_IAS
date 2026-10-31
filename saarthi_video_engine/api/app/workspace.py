"""Local copy of a job's objects.

The pipeline (ffprobe, ffmpeg, CLIP, HyperFrames) needs real files. A Workspace
pulls the objects a job needs into a scratch directory with the same relative keys,
and pushes back only files that are new or changed. When the storage is the local
disk and the scratch root is that same directory, pull and push do nothing.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from app.objects import ObjectStorage, StorageError, check_key


class Workspace:
    def __init__(self, storage: ObjectStorage, root: Path) -> None:
        self.storage = storage
        self.root = Path(root)
        local = storage.local_root()
        self.passthrough = local is not None and Path(local).resolve() == self.root.resolve()
        self._seen: dict[str, tuple[int, int]] = {}

    def path(self, key: str) -> Path:
        check_key(key)
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("Invalid object key.")
        return path

    def _remember(self, key: str, path: Path) -> None:
        stat = path.stat()
        self._seen[key] = (stat.st_size, stat.st_mtime_ns)

    def pull(self, key: str) -> Path | None:
        """Make the object available locally. Returns None when it does not exist."""
        path = self.path(key)
        if self.passthrough:
            return path if path.is_file() else None
        info = self.storage.head(key)
        if info is None:
            return path if path.is_file() else None
        if not path.is_file() or path.stat().st_size != info.size:
            self.storage.download(key, path)
        self._remember(key, path)
        return path

    def pull_prefix(self, prefix: str, exclude: tuple[str, ...] = ()) -> int:
        if self.passthrough:
            return 0
        count = 0
        for info in self.storage.list(prefix):
            if any(info.key.startswith(item) for item in exclude):
                continue
            path = self.path(info.key)
            if not path.is_file() or path.stat().st_size != info.size:
                self.storage.download(info.key, path)
                count += 1
            self._remember(info.key, path)
        return count

    def push_prefix(self, prefix: str, exclude: tuple[str, ...] = ()) -> int:
        """Upload local files under prefix that are new or changed since they were pulled."""
        if self.passthrough:
            return 0
        base = self.root / prefix
        if not base.exists():
            return 0
        count = 0
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.endswith(".part"):
                continue
            key = path.relative_to(self.root).as_posix()
            if any(key.startswith(item) for item in exclude):
                continue
            stat = path.stat()
            if self._seen.get(key) == (stat.st_size, stat.st_mtime_ns):
                continue
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.storage.put_file(key, path, content_type)
            self._remember(key, path)
            count += 1
        return count
