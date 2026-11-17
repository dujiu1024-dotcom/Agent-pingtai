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

class PostgresIdempotencyBackend:
    """PostgreSQL-backed atomic claims shared by all API instances."""
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def claim(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, request_hash: str, owner_token: str, ttl_seconds: int) -> IdempotencyClaim:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM platform_api_idempotency WHERE tenant_id=%s AND principal_id=%s AND http_method=%s AND route=%s AND idempotency_key=%s AND expires_at<=now()", (tenant_id,principal_id,method,route,key))
                await connection.execute("INSERT INTO platform_api_idempotency (tenant_id,principal_id,http_method,route,idempotency_key,request_hash,state,owner_token,lease_until,expires_at) VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,now()+make_interval(secs=>30),now()+make_interval(secs=>%s)) ON CONFLICT DO NOTHING", (tenant_id,principal_id,method,route,key,request_hash,owner_token,ttl_seconds))
                cursor = await connection.execute("SELECT request_hash,state,owner_token,lease_until,response_status,response_headers,response_body FROM platform_api_idempotency WHERE tenant_id=%s AND principal_id=%s AND http_method=%s AND route=%s AND idempotency_key=%s FOR UPDATE", (tenant_id,principal_id,method,route,key))
                row = await cursor.fetchone()
                if row is None: raise RuntimeError('idempotency claim disappeared')
                if row['request_hash'] != request_hash: return IdempotencyClaim('conflict')
                if row['state'] == 'completed':
                    import json
                    headers = row['response_headers']
                    if isinstance(headers, str): headers = json.loads(headers)
                    return IdempotencyClaim('replay', response=IdempotencyResponse(row['response_status'], dict(headers or {}), bytes(row['response_body'] or b'')))
                if row['owner_token'] == owner_token: return IdempotencyClaim('owner', owner_token=owner_token)
                cursor = await connection.execute("UPDATE platform_api_idempotency SET owner_token=%s,lease_until=now()+make_interval(secs=>30) WHERE tenant_id=%s AND principal_id=%s AND http_method=%s AND route=%s AND idempotency_key=%s AND state='pending' AND lease_until<=now() RETURNING owner_token", (owner_token,tenant_id,principal_id,method,route,key))
                if await cursor.fetchone(): return IdempotencyClaim('owner', owner_token=owner_token)
                return IdempotencyClaim('in_progress')

    async def complete(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, owner_token: str, response: IdempotencyResponse) -> bool:
        import json
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute("UPDATE platform_api_idempotency SET state='completed',response_status=%s,response_headers=%s,response_body=%s,owner_token=NULL,lease_until=NULL,completed_at=now() WHERE tenant_id=%s AND principal_id=%s AND http_method=%s AND route=%s AND idempotency_key=%s AND state='pending' AND owner_token=%s", (response.status_code,json.dumps(response.headers),response.body,tenant_id,principal_id,method,route,key,owner_token))
            return cursor.rowcount == 1

    async def release(self, *, tenant_id: str, principal_id: str, method: str, route: str, key: str, owner_token: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute("DELETE FROM platform_api_idempotency WHERE tenant_id=%s AND principal_id=%s AND http_method=%s AND route=%s AND idempotency_key=%s AND state='pending' AND owner_token=%s", (tenant_id,principal_id,method,route,key,owner_token))
            return cursor.rowcount == 1