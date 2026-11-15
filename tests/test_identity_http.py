"""HTTP middleware security regression tests."""
import unittest

from agent_platform.identity.http import IdentityASGIMiddleware, InMemorySlidingWindowRateLimiter
from agent_platform.identity.service import AuthenticationError
from agent_platform.identity.models import Principal


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
