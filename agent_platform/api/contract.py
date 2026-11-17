"""Stable public HTTP API contract primitives shared by platform routers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

API_V1_PREFIX = "/api/platform/v1"
API_VERSION_HEADER = "X-API-Version"
SUPPORTED_API_VERSIONS = ("v1",)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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
        if self.request_id is not None and len(self.request_id) > 128:
            raise ValueError("request_id is too long")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error": {"code": self.code, "message": self.message},
        }
        if self.request_id is not None:
            result["error"]["request_id"] = self.request_id
        if self.details:
            result["error"]["details"] = dict(self.details)
        return result


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
    """Build the uniqueness scope; never deduplicate across tenant/principal/route."""
    if not all(isinstance(v, str) and v.strip() for v in
               (tenant_id, principal_id, method, route)):
        raise ValueError("tenant, principal, method and route are required")
    return tenant_id, principal_id, method.upper(), route, validate_idempotency_key(key)


def validate_api_version(version: str) -> str:
    if version not in SUPPORTED_API_VERSIONS:
        raise ValueError(f"unsupported API version: {version}")
    return version


def error_for_http_status(status: int, message: str, *, request_id: str | None = None) -> ApiError:
    """Map transport status to public stable codes; do not expose exception details."""
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
    if limit < 1 or not 0 <= remaining <= limit or reset_epoch < 0:
        raise ValueError("invalid rate limit counters")
    headers = {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(remaining),
        "RateLimit-Reset": str(reset_epoch),
    }
    if retry_after is not None:
        if retry_after < 0:
            raise ValueError("retry_after must not be negative")
        headers["Retry-After"] = str(retry_after)
    return headers


class PublicApiContractASGIMiddleware:
    """Validate an optional version assertion and label every v1 API response.

    The URL prefix is authoritative; the header is an assertion only, keeping
    clients that omit it backward-compatible.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(API_V1_PREFIX):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        requested = headers.get(b"x-api-version")
        if requested is not None:
            try:
                validate_api_version(requested.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                error = error_for_http_status(400, "Unsupported API version")
                body = json.dumps(error.to_dict()).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"x-api-version", b"v1"),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return

        async def versioned_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                outgoing = list(message.get("headers", []))
                if not any(key.lower() == b"x-api-version" for key, _ in outgoing):
                    outgoing.append((b"x-api-version", b"v1"))
                message = {**message, "headers": outgoing}
            await send(message)

        await self.app(scope, receive, versioned_send)
