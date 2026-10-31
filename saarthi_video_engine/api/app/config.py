"""Studio settings. Reads STUDIO_, DATABASE_URL, and OBJECT_STORAGE_ variables from the process and api/.env."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ENGINE_ROOT / "data" / "studio"
ALLOWED_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".m4v")


def load_studio_env() -> None:
    """Load api/.env. Only Studio, database, and object-storage keys are read; engine keys are not."""
    path = API_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        allowed = key.startswith(("STUDIO_", "OBJECT_STORAGE_")) or key == "DATABASE_URL"
        if allowed and key not in os.environ:
            os.environ[key] = value


def guard_data_dir(path: Path) -> None:
    """Refuse a data directory that would mix uploads with source media or renders."""
    resolved = path.resolve()
    banned = [
        ENGINE_ROOT / "outputs",
        ENGINE_ROOT / "src",
        ENGINE_ROOT / "asset_library",
        ENGINE_ROOT / "compositions",
        ENGINE_ROOT.parent / "DR_SHIVIN_RAW",
        ENGINE_ROOT.parent / "OUTRO",
        ENGINE_ROOT.parent / "LOGO",
        ENGINE_ROOT.parent / "reference_dataset",
        ENGINE_ROOT.parent / "reference_inputs",
    ]
    for item in banned:
        try:
            item_resolved = item.resolve()
        except OSError:
            continue
        if resolved == item_resolved or item_resolved in resolved.parents:
            raise RuntimeError(
                "Studio uploads must stay outside source videos, logos, outros, and render outputs."
            )


PROCESS_SECRET = secrets.token_hex(32)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_file_bytes: int
    max_session_bytes: int
    cors_origins: tuple[str, ...]
    version: str = "1.0.0"
    environment: str = "development"
    database_url: str = ""
    storage_backend: str = "local"
    storage_endpoint: str = ""
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "auto"
    scratch_dir: Path | None = None
    auth_mode: str = "client_header"
    session_secret: str = ""
    session_ttl_hours: int = 168
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    job_runner: str = "inline"
    job_lease_seconds: int = 180
    signed_url_seconds: int = 900
    auto_migrate: bool = True
    rate_limit_per_minute: int = 600
    login_attempts_per_window: int = 5

    def __post_init__(self) -> None:
        if self.max_file_bytes < 1 or self.max_session_bytes < self.max_file_bytes:
            raise RuntimeError("Studio size limits are inconsistent.")
        guard_data_dir(self.data_dir)
        if self.auth_mode not in {"client_header", "password"}:
            raise RuntimeError("STUDIO_AUTH_MODE must be client_header or password.")
        if self.storage_backend not in {"local", "s3"}:
            raise RuntimeError("STUDIO_STORAGE_BACKEND must be local or s3.")
        if self.job_runner not in {"inline", "queue"}:
            raise RuntimeError("STUDIO_JOB_RUNNER must be inline or queue.")
        if self.cookie_samesite not in {"lax", "strict", "none"}:
            raise RuntimeError("STUDIO_COOKIE_SAMESITE must be lax, strict or none.")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise RuntimeError("STUDIO_COOKIE_SAMESITE=none requires secure cookies.")
        if self.environment == "production":
            problems = []
            if self.auth_mode != "password":
                problems.append("production requires STUDIO_AUTH_MODE=password")
            if self.storage_backend != "s3":
                problems.append("production requires object storage (STUDIO_STORAGE_BACKEND=s3)")
            if not self.database_url or self.database_url.startswith("sqlite"):
                problems.append("production requires DATABASE_URL for PostgreSQL")
            if self.job_runner != "queue":
                problems.append("production requires STUDIO_JOB_RUNNER=queue and a separate worker")
            if len(self.session_secret) < 32:
                problems.append("production requires STUDIO_SESSION_SECRET of at least 32 characters")
            if not self.cookie_secure:
                problems.append("production requires secure cookies")
            if not (self.storage_bucket and self.storage_access_key and self.storage_secret_key):
                problems.append("production requires OBJECT_STORAGE_BUCKET and access keys")
            if problems:
                raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url or f"sqlite:///{(self.data_dir / 'studio.db').as_posix()}"
        # Hosted Postgres hands out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def work_root(self) -> Path:
        """Where jobs read and write files. With local storage it is the storage root itself."""
        if self.storage_backend == "local":
            return self.data_dir
        return self.scratch_dir or (self.data_dir / "scratch")

    @property
    def signing_secret(self) -> str:
        return self.session_secret or PROCESS_SECRET

    @staticmethod
    def from_env() -> "Settings":
        load_studio_env()
        env = os.environ.get
        raw_dir = (env("STUDIO_DATA_DIR") or "").strip()
        data_dir = Path(raw_dir) if raw_dir else DEFAULT_DATA_DIR
        origins = (env("STUDIO_CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173").split(",")
        environment = (env("STUDIO_ENV") or "development").strip().lower()
        production = environment == "production"
        scratch = (env("STUDIO_SCRATCH_DIR") or "").strip()
        return Settings(
            data_dir=data_dir,
            max_file_bytes=int(env("STUDIO_MAX_FILE_BYTES") or 524_288_000),
            max_session_bytes=int(env("STUDIO_MAX_SESSION_BYTES") or 2_147_483_648),
            cors_origins=tuple(item.strip() for item in origins if item.strip()),
            environment=environment,
            database_url=(env("DATABASE_URL") or "").strip(),
            storage_backend=(env("STUDIO_STORAGE_BACKEND") or ("s3" if production else "local")).strip().lower(),
            storage_endpoint=(env("OBJECT_STORAGE_ENDPOINT") or "").strip(),
            storage_bucket=(env("OBJECT_STORAGE_BUCKET") or "").strip(),
            storage_access_key=(env("OBJECT_STORAGE_ACCESS_KEY") or "").strip(),
            storage_secret_key=(env("OBJECT_STORAGE_SECRET_KEY") or "").strip(),
            storage_region=(env("OBJECT_STORAGE_REGION") or "auto").strip(),
            scratch_dir=Path(scratch) if scratch else None,
            auth_mode=(env("STUDIO_AUTH_MODE") or ("password" if production else "client_header")).strip().lower(),
            session_secret=(env("STUDIO_SESSION_SECRET") or "").strip(),
            session_ttl_hours=int(env("STUDIO_SESSION_TTL_HOURS") or 168),
            cookie_secure=_flag(env("STUDIO_COOKIE_SECURE"), production),
            cookie_samesite=(env("STUDIO_COOKIE_SAMESITE") or "lax").strip().lower(),
            job_runner=(env("STUDIO_JOB_RUNNER") or ("queue" if production else "inline")).strip().lower(),
            job_lease_seconds=int(env("STUDIO_JOB_LEASE_SECONDS") or 180),
            signed_url_seconds=int(env("STUDIO_SIGNED_URL_SECONDS") or 900),
            auto_migrate=_flag(env("STUDIO_AUTO_MIGRATE"), not production),
            rate_limit_per_minute=int(env("STUDIO_RATE_LIMIT_PER_MINUTE") or 600),
            login_attempts_per_window=int(env("STUDIO_LOGIN_ATTEMPTS") or 5),
        )


def _flag(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
