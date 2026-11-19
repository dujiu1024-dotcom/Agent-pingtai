"""Framework-neutral ASGI identity and HTTP security middleware.

The middleware is intentionally independent of FastAPI/Starlette. Applications
must configure protected_paths and exact CORS origins at composition time.
"""
from __future__ import annotations

import hmac
import inspect
import time
from collections import defaultdict, deque
from secrets import token_urlsafe
from typing import Any, Protocol
from urllib.parse import urlsplit

from agent_platform.identity.service import AuthenticationError, IdentityService


class RateLimiter(Protocol):
    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool: ...


class RedisSlidingWindowRateLimiter:
    """Shared Redis sliding-window limiter using one atomic Lua operation."""

    _SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
      return 0
    end
    redis.call('ZADD', key, now, ARGV[4])
    redis.call('EXPIRE', key, math.ceil(window / 1000))
    return 1
    """

    def __init__(self, redis: Any, *, key_prefix: str = "platform:auth:rate:") -> None:
        if not key_prefix:
            raise ValueError("key_prefix is required")
        self.redis = redis
        self.key_prefix = key_prefix

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit settings must be positive")
        now_ms = int(time.time() * 1000)
        allowed = await self.redis.eval(
            self._SCRIPT, 1, self.key_prefix + key,
            now_ms, window_seconds * 1000, limit, token_urlsafe(16),
        )
        return bool(allowed)


def auth_cookie_headers(
    *, session_token: str, csrf_token: str, max_age_seconds: int,
    session_cookie: str = "__Host-platform_session",
    csrf_cookie: str = "__Host-platform_csrf",
) -> list[tuple[bytes, bytes]]:
    """Build secure session and double-submit CSRF Set-Cookie headers."""
    if not session_token or not csrf_token or max_age_seconds < 1:
        raise ValueError("tokens and positive max_age_seconds are required")
    for value in (session_token, csrf_token):
        if not value.isascii() or not all(
            character.isalnum() or character in "-_." for character in value
        ):
            raise ValueError("cookie token contains unsafe characters")
    for name in (session_cookie, csrf_cookie):
        if not name.startswith("__Host-") or not all(
            character.isalnum() or character in "-_" for character in name
        ):
            raise ValueError("auth cookie names must be valid __Host- names")
    return [
        (b"set-cookie", (
            f"{session_cookie}={session_token}; Path=/; Max-Age={max_age_seconds}; "
            "Secure; HttpOnly; SameSite=Strict"
        ).encode("ascii")),
        (b"set-cookie", (
            f"{csrf_cookie}={csrf_token}; Path=/; Max-Age={max_age_seconds}; "
            "Secure; SameSite=Strict"
        ).encode("ascii")),
    ]


def clear_auth_cookie_headers(
    *, session_cookie: str = "__Host-platform_session",
    csrf_cookie: str = "__Host-platform_csrf",
) -> list[tuple[bytes, bytes]]:
    for name in (session_cookie, csrf_cookie):
        if not name.startswith("__Host-") or not all(
            character.isalnum() or character in "-_" for character in name
        ):
            raise ValueError("auth cookie names must be valid __Host- names")
    return [
        (b"set-cookie", f"{session_cookie}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict".encode("ascii")),
        (b"set-cookie", f"{csrf_cookie}=; Path=/; Max-Age=0; Secure; SameSite=Strict".encode("ascii")),
    ]


class InMemorySlidingWindowRateLimiter:
    """Single-process fallback for development; use a shared Redis adapter in production."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


class IdentityASGIMiddleware:
    """Authenticate HTTP requests and enforce cookie CSRF/CORS/security headers."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    PUBLIC_PATHS = {"/health", "/ready", "/auth/login", "/api/platform/v1/bootstrap"}

    def __init__(
        self,
        app: Any,
        *,
        identity: IdentityService | None,
        service_identity: Any | None = None,
        session_cookie: str = "__Host-platform_session",
        csrf_cookie: str = "__Host-platform_csrf",
        allowed_origins: tuple[str, ...] = (),
        protected_paths: tuple[str, ...] = ("/api", "/auth"),
        login_rate_limiter: RateLimiter | None = None,
        login_limit: int = 10,
        login_window_seconds: int = 300,
        trust_forwarded_headers: bool = False,
        local_principal: Any | None = None,
        local_client_hosts: tuple[str, ...] = ("127.0.0.1", "::1"),
        auth_enabled: bool = True,
    ) -> None:
        if auth_enabled and identity is None:
            raise ValueError("identity service is required when authentication is enabled")
        if not session_cookie.startswith("__Host-"):
            raise ValueError("session cookie must use __Host- prefix")
        if not csrf_cookie.startswith("__Host-"):
            raise ValueError("CSRF cookie must use __Host- prefix")
        if login_limit < 1 or login_window_seconds < 1:
            raise ValueError("login rate limit settings must be positive")
        normalized_origins = tuple(self._normalize_origin(x) for x in allowed_origins)
        if len(set(normalized_origins)) != len(normalized_origins):
            raise ValueError("allowed_origins must be unique")
        self.app = app
        self.identity = identity
        self.auth_enabled = auth_enabled
        self.service_identity = service_identity
        self.session_cookie = session_cookie
        self.csrf_cookie = csrf_cookie
        self.allowed_origins = frozenset(normalized_origins)
        self.protected_paths = protected_paths
        self.login_rate_limiter = login_rate_limiter or InMemorySlidingWindowRateLimiter()
        self.login_limit = login_limit
        self.login_window_seconds = login_window_seconds
        self.trust_forwarded_headers = trust_forwarded_headers
        self.local_principal = local_principal
        self.local_client_hosts = frozenset(local_client_hosts)

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("CORS origins must be exact HTTPS origins")
        return f"https://{parsed.netloc.lower()}"

    @staticmethod
    def _headers(scope: dict[str, Any]) -> dict[bytes, bytes]:
        return {k.lower(): v for k, v in scope.get("headers", [])}

    @staticmethod
    def _cookies(raw: bytes) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in raw.decode("latin-1").split(";"):
            name, sep, value = item.strip().partition("=")
            if sep and name:
                result[name] = value
        return result

    @staticmethod
    def _security_headers() -> list[tuple[bytes, bytes]]:
        return [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
            (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'"),
            (b"cache-control", b"no-store"),
            (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        ]

    async def _respond(
        self, send: Any, status: int, body: bytes,
        extra_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        headers = [(b"content-type", b"application/json; charset=utf-8")]
        headers.extend(self._security_headers())
        if extra_headers:
            headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _client_key(self, scope: dict[str, Any], headers: dict[bytes, bytes]) -> str:
        host = scope.get("client") or ("unknown", 0)
        address = str(host[0])
        if self.trust_forwarded_headers and headers.get(b"x-forwarded-for"):
            address = headers[b"x-forwarded-for"].decode("latin-1").split(",", 1)[0].strip()
        return address[:128]

    def _protected(self, path: str) -> bool:
        return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in self.protected_paths)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        path = scope.get("path", "/")
        method = scope.get("method", "GET").upper()
        origin = headers.get(b"origin", b"").decode("latin-1")

        async def secure_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                outgoing = list(message.get("headers", []))
                present = {k.lower() for k, _ in outgoing}
                outgoing.extend((k, v) for k, v in self._security_headers() if k not in present)
                if origin and origin in self.allowed_origins:
                    outgoing.extend([
                        (b"access-control-allow-origin", origin.encode("ascii")),
                        (b"vary", b"Origin"),
                        (b"access-control-allow-credentials", b"true"),
                        (b"access-control-allow-headers", b"authorization,content-type,x-csrf-token"),
                        (b"access-control-allow-methods", b"GET,POST,PUT,PATCH,DELETE,OPTIONS"),
                    ])
                message = {**message, "headers": outgoing}
            await send(message)

        if origin and origin not in self.allowed_origins:
            await self._respond(send, 403, b'{"error":"origin_not_allowed"}')
            return

        if method == "OPTIONS":
            if origin and origin in self.allowed_origins:
                await self._respond(send, 204, b"", [
                    (b"access-control-allow-origin", origin.encode("ascii")),
                    (b"access-control-allow-credentials", b"true"),
                    (b"access-control-allow-headers", b"authorization,content-type,x-csrf-token"),
                    (b"access-control-allow-methods", b"GET,POST,PUT,PATCH,DELETE,OPTIONS"),
                    (b"vary", b"Origin"),
                ])
            else:
                await self._respond(send, 204, b"")
            return

        if path == "/auth/login" and method == "POST":
            key = "login:" + self._client_key(scope, headers)
            if not await self.login_rate_limiter.allow(
                key, limit=self.login_limit, window_seconds=self.login_window_seconds,
            ):
                await self._respond(send, 429, b'{"error":"login_rate_limited"}',
                                    [(b"retry-after", str(self.login_window_seconds).encode())])
                return

        if not self.auth_enabled:
            # No-auth mode bypasses identity and CSRF gates, but deliberately keeps
            # origin allowlisting, CORS, and security response headers.
            # Seed a dummy principal so every downstream resolve_principal call
            # and authorizer check sees a non-None identity with a default tenant.
            from agent_platform.identity.models import Principal
            state = scope.setdefault("state", {})
            state["identity.authenticated"] = False
            state["identity.principal"] = Principal(
                user_id="anon",
                tenant_id="default",
                session_id="no-auth",
                email="anon@local",
                display_name="Anonymous",
            )
            await self.app(scope, receive, secure_send)
            return

        cookies = self._cookies(headers.get(b"cookie", b""))
        cookie_token = cookies.get(self.session_cookie)
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        token = bearer or cookie_token
        principal: Any = None

        if token:
            if bearer and token.startswith("svc_") and self.service_identity is not None:
                try:
                    principal = await self.service_identity.authenticate(token)
                except PermissionError:
                    principal = None
            else:
                try:
                    principal = await self.identity.authenticate(token)
                except AuthenticationError:
                    if bearer:
                        try:
                            principal = await self.identity.authenticate_api_credential(token)
                        except AuthenticationError:
                            principal = None
                    else:
                        principal = None

        if principal is None and self.local_principal is not None:
            client = scope.get("client") or ()
            if client and str(client[0]) in self.local_client_hosts:
                principal = self.local_principal()
                if inspect.isawaitable(principal):
                    principal = await principal

        if cookie_token and method not in self.SAFE_METHODS:
            csrf_cookie = cookies.get(self.csrf_cookie, "")
            csrf_header = headers.get(b"x-csrf-token", b"").decode("latin-1")
            if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                await self._respond(send, 403, b'{"error":"csrf_validation_failed"}')
                return
            if origin and origin not in self.allowed_origins:
                await self._respond(send, 403, b'{"error":"origin_not_allowed"}')
                return

        if self._protected(path) and path not in self.PUBLIC_PATHS and principal is None:
            await self._respond(send, 401, b'{"error":"authentication_required"}',
                                [(b"www-authenticate", b"Bearer")])
            return

        if principal is not None:
            scope.setdefault("state", {})["identity.principal"] = principal
            scope["state"]["identity.authenticated"] = True
        else:
            scope.setdefault("state", {})["identity.authenticated"] = False

        await self.app(scope, receive, secure_send)
