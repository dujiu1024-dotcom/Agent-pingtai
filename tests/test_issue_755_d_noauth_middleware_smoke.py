"""No-auth middleware smoke coverage for the P0 dual-mode API boundary.

These tests exercise the real ASGI identity middleware without replacing
application routers. They prove that all protected API namespaces share the
same instance-mode identity bypass while CORS and security headers remain on.
They do not claim that each optional business router is configured or healthy.
"""
from __future__ import annotations

import unittest

from agent_platform.identity.http import IdentityASGIMiddleware
from agent_platform.identity.models import Principal
from agent_platform.identity.service import AuthenticationError


class _Identity:
    async def authenticate(self, token):
        raise AuthenticationError("invalid")

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")


class NoAuthApiBoundarySmokeTests(unittest.IsolatedAsyncioTestCase):
    API_PATHS = (
        "/api/platform/v1/memories",
        "/api/platform/v1/registry/agents",
        "/api/platform/v1/blackboard/entries",
        "/api/platform/v1/observability/metrics",
        "/api/platform/v1/audit/tool-events",
        "/api/platform/v1/connectors",
    )

    async def _request(self, middleware, *, path, method="GET", headers=()):
        sent = []
        observed_state = {}

        async def app(scope, receive, send):
            observed_state.update(scope.get("state", {}))
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "path": path,
            "method": method,
            "headers": list(headers),
            "client": ("192.0.2.10", 4321),
            "state": {},
        }
        middleware.app = app
        await middleware(scope, receive, send)
        return sent, observed_state

    async def test_all_p0_p1_module_namespaces_bypass_identity_in_instance_mode(self):
        middleware = IdentityASGIMiddleware(
            None,
            identity=None,
            auth_enabled=False,
            allowed_origins=("https://console.example.com",),
            protected_paths=("/api", "/auth"),
        )

        for path in self.API_PATHS:
            with self.subTest(path=path):
                sent, state = await self._request(middleware, path=path)
                self.assertEqual(sent[0]["status"], 200)
                principal = state["identity.principal"]
                self.assertIsInstance(principal, Principal)
                self.assertEqual(principal.user_id, "anon")
                self.assertEqual(principal.tenant_id, "default")
                self.assertEqual(principal.session_id, "no-auth")
                self.assertFalse(state["identity.authenticated"])
                response_headers = dict(sent[0]["headers"])
                self.assertEqual(response_headers[b"x-content-type-options"], b"nosniff")

    async def test_no_auth_mutations_do_not_require_cookie_csrf_tokens(self):
        middleware = IdentityASGIMiddleware(
            None, identity=None, auth_enabled=False,
            protected_paths=("/api", "/auth"),
        )
        sent, state = await self._request(
            middleware, path="/api/platform/v1/connectors", method="POST",
        )
        self.assertEqual(sent[0]["status"], 200)
        self.assertFalse(state["identity.authenticated"])

    async def test_no_auth_keeps_origin_allowlist_enforced(self):
        middleware = IdentityASGIMiddleware(
            None, identity=None, auth_enabled=False,
            allowed_origins=("https://console.example.com",),
        )
        sent, _ = await self._request(
            middleware,
            path="/api/platform/v1/memories",
            headers=((b"origin", b"https://untrusted.example"),),
        )
        self.assertEqual(sent[0]["status"], 403)

    async def test_auth_enabled_still_rejects_missing_credentials(self):
        middleware = IdentityASGIMiddleware(
            None, identity=_Identity(), auth_enabled=True,
            protected_paths=("/api", "/auth"),
        )
        sent, state = await self._request(
            middleware, path="/api/platform/v1/memories",
        )
        self.assertEqual(sent[0]["status"], 401)
        self.assertNotIn("identity.principal", state)


if __name__ == "__main__":
    unittest.main()
