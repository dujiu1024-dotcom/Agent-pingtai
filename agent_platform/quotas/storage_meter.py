"""Paginated S3-compatible tenant storage measurement for quota accounting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class S3ListClient(Protocol):
    async def list_objects_v2(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class StorageMeasurement:
    tenant_id: str
    object_count: int
    storage_bytes: int


class S3TenantStorageMeter:
    """Measure object bytes under one immutable tenant prefix.

    The injected S3 client owns credentials and endpoint configuration. The
    meter never accepts a caller-supplied prefix, bucket, or continuation token.
    """

    def __init__(
        self,
        *,
        client: S3ListClient,
        bucket: str,
        max_pages: int = 1000,
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("bucket is required")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 10000:
            raise ValueError("max_pages must be an integer between 1 and 10000")
        self.client = client
        self.bucket = bucket
        self.max_pages = max_pages

    async def measure(self, *, tenant_id: str) -> StorageMeasurement:
        if (
            not isinstance(tenant_id, str)
            or not 1 <= len(tenant_id) <= 128
            or not all(character.isascii() and (character.isalnum() or character in "-_")
                       for character in tenant_id)
        ):
            raise ValueError("tenant_id must be a safe non-empty path segment")
        prefix = f"tenant/{tenant_id}/"
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        seen_keys: set[str] = set()
        object_count = 0
        storage_bytes = 0

        for _ in range(self.max_pages):
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation_token is not None:
                request["ContinuationToken"] = continuation_token
            response = await self.client.list_objects_v2(**request)
            if not isinstance(response, dict):
                raise ValueError("S3 list response must be a mapping")
            contents = response.get("Contents") or []
            if not isinstance(contents, list):
                raise ValueError("S3 Contents must be a list")
            for item in contents:
                if not isinstance(item, dict):
                    raise ValueError("S3 object entry must be a mapping")
                key = item.get("Key")
                size = item.get("Size")
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise ValueError("S3 returned an object outside the tenant prefix")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise ValueError("S3 object size must be a non-negative integer")
                if key in seen_keys:
                    raise ValueError("S3 pagination returned a duplicate object key")
                seen_keys.add(key)
                object_count += 1
                storage_bytes += size

            truncated = response.get("IsTruncated", False)
            if not isinstance(truncated, bool):
                raise ValueError("S3 IsTruncated must be a boolean")
            if not truncated:
                return StorageMeasurement(tenant_id, object_count, storage_bytes)
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise ValueError("truncated S3 response requires a continuation token")
            if next_token in seen_tokens or next_token == continuation_token:
                raise ValueError("S3 pagination continuation token repeated")
            seen_tokens.add(next_token)
            continuation_token = next_token

        raise RuntimeError("S3 storage measurement exceeded the configured page limit")
