"""Small synchronous SDK for the versioned platform API."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Generic, Iterator, Mapping, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

API_VERSION = "v1"
API_VERSION_HEADER = "1"
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


T = TypeVar("T")


@dataclass(frozen=True)
class ApiResponse(Generic[T]):
    status_code: int
    headers: Mapping[str, str]
    body: T


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    has_more: bool


class Transport(Protocol):
    def send(self, request: Request, *, timeout: float) -> ApiResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class UrllibTransport:
    """Standard-library transport that never forwards credentials on redirects."""

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirect())

    def send(self, request: Request, *, timeout: float) -> ApiResponse:
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read()
                body = json.loads(raw) if raw else None
                return ApiResponse(response.status, dict(response.headers.items()), body)
        except HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = {"detail": raw.decode("utf-8", errors="replace")[:2000]}
            return ApiResponse(exc.code, dict(exc.headers.items()), body)
        except URLError as exc:
            raise ConnectionError("platform API request failed") from exc


class PlatformApiError(RuntimeError):
    def __init__(self, status_code: int, body: Any, headers: Mapping[str, str] | None = None) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = dict(headers or {})
        if isinstance(body, dict):
            error = body.get("error", {})
            if isinstance(error, dict):
                self.code = str(error.get("code") or "http_error")
                self.message = str(error.get("message") or f"platform API returned HTTP {status_code}")
                self.details = error.get("details")
            else:
                self.code = str(error or "http_error")
                self.message = str(error or f"platform API returned HTTP {status_code}")
                self.details = None
            self.request_id = body.get("request_id")
        else:
            self.code = "http_error"
            self.message = f"platform API returned HTTP {status_code}"
            self.details = None
            self.request_id = None
        self.request_id = self.request_id or next(
            (value for key, value in self.headers.items() if key.lower() == "x-request-id"), None,
        )
        super().__init__(self.message)


class AuthenticationError(PlatformApiError):
    pass


class AuthorizationError(PlatformApiError):
    pass


class RateLimitError(PlatformApiError):
    pass


class ConflictError(PlatformApiError):
    pass


class ServerError(PlatformApiError):
    pass


class PlatformClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        transport: Transport | None = None,
        timeout: float = 15.0,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("platform base_url must be an HTTPS origin without path or credentials")
        if (
            not isinstance(token, str)
            or not token.strip()
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or timeout > 120
            or isinstance(retry_attempts, bool)
            or not isinstance(retry_attempts, int)
            or not 0 <= retry_attempts <= 5
            or isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or not 0 <= retry_backoff_seconds <= 5
            or not callable(sleep)
        ):
            raise ValueError("token, timeout, and bounded retry settings are required")
        # Accessing .port validates malformed/out-of-range port values.
        port = parsed.port
        authority = parsed.hostname if port is None else f"{parsed.hostname}:{port}"
        if ":" in parsed.hostname and not parsed.hostname.startswith("["):
            authority = f"[{parsed.hostname}]" if port is None else f"[{parsed.hostname}]:{port}"
        self.base_url = f"https://{authority}"
        self.token = token
        self.transport = transport or UrllibTransport()
        self.timeout = float(timeout)
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._sleep = sleep

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        normalized_method = method.upper() if isinstance(method, str) else ""
        if normalized_method not in _ALLOWED_METHODS:
            raise ValueError("method must be GET, POST, PUT, PATCH or DELETE")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or "#" in path
            or "\x00" in path
            or any(part == ".." for part in unquote(urlsplit(path).path).split("/"))
        ):
            raise ValueError("API path must be an absolute platform-relative path")
        if body is not None and not isinstance(body, Mapping):
            raise TypeError("request body must be a mapping")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)
        ):
            raise ValueError("idempotency_key must be a non-empty printable string up to 255 characters")

        url = f"{self.base_url}/api/platform/{API_VERSION}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "X-API-Version": API_VERSION_HEADER,
            "X-Request-ID": str(uuid4()),
            "User-Agent": "agent-platform-python/0.1",
        }
        data = None
        if body is not None:
            data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        # Retrying a write is safe only when the caller supplied an idempotency
        # key. GET/PUT/DELETE are treated as idempotent by the SDK contract.
        retry_safe = normalized_method in {"GET", "PUT", "DELETE"} or idempotency_key is not None
        attempts = self.retry_attempts if retry_safe else 0
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt in range(attempts + 1):
            request = Request(url, data=data, headers=headers, method=normalized_method)
            try:
                response = self.transport.send(request, timeout=self.timeout)
            except (ConnectionError, TimeoutError):
                if attempt >= attempts:
                    raise
                delay = min(self.retry_backoff_seconds * (2 ** attempt), 30.0)
                self._sleep(delay)
                continue
            if (
                isinstance(response.status_code, bool)
                or not isinstance(response.status_code, int)
                or not 100 <= response.status_code <= 599
            ):
                raise RuntimeError("platform transport returned an invalid HTTP status")
            if 200 <= response.status_code < 300:
                return response.body
            error_type: type[PlatformApiError] = {
                401: AuthenticationError, 403: AuthorizationError,
                409: ConflictError, 429: RateLimitError,
            }.get(response.status_code, ServerError if response.status_code >= 500 else PlatformApiError)
            error = error_type(response.status_code, response.body, response.headers)
            if response.status_code not in retryable_statuses or attempt >= attempts:
                raise error
            retry_after = next(
                (value for key, value in response.headers.items() if key.lower() == "retry-after"),
                None,
            )
            server_delay = None
            if retry_after is not None:
                try:
                    server_delay = float(retry_after)
                except (TypeError, ValueError):
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=timezone.utc)
                        server_delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        server_delay = None
            delay = server_delay if server_delay is not None and server_delay >= 0 else (
                self.retry_backoff_seconds * (2 ** attempt)
            )
            self._sleep(min(delay, 30.0))
        raise RuntimeError("platform SDK retry loop exhausted")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.request("POST", path, body=body, idempotency_key=idempotency_key)

    def put(self, path: str, body: Mapping[str, Any]) -> Any:
        return self.request("PUT", path, body=body)

    def patch(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.request("PATCH", path, body=body, idempotency_key=idempotency_key)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def get_page(
        self, path: str, *, limit: int = 100, cursor: str | None = None,
    ) -> Page[Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("page limit must be between 1 and 200")
        query: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
                raise ValueError("cursor must be a non-empty string up to 2048 characters")
            query["cursor"] = cursor
        separator = "&" if "?" in path else "?"
        result = self.get(path + separator + urlencode(query))
        if not isinstance(result, Mapping) or not isinstance(result.get("items"), list):
            raise ValueError("paginated response must contain an items array")
        next_cursor = result.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValueError("next_cursor must be a string or null")
        return Page(
            items=tuple(result["items"]),
            next_cursor=next_cursor,
            has_more=bool(result.get("has_more", bool(next_cursor))),
        )

    def iter_pages(self, path: str, *, limit: int = 100) -> Iterator[Any]:
        cursor = None
        while True:
            page = self.get_page(path, limit=limit, cursor=cursor)
            yield from page.items
            if not page.has_more or not page.next_cursor:
                break
            cursor = page.next_cursor
