"""Trusted publisher key registry contract and validation."""
from __future__ import annotations

import math
import re
from typing import Any, Protocol

from agent_platform.extensions.oci_runtime import TrustedPublisherKey

_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PUBLISHER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EXTENSION_ID = re.compile(r"[a-z][a-z0-9-]{1,62}$")


class TrustedPublisherKeyRepository(Protocol):
    async def register(self, *, tenant_id: str, extension_id: str, publisher_id: str,
                       key_id: str, public_key_pem: bytes, not_before: float,
                       expires_at: float | None, created_by: str) -> TrustedPublisherKey: ...
    async def rotate(self, *, tenant_id: str, extension_id: str, publisher_id: str,
                     key_id: str, public_key_pem: bytes, not_before: float,
                     expires_at: float | None, created_by: str,
                     revoke_key_ids: tuple[str, ...]) -> TrustedPublisherKey: ...
    async def revoke(self, *, tenant_id: str, extension_id: str, key_id: str,
                     revoked_by: str) -> bool: ...
    async def resolve(self, tenant_id: str, extension_id: str,
                      key_id: str) -> TrustedPublisherKey | None: ...


class TrustedPublisherKeyRegistry:
    """Validate publisher trust changes before delegating to durable storage.

    This registry deliberately has no in-memory production fallback. The
    injected repository owns persistence and tenant scoping.
    """

    def __init__(self, repository: TrustedPublisherKeyRepository) -> None:
        for method in ("register", "rotate", "revoke", "resolve"):
            if not callable(getattr(repository, method, None)):
                raise TypeError(f"publisher key repository must implement {method}()")
        self.repository = repository

    @staticmethod
    def _validate(*, tenant_id: str, extension_id: str, publisher_id: str,
                  key_id: str, public_key_pem: bytes, not_before: float,
                  expires_at: float | None, created_by: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip() or len(tenant_id) > 128:
            raise ValueError("tenant_id is required")
        if not isinstance(extension_id, str) or not _EXTENSION_ID.fullmatch(extension_id):
            raise ValueError("invalid extension_id")
        if not isinstance(publisher_id, str) or not _PUBLISHER_ID.fullmatch(publisher_id):
            raise ValueError("invalid publisher_id")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise ValueError("invalid publisher key_id")
        if (not isinstance(public_key_pem, bytes) or not public_key_pem
                or len(public_key_pem) > 8192
                or b"-----BEGIN PUBLIC KEY-----" not in public_key_pem
                or b"PRIVATE KEY-----" in public_key_pem):
            raise ValueError("public_key_pem must be a PEM public key of at most 8 KiB")
        if (isinstance(not_before, bool) or not isinstance(not_before, (int, float))
                or not math.isfinite(not_before)):
            raise ValueError("not_before must be a finite Unix timestamp")
        if expires_at is not None and (
            isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at) or expires_at <= not_before
        ):
            raise ValueError("expires_at must be later than not_before")
        if not isinstance(created_by, str) or not created_by.strip() or len(created_by) > 128:
            raise ValueError("created_by is required")

    async def register(self, **values: Any) -> TrustedPublisherKey:
        self._validate(**values)
        return await self.repository.register(**values)

    async def rotate(self, *, revoke_key_ids: tuple[str, ...] = (), **values: Any) -> TrustedPublisherKey:
        self._validate(**values)
        if (not isinstance(revoke_key_ids, tuple)
                or len(set(revoke_key_ids)) != len(revoke_key_ids)
                or any(not isinstance(item, str) or not _KEY_ID.fullmatch(item)
                       for item in revoke_key_ids)):
            raise ValueError("revoke_key_ids must contain unique valid key ids")
        if values["key_id"] in revoke_key_ids:
            raise ValueError("a new key cannot revoke itself")
        return await self.repository.rotate(revoke_key_ids=revoke_key_ids, **values)

    async def revoke(self, *, tenant_id: str, extension_id: str, key_id: str,
                     revoked_by: str) -> bool:
        if not isinstance(tenant_id, str) or not tenant_id.strip() or len(tenant_id) > 128:
            raise ValueError("tenant_id is required")
        if not isinstance(extension_id, str) or not _EXTENSION_ID.fullmatch(extension_id):
            raise ValueError("invalid extension_id")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise ValueError("invalid publisher key_id")
        if not isinstance(revoked_by, str) or not revoked_by.strip() or len(revoked_by) > 128:
            raise ValueError("revoked_by is required")
        return await self.repository.revoke(
            tenant_id=tenant_id, extension_id=extension_id, key_id=key_id,
            revoked_by=revoked_by,
        )

    async def resolve(self, tenant_id: str, extension_id: str,
                      key_id: str) -> TrustedPublisherKey | None:
        return await self.repository.resolve(tenant_id, extension_id, key_id)
