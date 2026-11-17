"""Stable public API primitives: version negotiation, errors, pagination and idempotency."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

API_VERSION = "1"
SUPPORTED_API_VERSIONS = frozenset({API_VERSION})


@dataclass(frozen=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 400 <= self.status_code <= 599 or not self.code or not self.message:
            raise ValueError("ApiError requires an HTTP error status, code and message")


def error_payload(code: str, message: str, *, request_id: str | None = None,
                  details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = dict(details)
    result: dict[str, Any] = {"error": error}
    if request_id:
        result["request_id"] = request_id
    return result


def install_api_error_handlers(app: FastAPI) -> None:
    """Install the stable envelope for domain API errors."""
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, request_id=request_id, details=exc.details),
        )


class PublicApiContractMiddleware:
    """Negotiate explicit API versions and attach a correlation/request identifier."""

    def __init__(self, app: Any, *, supported_versions: frozenset[str] = SUPPORTED_API_VERSIONS) -> None:
        if not supported_versions or any(not v.isdigit() for v in supported_versions):
            raise ValueError("supported API versions must be numeric major versions")
        self.app = app
        self.supported_versions = supported_versions

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        request_id = headers.get(b"x-request-id", b"").decode("latin-1").strip()
        if not request_id or len(request_id) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in request_id):
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        version = headers.get(b"x-api-version")
        is_public_api = path == "/api" or path.startswith("/api/")
        if is_public_api and version:
            requested = version.decode("latin-1").strip()
            if requested not in self.supported_versions:
                body = json.dumps(error_payload(
                    "unsupported_api_version",
                    "Requested API version is not supported",
                    request_id=request_id,
                    details={"requested": requested, "supported": sorted(self.supported_versions)},
                )).encode()
                await send({"type": "http.response.start", "status": 400, "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-api-version", API_VERSION.encode("ascii")),
                ]})
                await send({"type": "http.response.body", "body": body})
                return

        async def send_with_contract(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                outgoing = list(message.get("headers", []))
                present = {key.lower() for key, _ in outgoing}
                if b"x-request-id" not in present:
                    outgoing.append((b"x-request-id", request_id.encode("ascii")))
                if is_public_api and b"x-api-version" not in present:
                    outgoing.append((b"x-api-version", API_VERSION.encode("ascii")))
                message = {**message, "headers": outgoing}
            await send(message)

        await self.app(scope, receive, send_with_contract)


def encode_cursor(values: Mapping[str, Any]) -> str:
    if not isinstance(values, Mapping) or not values:
        raise ValueError("cursor values must be a non-empty mapping")
    raw = json.dumps(dict(values), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 2048:
        raise ValueError("cursor is empty or too long")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError("pagination cursor must encode a non-empty object")
    return value


@dataclass(frozen=True)
class Page:
    items: list[Any]
    next_cursor: str | None
    has_more: bool


def build_page(items: list[Any], *, limit: int, cursor_values: Mapping[str, Any] | None = None) -> Page:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    has_more = len(items) > limit
    selected = items[:limit]
    next_cursor = encode_cursor(cursor_values) if has_more and cursor_values else None
    return Page(items=selected, next_cursor=next_cursor, has_more=has_more)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    response: Any
    expires_at: float


class IdempotencyStore(Protocol):
    async def get(self, tenant_id: str, key: str) -> IdempotencyRecord | None: ...
    async def put(self, tenant_id: str, key: str, record: IdempotencyRecord) -> None: ...


class InMemoryIdempotencyStore:
    """Development/test store. Production deployments must inject a shared durable store."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}
        self._lock = RLock()

    async def get(self, tenant_id: str, key: str) -> IdempotencyRecord | None:
        with self._lock:
            record = self._records.get((tenant_id, key))
            if record is not None and record.expires_at <= time.time():
                self._records.pop((tenant_id, key), None)
                return None
            return record

    async def put(self, tenant_id: str, key: str, record: IdempotencyRecord) -> None:
        if not tenant_id or not key or len(key) > 255:
            raise ValueError("tenant_id and an idempotency key of at most 255 chars are required")
        with self._lock:
            self._records[(tenant_id, key)] = record


def request_fingerprint(method: str, path: str, body: bytes) -> str:
    canonical = b"\0".join((method.upper().encode(), path.encode(), body))
    return hashlib.sha256(canonical).hexdigest()


async def resolve_idempotency(store: IdempotencyStore, *, tenant_id: str, key: str,
                              method: str, path: str, body: bytes) -> Any | None:
    """Return a prior response, reject key reuse with a different request, else None."""
    if not key or len(key) > 255:
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key is required and must be <= 255 characters")
    record = await store.get(tenant_id, key)
    if record is None:
        return None
    fingerprint = request_fingerprint(method, path, body)
    if record.request_hash != fingerprint:
        raise ApiError(409, "idempotency_key_conflict", "Idempotency-Key was already used for a different request")
    return record.response


async def save_idempotency(store: IdempotencyStore, *, tenant_id: str, key: str,
                           method: str, path: str, body: bytes, response: Any,
                           ttl_seconds: int = 86400) -> None:
    if ttl_seconds < 1 or ttl_seconds > 604800:
        raise ValueError("idempotency TTL must be between 1 second and 7 days")
    await store.put(tenant_id, key, IdempotencyRecord(
        request_hash=request_fingerprint(method, path, body),
        response=response, expires_at=time.time() + ttl_seconds,
    ))
