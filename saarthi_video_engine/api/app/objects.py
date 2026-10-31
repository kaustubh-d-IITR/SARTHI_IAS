"""Object storage. Keys are server-built (UUIDs and SHA-256 values), never user filenames.

LocalStorageProvider keeps objects under a directory and signs its own short-lived
URLs, served by the API. S3StorageProvider talks to Cloudflare R2 or AWS S3 with a
private bucket and presigned URLs. Neither ever returns a permanent public URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1024 * 1024
_KEY = re.compile(r"^(uploads|work|visual|transcript_cache|renders)/[A-Za-z0-9._\-/]+$")


class StorageError(RuntimeError):
    pass


def check_key(key: str) -> str:
    """Reject anything that is not a Studio key: no absolute paths, no '..', no backslashes."""
    if not isinstance(key, str) or not _KEY.match(key) or ".." in key.split("/") or "//" in key or key.endswith("/"):
        raise StorageError("Invalid object key.")
    return key


@dataclass
class ObjectInfo:
    key: str
    size: int
    etag: str = ""


class ObjectStorage:
    backend = "base"

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def download(self, key: str, dest: Path) -> Path: ...
    def delete(self, key: str) -> None: ...
    def head(self, key: str) -> ObjectInfo | None: ...
    def list(self, prefix: str) -> list[ObjectInfo]: ...
    def presign_get(self, key: str, seconds: int, filename: str | None = None, inline: bool = True) -> str: ...
    def presign_put(self, key: str, seconds: int, content_type: str) -> dict: ...
    def ping(self) -> tuple[bool, str]: ...

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def size(self, key: str) -> int | None:
        info = self.head(key)
        return info.size if info else None

    def sha256(self, key: str) -> str:
        return hashlib.sha256(self.get_bytes(key)).hexdigest()

    def local_root(self) -> Path | None:
        """The directory that holds the objects, when that is the local disk."""
        return None


def _sign(secret: str, payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify_token(secret: str, token: str) -> dict:
    try:
        body, mac = token.rsplit(".", 1)
    except ValueError as exc:
        raise StorageError("Invalid link.") from exc
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise StorageError("Invalid link.")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise StorageError("This link has expired.")
    check_key(payload.get("key", ""))
    return payload


class LocalStorageProvider(ObjectStorage):
    """Objects are files under root. Signed URLs point back at the API (/api/files/...)."""

    backend = "local"

    def __init__(self, root: Path, secret: str, url_prefix: str = "/api/files") -> None:
        self.root = Path(root)
        self.secret = secret
        self.url_prefix = url_prefix

    def path(self, key: str) -> Path:
        check_key(key)
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise StorageError("Invalid object key.")
        return path

    def local_root(self) -> Path | None:
        return self.root

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        dest = self.path(key)
        if Path(path).resolve() == dest:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    def get_bytes(self, key: str) -> bytes:
        path = self.path(key)
        if not path.is_file():
            raise StorageError("Object not found.")
        return path.read_bytes()

    def download(self, key: str, dest: Path) -> Path:
        src = self.path(key)
        if not src.is_file():
            raise StorageError("Object not found.")
        if src != Path(dest).resolve():
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        return Path(dest)

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def head(self, key: str) -> ObjectInfo | None:
        path = self.path(key)
        if not path.is_file():
            return None
        return ObjectInfo(key, path.stat().st_size)

    def list(self, prefix: str) -> list[ObjectInfo]:
        base = self.root / prefix
        if not base.exists():
            return []
        rows = []
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.endswith(".part"):
                key = path.relative_to(self.root).as_posix()
                if _KEY.match(key):
                    rows.append(ObjectInfo(key, path.stat().st_size))
        return rows

    def sha256(self, key: str) -> str:
        digest = hashlib.sha256()
        with self.path(key).open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def presign_get(self, key: str, seconds: int, filename: str | None = None, inline: bool = True) -> str:
        check_key(key)
        token = _sign(self.secret, {"key": key, "exp": int(time.time()) + seconds, "op": "get", "name": filename or "", "inline": inline})
        return f"{self.url_prefix}/{token}"

    def presign_put(self, key: str, seconds: int, content_type: str) -> dict:
        check_key(key)
        token = _sign(self.secret, {"key": key, "exp": int(time.time()) + seconds, "op": "put", "type": content_type})
        return {"method": "PUT", "url": f"{self.url_prefix}/{token}", "headers": {"Content-Type": content_type}}

    def ping(self) -> tuple[bool, str]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".storage_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, ""
        except OSError as exc:
            return False, f"local storage is not writable ({type(exc).__name__})"


class S3StorageProvider(ObjectStorage):
    """Cloudflare R2 or AWS S3. The bucket must be private; access is only by presigned URL."""

    backend = "s3"

    def __init__(self, bucket: str, endpoint: str, access_key: str, secret_key: str, region: str = "auto", client=None) -> None:
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint or None,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region or "auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 4, "mode": "standard"}, s3={"addressing_style": "path"}),
            )
        self.client = client
        self._private_checked_at = 0.0

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        self.client.upload_file(str(path), self.bucket, check_key(key), ExtraArgs={"ContentType": content_type})

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=check_key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError("Object not found.") from exc

    def download(self, key: str, dest: Path) -> Path:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(dest) + ".part")
        self.client.download_file(self.bucket, check_key(key), str(tmp))
        tmp.replace(dest)
        return Path(dest)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=check_key(key))

    def head(self, key: str) -> ObjectInfo | None:
        from botocore.exceptions import ClientError

        try:
            info = self.client.head_object(Bucket=self.bucket, Key=check_key(key))
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectInfo(key, int(info["ContentLength"]), str(info.get("ETag", "")).strip('"'))

    def list(self, prefix: str) -> list[ObjectInfo]:
        rows = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents") or []:
                if _KEY.match(item["Key"]):
                    rows.append(ObjectInfo(item["Key"], int(item["Size"]), str(item.get("ETag", "")).strip('"')))
        return rows

    def sha256(self, key: str) -> str:
        body = self.client.get_object(Bucket=self.bucket, Key=check_key(key))["Body"]
        digest = hashlib.sha256()
        for chunk in iter(lambda: body.read(CHUNK), b""):
            digest.update(chunk)
        return digest.hexdigest()

    def presign_get(self, key: str, seconds: int, filename: str | None = None, inline: bool = True) -> str:
        params = {"Bucket": self.bucket, "Key": check_key(key)}
        if filename:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:80]
            params["ResponseContentDisposition"] = f'{"inline" if inline else "attachment"}; filename="{safe}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)

    def presign_put(self, key: str, seconds: int, content_type: str) -> dict:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": check_key(key), "ContentType": content_type},
            ExpiresIn=seconds,
        )
        return {"method": "PUT", "url": url, "headers": {"Content-Type": content_type}}

    def ping(self) -> tuple[bool, str]:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as exc:  # noqa: BLE001
            return False, f"object storage is not reachable ({type(exc).__name__})"
        return self.check_private()

    def check_private(self, every_seconds: float = 600) -> tuple[bool, str]:
        """Write a probe object and read it back without credentials. Any success means the bucket is public."""
        now = time.monotonic()
        if self._private_checked_at and now - self._private_checked_at < every_seconds:
            return True, ""
        import httpx

        key = "work/.privacy-probe"
        try:
            self.put_bytes(key, b"private", "text/plain")
            response = httpx.get(f"{self.client.meta.endpoint_url.rstrip('/')}/{self.bucket}/{key}", timeout=10)
        except Exception as exc:  # noqa: BLE001
            return False, f"object storage privacy check failed ({type(exc).__name__})"
        if response.status_code < 300:
            return False, "the bucket allows anonymous reads; make it private"
        self._private_checked_at = now
        return True, ""


def build_storage(settings) -> ObjectStorage:
    if settings.storage_backend == "s3":
        return S3StorageProvider(
            settings.storage_bucket,
            settings.storage_endpoint,
            settings.storage_access_key,
            settings.storage_secret_key,
            settings.storage_region,
        )
    return LocalStorageProvider(settings.data_dir, settings.signing_secret)
