"""Execution helpers for tenant-scoped public API idempotency."""
from __future__ import annotations
import hashlib, inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
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
    async def claim(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, request_hash: str, owner_token: str, ttl_seconds: int) -> IdempotencyClaim: ...
    async def complete(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, owner_token: str, response: IdempotencyResponse) -> bool: ...
    async def release(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, owner_token: str) -> bool: ...

def request_fingerprint(method: str, route: str, body: bytes) -> str:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    return hashlib.sha256(b"\\0".join((method.upper().encode("ascii"), route.encode("utf-8"), body))).hexdigest()

class ApiIdempotencyService:
    """Execute a mutation once; replay its response for an identical retry."""
    def __init__(self, backend: IdempotencyBackend, *, ttl_seconds: int = 86400) -> None:
        if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 604800:
            raise ValueError("idempotency TTL must be between 1 second and 7 days")
        self.backend, self.ttl_seconds = backend, ttl_seconds

    async def execute(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, body: bytes, operation: Callable[[], Any]) -> tuple[IdempotencyResponse, bool]:
        if not all(isinstance(v, str) and v.strip() for v in (tenant_id, principal_id, method, route)):
            raise ValueError("tenant, principal, method and route are required")
        key = validate_idempotency_key(key)
        method = method.upper()
        digest = request_fingerprint(method, route, body)
        owner = str(uuid4())
        claim = await self.backend.claim(tenant_id=tenant_id, principal_id=principal_id, method=method, route=route, key=key, request_hash=digest, owner_token=owner, ttl_seconds=self.ttl_seconds)
        if claim.state == "replay" and claim.response is not None:
            return claim.response, True
        if claim.state == "conflict":
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT"})
        if claim.state != "owner":
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"})
        try:
            response = operation()
            if inspect.isawaitable(response):
                response = await response
            if not isinstance(response, IdempotencyResponse):
                raise TypeError("operation must return IdempotencyResponse")
            if not await self.backend.complete(tenant_id=tenant_id, principal_id=principal_id, method=method, route=route, key=key, owner_token=owner, response=response):
                raise RuntimeError("idempotency claim lease was lost")
            return response, False
        except BaseException:
            await self.backend.release(tenant_id=tenant_id, principal_id=principal_id, method=method, route=route, key=key, owner_token=owner)
            raise
