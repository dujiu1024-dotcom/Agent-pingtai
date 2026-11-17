"""S3-compatible durable object storage adapter (AWS S3 / MinIO)."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import quote


class S3Client(Protocol):
    async def put_object(self, **kwargs: Any) -> Any: ...
    async def get_object(self, **kwargs: Any) -> Any: ...
    async def delete_object(self, **kwargs: Any) -> Any: ...
    def generate_presigned_url(self, ClientMethod: str, *, Params: dict[str, Any], ExpiresIn: int) -> Any: ...


class ObjectStorageError(RuntimeError):
    pass


class S3ObjectStorage:
    """ObjectStorage adapter using an injected async S3-compatible client.

    The client/session lifecycle and credentials are owned by host composition.
    Object keys must be tenant-prefixed and are never accepted from user paths.
    """
    def __init__(
        self, *, client: S3Client, bucket: str,
        server_side_encryption: str | None = "AES256",
        kms_key_id: str | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket is required")
        if server_side_encryption not in {None, "AES256", "aws:kms"}:
            raise ValueError("unsupported S3 server-side encryption mode")
        if server_side_encryption == "aws:kms" and not kms_key_id:
            raise ValueError("kms_key_id is required for aws:kms encryption")
        self.client = client
        self.bucket = bucket
        self.server_side_encryption = server_side_encryption
        self.kms_key_id = kms_key_id

    @staticmethod
    def _validate_key(object_key: str) -> str:
        path = PurePosixPath(object_key)
        if (
            not object_key.startswith("tenant/")
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in object_key.split("/"))
            or "\\" in object_key
        ):
            raise ObjectStorageError("object key must be a safe tenant-scoped POSIX key")
        return str(path)

    def _encryption_args(self) -> dict[str, str]:
        if self.server_side_encryption is None:
            return {}
        result = {"ServerSideEncryption": self.server_side_encryption}
        if self.kms_key_id:
            result["SSEKMSKeyId"] = self.kms_key_id
        return result

    async def put(self, object_key: str, content: bytes, *, media_type: str) -> None:
        key = self._validate_key(object_key)
        if not content:
            raise ObjectStorageError("empty object payload is not allowed")
        await self.client.put_object(
            Bucket=self.bucket, Key=key, Body=bytes(content),
            ContentType=media_type,
            Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            **self._encryption_args(),
        )

    async def get(self, object_key: str) -> bytes:
        key = self._validate_key(object_key)
        response = await self.client.get_object(Bucket=self.bucket, Key=key)
        body = response.get("Body")
        if body is None:
            raise ObjectStorageError("S3 response did not include an object body")
        try:
            result = body.read()
            if inspect.isawaitable(result):
                result = await result
            return bytes(result)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                closed = close()
                if inspect.isawaitable(closed):
                    await closed

    async def delete(self, object_key: str) -> None:
        key = self._validate_key(object_key)
        await self.client.delete_object(Bucket=self.bucket, Key=key)

    async def presign_get(
        self, object_key: str, *, expires_in: int = 300,
        filename: str | None = None, media_type: str | None = None,
    ) -> str:
        """Create a short-lived URL with safe browser download metadata."""
        key = self._validate_key(object_key)
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or not 1 <= expires_in <= 900
        ):
            raise ValueError("presigned URL TTL must be between 1 and 900 seconds")
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if filename is not None:
            if (
                not isinstance(filename, str) or not filename
                or len(filename) > 180 or filename != filename.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in filename)
            ):
                raise ValueError("invalid download filename")
            # RFC 5987 encoding prevents header syntax injection and handles Unicode.
            params["ResponseContentDisposition"] = (
                "attachment; filename*=UTF-8''" + quote(filename, safe="")
            )
        if media_type is not None:
            if (
                not isinstance(media_type, str) or len(media_type) > 127
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*",
                    media_type,
                )
            ):
                raise ValueError("invalid download media type")
            params["ResponseContentType"] = media_type.lower()
        generator = getattr(self.client, "generate_presigned_url", None)
        if not callable(generator):
            raise ObjectStorageError("S3 client does not support presigned URLs")
        result = generator(
            "get_object", Params=params, ExpiresIn=expires_in,
        )
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, str) or not result.startswith("https://"):
            raise ObjectStorageError("S3 client returned an invalid presigned URL")
        return result
