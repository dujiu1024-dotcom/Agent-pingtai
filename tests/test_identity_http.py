"""HTTP middleware security regression tests."""
import unittest

from agent_platform.identity.http import (
    IdentityASGIMiddleware,
    InMemorySlidingWindowRateLimiter,
    auth_cookie_headers,
    clear_auth_cookie_headers,
)
from agent_platform.identity.models import Principal
from agent_platform.identity.service import AuthenticationError


class FakeIdentity:
    async def authenticate(self, token):
        if token != "valid-session":
            raise AuthenticationError("invalid")
        return Principal("user-1", "tenant-1", "session-1", "a@example.com", "A")

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")


class IdentityHttpMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def _request(self, middleware, *, path="/api/private", method="GET",
                       headers=None, client=("127.0.0.1", 1234)):
        sent = []
        scope = {"type":"http", "path":path, "method":method,
                 "headers":headers or [], "client":client, "state":{}}
        async def receive():
            return {"type":"http.request", "body":b"", "more_body":False}
        async def send(message):
            sent.append(message)
        await middleware(scope, receive, send)
        return sent, scope

    async def test_protected_path_requires_authentication(self):
        async def app(scope, receive, send):
            await send({"type":"http.response.start","status":200,"headers":[]})
            await send({"type":"http.response.body","body":b"ok"})
        middleware = IdentityASGIMiddleware(app, identity=FakeIdentity())
        sent, _ = await self._request(middleware)
        self.assertEqual(sent[0]["status"], 401)

    async def test_auth_enabled_requires_non_nullable_identity_service(self):
        async def app(scope, receive, send):
            pass
        with self.assertRaisesRegex(ValueError, "identity service is required"):
            IdentityASGIMiddleware(app, identity=None, auth_enabled=True)

    async def test_bearer_session_populates_principal_and_security_headers(self):
        async def app(scope, receive, send):
            await send({"type":"http.response.start","status":200,"headers":[]})
            await send({"type":"http.response.body","body":b"ok"})
        middleware = IdentityASGIMiddleware(app, identity=FakeIdentity())
        sent, scope = await self._request(
            middleware, headers=[(b"authorization", b"Bearer valid-session")],
        )
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(scope["state"]["identity.principal"].user_id, "user-1")
        names = {k for k, _ in sent[0]["headers"]}
        self.assertIn(b"x-content-type-options", names)
        self.assertIn(b"strict-transport-security", names)

    async def test_cookie_mutation_requires_matching_csrf_token(self):
        async def app(scope, receive, send):
            await send({"type":"http.response.start","status":200,"headers":[]})
            await send({"type":"http.response.body","body":b"ok"})
        middleware = IdentityASGIMiddleware(app, identity=FakeIdentity())
        sent, _ = await self._request(
            middleware, method="POST",
            headers=[(b"cookie", b"__Host-platform_session=valid-session")],
        )
        self.assertEqual(sent[0]["status"], 403)
        sent, _ = await self._request(
            middleware, method="POST",
            headers=[
                (b"cookie", b"__Host-platform_session=valid-session; __Host-platform_csrf=abc"),
                (b"x-csrf-token", b"abc"),
            ],
        )
        self.assertEqual(sent[0]["status"], 200)

    async def test_disallowed_origin_is_rejected(self):
        async def app(scope, receive, send):
            await send({"type":"http.response.start","status":200,"headers":[]})
            await send({"type":"http.response.body","body":b"ok"})
        middleware = IdentityASGIMiddleware(
            app, identity=FakeIdentity(),
            allowed_origins=("https://console.example.com",),
        )
        sent, _ = await self._request(
            middleware, headers=[(b"origin", b"https://evil.example")],
        )
        self.assertEqual(sent[0]["status"], 403)


    async def test_cookie_helpers_set_secure_flags_and_clear_both_cookies(self):
        headers = auth_cookie_headers(
            session_token="session", csrf_token="csrf", max_age_seconds=3600,
        )
        self.assertEqual(len(headers), 2)
        session = headers[0][1].decode()
        csrf = headers[1][1].decode()
        self.assertIn("HttpOnly", session)
        self.assertIn("Secure", session)
        self.assertIn("SameSite=Strict", session)
        self.assertNotIn("HttpOnly", csrf)
        cleared = clear_auth_cookie_headers()
        self.assertEqual(len(cleared), 2)
        self.assertTrue(all(b"Max-Age=0" in value for _, value in cleared))

    async def test_cookie_helpers_reject_header_injection(self):
        with self.assertRaises(ValueError):
            auth_cookie_headers(
                session_token="session; Secure=false",
                csrf_token="csrf",
                max_age_seconds=60,
            )
        with self.assertRaises(ValueError):
            auth_cookie_headers(
                session_token="session",
                csrf_token="csrf",
                max_age_seconds=60,
                session_cookie="__Host-session\r\nSet-Cookie",
            )

    async def test_login_rate_limit_returns_429(self):
        limiter = InMemorySlidingWindowRateLimiter()
        async def app(scope, receive, send):
            await send({"type":"http.response.start","status":200,"headers":[]})
            await send({"type":"http.response.body","body":b"ok"})
        middleware = IdentityASGIMiddleware(
            app, identity=FakeIdentity(), login_rate_limiter=limiter,
            login_limit=1, login_window_seconds=60,
        )
        sent, _ = await self._request(middleware, path="/auth/login", method="POST")
        self.assertEqual(sent[0]["status"], 200)
        sent, _ = await self._request(middleware, path="/auth/login", method="POST")
        self.assertEqual(sent[0]["status"], 429)


if __name__ == "__main__":
    unittest.main()
