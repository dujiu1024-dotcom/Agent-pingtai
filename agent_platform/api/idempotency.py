"""Execution helpers for tenant-scoped public API idempotency."""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Protocol
from uuid import uuid4

from fastapi import HTTPException

from agent_platform.api.contract import validate_idempotency_key


@dataclass(frozen=True)
class IdempotencyResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class IdempotencyClaim:
    state: str
    owner_token: str | None = None
    response: IdempotencyResponse | None = None


class IdempotencyBackend(Protocol):
    async def claim(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, request_hash: str, owner_token: str, ttl_seconds: int,
    ) -> IdempotencyClaim: ...

    async def complete(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, owner_token: str, response: IdempotencyResponse,
    ) -> bool: ...

    async def release(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, owner_token: str,
    ) -> bool: ...


def request_fingerprint(method: str, route: str, body: bytes) -> str:
    """Hash the exact method, route and request bytes with unambiguous separators."""
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if not isinstance(method, str) or not method or not method.isascii():
        raise ValueError("method must be a non-empty ASCII string")
    if not isinstance(route, str) or not route.startswith("/"):
        raise ValueError("route must be an absolute path")
    canonical = b"\0".join((method.upper().encode("ascii"), route.encode("utf-8"), body))
    return hashlib.sha256(canonical).hexdigest()


class ApiIdempotencyService:
    """Execute a mutation once; replay the exact stored response for matching retries."""

    def __init__(self, backend: IdempotencyBackend, *, ttl_seconds: int = 86400) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 604800:
            raise ValueError("idempotency TTL must be between 1 second and 7 days")
        self.backend = backend
        self.ttl_seconds = ttl_seconds

    async def execute(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, body: bytes, operation: Callable[[], Any],
    ) -> tuple[IdempotencyResponse, bool]:
        if not all(isinstance(value, str) and value.strip() for value in (
            tenant_id, principal_id, method, route,
        )):
            raise ValueError("tenant, principal, method and route are required")
        if not callable(operation):
            raise TypeError("operation must be callable")
        key = validate_idempotency_key(key)
        method = method.upper()
        digest = request_fingerprint(method, route, body)
        owner = str(uuid4())
        claim = await self.backend.claim(
            tenant_id=tenant_id, principal_id=principal_id, method=method,
            route=route, key=key, request_hash=digest, owner_token=owner,
            ttl_seconds=self.ttl_seconds,
        )
        if claim.state == "replay" and claim.response is not None:
            return claim.response, True
        if claim.state == "conflict":
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT"})
        if claim.state != "owner" or claim.owner_token != owner:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"})

        try:
            response = operation()
            if inspect.isawaitable(response):
                response = await response
            if not isinstance(response, IdempotencyResponse):
                raise TypeError("operation must return IdempotencyResponse")
            if not 100 <= response.status_code <= 599 or not isinstance(response.body, bytes):
                raise ValueError("idempotency response must contain a valid status and bytes body")
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in response.headers.items()):
                raise ValueError("idempotency response headers must be strings")
            if not await self.backend.complete(
                tenant_id=tenant_id, principal_id=principal_id, method=method,
                route=route, key=key, owner_token=owner, response=response,
            ):
                # The side effect may already have happened. Never release a lost
                # claim or report success: the caller must reconcile this outcome.
                raise RuntimeError("idempotency claim lease was lost after operation")
            return response, False
        except BaseException:
            # The backend must compare owner_token so a stale worker cannot delete
            # a newer worker's claim after lease expiry/takeover.
            await self.backend.release(
                tenant_id=tenant_id, principal_id=principal_id, method=method,
                route=route, key=key, owner_token=owner,
            )
            raise


class PostgresIdempotencyBackend:
    """Shared durable backend using an async psycopg connection pool.

    A short owner lease makes abandoned in-flight claims recoverable. The
    completed response remains available for ttl_seconds from claim creation.
    """

    def __init__(self, pool: Any, *, lease_seconds: int = 120) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if not callable(getattr(pool, "connection", None)):
            raise TypeError("pool must expose async connection()")
        self.pool = pool
        self.lease_seconds = lease_seconds

    @staticmethod
    def _scope(tenant_id: str, principal_id: str, method: str, route: str, key: str) -> tuple[str, ...]:
        return tenant_id, principal_id, method, route, key

    async def claim(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, request_hash: str, owner_token: str, ttl_seconds: int,
    ) -> IdempotencyClaim:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_api_idempotency
                       (tenant_id, principal_id, http_method, route, idempotency_key,
                        request_hash, state, owner_token, lease_expires_at, expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,
                               now() + (%s * interval '1 second'),
                               now() + (%s * interval '1 second'))
                       ON CONFLICT (tenant_id,principal_id,http_method,route,idempotency_key)
                       DO NOTHING""",
                    (*self._scope(tenant_id, principal_id, method, route, key),
                     request_hash, owner_token, self.lease_seconds, ttl_seconds),
                )
                cursor = await connection.execute(
                    """SELECT request_hash,state,owner_token,lease_expires_at,
                              response_status,response_headers,response_body,expires_at
                       FROM platform_api_idempotency
                       WHERE tenant_id=%s AND principal_id=%s AND http_method=%s
                         AND route=%s AND idempotency_key=%s
                       FOR UPDATE""",
                    self._scope(tenant_id, principal_id, method, route, key),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("idempotency claim disappeared inside transaction")
                if row["expires_at"] <= row.get("now", row["expires_at"]):
                    # Expiry is checked in SQL below; this branch is intentionally
                    # not used because row timestamps are database-clock values.
                    pass
                if row["request_hash"] != request_hash:
                    return IdempotencyClaim("conflict")
                if row["state"] == "completed":
                    if row["response_status"] is None or row["response_body"] is None:
                        raise RuntimeError("completed idempotency row has no response")
                    headers = row["response_headers"] or {}
                    return IdempotencyClaim("replay", response=IdempotencyResponse(
                        status_code=row["response_status"], headers=dict(headers),
                        body=bytes(row["response_body"]),
                    ))
                cursor = await connection.execute(
                    """UPDATE platform_api_idempotency
                       SET owner_token=%s,
                           lease_expires_at=now() + (%s * interval '1 second'),
                           expires_at=GREATEST(expires_at, now() + (%s * interval '1 second'))
                       WHERE tenant_id=%s AND principal_id=%s AND http_method=%s
                         AND route=%s AND idempotency_key=%s
                         AND state='pending' AND lease_expires_at <= now()
                       RETURNING owner_token""",
                    (owner_token, self.lease_seconds, ttl_seconds,
                     *self._scope(tenant_id, principal_id, method, route, key)),
                )
                taken = await cursor.fetchone()
                if taken is not None:
                    return IdempotencyClaim("owner", owner_token=owner_token)
                return IdempotencyClaim("in_progress")

    async def complete(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, owner_token: str, response: IdempotencyResponse,
    ) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE platform_api_idempotency
                   SET state='completed', response_status=%s, response_headers=%s::jsonb,
                       response_body=%s, owner_token=NULL, lease_expires_at=NULL,
                       completed_at=now()
                   WHERE tenant_id=%s AND principal_id=%s AND http_method=%s
                     AND route=%s AND idempotency_key=%s
                     AND state='pending' AND owner_token=%s AND lease_expires_at > now()
                   RETURNING 1""",
                (response.status_code, json.dumps(response.headers), response.body,
                 *self._scope(tenant_id, principal_id, method, route, key), owner_token),
            )
            return await cursor.fetchone() is not None

    async def release(
        self, *, tenant_id: str, principal_id: str, method: str, route: str,
        key: str, owner_token: str,
    ) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """DELETE FROM platform_api_idempotency
                   WHERE tenant_id=%s AND principal_id=%s AND http_method=%s
                     AND route=%s AND idempotency_key=%s
                     AND state='pending' AND owner_token=%s
                   RETURNING 1""",
                (*self._scope(tenant_id, principal_id, method, route, key), owner_token),
            )
            return await cursor.fetchone() is not None
