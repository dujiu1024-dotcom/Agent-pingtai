"""Stable public HTTP API contract primitives and ASGI enforcement."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

API_V1_PREFIX = "/api/platform/v1"
API_VERSION_HEADER = "X-API-Version"
REQUEST_ID_HEADER = "X-Request-ID"
SUPPORTED_API_VERSIONS = ("v1",)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class ApiError:
    """Stable machine-readable error independent of internal exception text."""

    code: str
    message: str
    status: int
    request_id: str | None = None
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", self.code):
            raise ValueError("error code must be a stable uppercase identifier")
        if not self.message or len(self.message) > 512:
            raise ValueError("error message must contain 1..512 characters")
        if isinstance(self.status, bool) or not 400 <= self.status <= 599:
            raise ValueError("error status must be an HTTP error status")
        if self.request_id is not None and not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("request_id must contain 1..128 safe characters")

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.request_id is not None:
            error["request_id"] = self.request_id
        if self.details:
            error["details"] = dict(self.details)
        return {"error": error}


def validate_page(*, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> tuple[int, str | None]:
    """Validate bounded cursor pagination; cursors are opaque to API consumers."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 1024):
        raise ValueError("cursor must contain 1..1024 characters")
    return limit, cursor


def validate_idempotency_key(value: str | None) -> str:
    """Require a bounded client key for retryable state-changing requests."""
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ValueError("Idempotency-Key must be 1..128 safe ASCII characters")
    return value


def idempotency_scope(*, tenant_id: str, principal_id: str, method: str,
                      route: str, key: str) -> tuple[str, str, str, str, str]:
    """Build uniqueness scope; never deduplicate across tenant/principal/route."""
    if not all(isinstance(v, str) and v.strip() for v in
               (tenant_id, principal_id, method, route)):
        raise ValueError("tenant, principal, method and route are required")
    return tenant_id, principal_id, method.upper(), route, validate_idempotency_key(key)


def validate_api_version(version: str) -> str:
    if version not in SUPPORTED_API_VERSIONS:
        raise ValueError(f"unsupported API version: {version}")
    return version


def error_for_http_status(status: int, message: str, *, request_id: str | None = None) -> ApiError:
    """Map transport status to public stable codes; never expose server exception details."""
    codes = {
        400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN",
        404: "NOT_FOUND", 409: "CONFLICT", 410: "GONE", 412: "PRECONDITION_FAILED",
        422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR",
        502: "UPSTREAM_ERROR", 503: "SERVICE_UNAVAILABLE", 504: "UPSTREAM_TIMEOUT",
    }
    code = codes.get(status, "REQUEST_FAILED" if 400 <= status < 500 else "INTERNAL_ERROR")
    safe_message = message if 400 <= status < 500 else "The request could not be completed"
    return ApiError(code=code, message=safe_message, status=status, request_id=request_id)


def rate_limit_headers(*, limit: int, remaining: int, reset_epoch: int,
                       retry_after: int | None = None) -> dict[str, str]:
    if any(isinstance(value, bool) for value in (limit, remaining, reset_epoch)):
        raise ValueError("rate limit counters must be integers")
    if limit < 1 or not 0 <= remaining <= limit or reset_epoch < 0:
        raise ValueError("invalid rate limit counters")
    headers = {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(remaining),
        "RateLimit-Reset": str(reset_epoch),
    }
    if retry_after is not None:
        if isinstance(retry_after, bool) or retry_after < 0:
            raise ValueError("retry_after must not be negative")
        headers["Retry-After"] = str(retry_after)
    return headers


class PublicApiContractASGIMiddleware:
    """Enforce URL/header version agreement, request correlation and shared IP limits."""

    def __init__(self, app: Any, *, rate_limiter: Any, limit: int = 120,
                 window_seconds: int = 60) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("API rate limit settings must be positive")
        self.app = app
        self.rate_limiter = rate_limiter
        self.limit = limit
        self.window_seconds = window_seconds

    async def _reject(self, send: Any, error: ApiError,
                      headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps(error.to_dict(), separators=(",", ":")).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-request-id", (error.request_id or "").encode("ascii")),
        ]
        response_headers.extend(headers or [])
        await send({"type": "http.response.start", "status": error.status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not path.startswith("/api/platform/"):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied_id = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = supplied_id if _REQUEST_ID.fullmatch(supplied_id) else str(uuid4())
        scope.setdefault("state", {})["api.request_id"] = request_id

        segments = path.split("/")
        version = segments[3] if len(segments) > 3 else ""
        if version not in SUPPORTED_API_VERSIONS:
            await self._reject(send, ApiError(
                "UNSUPPORTED_API_VERSION", "The requested API version is not supported",
                404, request_id,
            ))
            return

        requested = headers.get(b"x-api-version")
        if requested is not None:
            try:
                header_version = requested.decode("ascii")
                validate_api_version(header_version)
            except (UnicodeDecodeError, ValueError):
                await self._reject(send, ApiError(
                    "UNSUPPORTED_API_VERSION", "X-API-Version must be v1", 400, request_id,
                ))
                return
            if header_version != version:
                await self._reject(send, ApiError(
                    "API_VERSION_MISMATCH", "X-API-Version does not match the URL version",
                    400, request_id,
                ))
                return

        client = scope.get("client") or ("unknown", 0)
        rate_key = f"{client[0]}:{scope.get('method', 'GET').upper()}"
        try:
            allowed = await self.rate_limiter.allow(
                rate_key, limit=self.limit, window_seconds=self.window_seconds,
            )
        except Exception:
            await self._reject(send, ApiError(
                "SERVICE_UNAVAILABLE", "API rate limit service is unavailable", 503, request_id,
            ))
            return
        if not allowed:
            retry_after = self.window_seconds
            limit_headers = rate_limit_headers(
                limit=self.limit, remaining=0, reset_epoch=int(time.time()) + retry_after,
                retry_after=retry_after,
            )
            await self._reject(send, ApiError(
                "RATE_LIMITED", "API request rate limit exceeded", 429, request_id,
            ), [(key.lower().encode("ascii"), value.encode("ascii"))
                for key, value in limit_headers.items()])
            return

        async def contract_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                outgoing = [(key, value) for key, value in message.get("headers", [])
                            if key.lower() not in (b"x-api-version", b"x-request-id")]
                outgoing.extend([
                    (b"x-api-version", version.encode("ascii")),
                    (b"x-request-id", request_id.encode("ascii")),
                ])
                message = {**message, "headers": outgoing}
            await send(message)

        await self.app(scope, receive, contract_send)
