"""Private object storage for invoice files and exports (spec 8).

Objects are never public. Downloads go through short-lived signed URLs: S3 presigned URLs, or for
the local driver an HMAC-signed link served by ``GET /api/v1/files/signed``.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode

from app.core.config import get_settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def signed_url(
        self,
        key: str,
        *,
        filename: str,
        content_type: str,
        expires_in: int | None = None,
        inline: bool = False,
    ) -> str: ...


def _disposition(filename: str, inline: bool) -> str:
    safe = filename.replace('"', "").replace("\\", "")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename)}"


# --- signed tokens for the local driver ---------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_payload(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify_payload(token: str) -> dict | None:
    try:
        body, mac = token.split(".", 1)
        expected = hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256)
        if not hmac.compare_digest(_b64(expected.digest()), mac):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("Object not found") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def signed_url(
        self,
        key: str,
        *,
        filename: str,
        content_type: str,
        expires_in: int | None = None,
        inline: bool = False,
    ) -> str:
        s = get_settings()
        token = sign_payload(
            {
                "k": key,
                "f": filename,
                "t": content_type,
                "i": inline,
                "exp": int(time.time()) + (expires_in or s.signed_url_ttl_seconds),
            }
        )
        return f"{s.api_base_url}/api/v1/files/signed?{urlencode({'token': token})}"


class S3Storage:
    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError("Object not found") from exc

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def signed_url(
        self,
        key: str,
        *,
        filename: str,
        content_type: str,
        expires_in: int | None = None,
        inline: bool = False,
    ) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": _disposition(filename, inline),
                "ResponseContentType": content_type,
            },
            ExpiresIn=expires_in or get_settings().signed_url_ttl_seconds,
        )


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_driver == "s3":
        return S3Storage()
    return LocalStorage(s.storage_local_path)


content_disposition = _disposition
