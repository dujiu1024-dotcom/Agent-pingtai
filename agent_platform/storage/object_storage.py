"""S3-compatible durable object storage adapter (AWS S3 / MinIO)."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Any, Protocol


class S3Client(Protocol):
    async def put_object(self, **kwargs: Any) -> Any: ...
    async def get_object(self, **kwargs: Any) -> Any: ...
    async def delete_object(self, **kwargs: Any) -> Any: ...


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
        self, object_key: str, *, filename: str, media_type: str,
        expires_in: int = 300,
    ) -> str:
        """Create a short-lived download URL for an already-authorized asset."""
        key = self._validate_key(object_key)
        if expires_in < 30 or expires_in > 3600:
            raise ValueError("presigned URL expiry must be between 30 and 3600 seconds")
        if not filename or any(char in filename for char in "\r\n"):
            raise ValueError("filename is invalid")
        if not media_type or any(char in media_type for char in "\r\n"):
            raise ValueError("media_type is invalid")
        method = getattr(self.client, "generate_presigned_url", None)
        if method is None:
            raise ObjectStorageError("S3 client does not support presigned URLs")
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentType": media_type,
            "ResponseContentDisposition": (
                "attachment; filename*=UTF-8''" + quote(filename, safe="")
            ),
        }
        if inspect.iscoroutinefunction(method):
            url = await method(
                "get_object", Params=params, ExpiresIn=expires_in,
            )
        else:
            url = await asyncio.to_thread(
                method, "get_object", Params=params, ExpiresIn=expires_in,
            )
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ObjectStorageError("S3 client returned an invalid presigned URL")
        return url
