"""FastAPI identity route integration tests."""
import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from agent_platform.api.identity import create_identity_router
from agent_platform.identity.http import IdentityASGIMiddleware
from agent_platform.identity.models import IssuedSession, Principal
from agent_platform.identity.service import AuthenticationError


class FakeIdentity:
    def __init__(self):
        self.logged_out = False

    async def login(self, *, email, password, tenant_id, session_ttl):
        if email != "user@example.com" or password != "correct-password":
            raise AuthenticationError("invalid")
        return IssuedSession(
            session_id="session-id", tenant_id=tenant_id, user_id="user-id",
            access_token="opaque-session-token",
            expires_at=datetime.now(timezone.utc) + session_ttl,
        )

    async def authenticate(self, token):
        if token != "opaque-session-token":
            raise AuthenticationError("invalid")
        return Principal(
            user_id="user-id", tenant_id="tenant-id", session_id="session-id",
            email="user@example.com", display_name="User",
        )

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")

    async def logout(self, principal):
        self.logged_out = True
        return True


class IdentityApiTests(unittest.TestCase):
    def test_initialize_creates_first_tenant_when_connection_factory_is_configured(self):
        try:
            from fastapi import FastAPI
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI and Starlette test client are optional platform dependencies")

        class FakeCursor:
            async def fetchone(self):
                return {"total": 0}

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def execute(self, query, params=None):
                return FakeCursor()

            async def close(self):
                return None

        identity = FakeIdentity()
        app = FastAPI()
        app.add_middleware(IdentityASGIMiddleware, identity=identity)
        app.include_router(create_identity_router(
            identity=identity,
            connection_factory=lambda: FakeConnection(),
        ))
        with patch("agent_platform.api.identity.create_tenant_with_owner", new=AsyncMock(return_value="owner-id")) as create_owner:
            with TestClient(app, base_url="https://testserver") as client:
                response = client.post("/auth/initialize", json={
                    "tenant_id": "tenant-new",
                    "tenant_name": "New Tenant",
                    "email": "owner@example.com",
                    "display_name": "Tenant Owner",
                    "password": "a-secure-password-123",
                })
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["initialized"], True)
            self.assertEqual(response.json()["tenant_id"], "tenant-new")
            create_owner.assert_awaited_once()

    def test_login_sets_secure_cookies_without_exposing_session_token(self):
        try:
            from fastapi import FastAPI
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI and Starlette test client are optional platform dependencies")

        identity = FakeIdentity()
        app = FastAPI()
        app.add_middleware(IdentityASGIMiddleware, identity=identity)
        app.include_router(create_identity_router(identity=identity))
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post("/auth/login", json={
                "email": "user@example.com", "password": "correct-password",
                "tenant_id": "tenant-id",
            })
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("opaque-session-token", response.text)
            cookies = response.headers.get_list("set-cookie")
            self.assertEqual(len(cookies), 2)
            self.assertIn("HttpOnly", cookies[0])
            self.assertIn("Secure", cookies[0])
            self.assertIn("SameSite=Strict", cookies[0])
            self.assertNotIn("HttpOnly", cookies[1])

            me = client.get("/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["user_id"], "user-id")

            csrf = client.cookies.get("__Host-platform_csrf")
            logout = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
            self.assertEqual(logout.status_code, 200)
            self.assertTrue(identity.logged_out)
            self.assertTrue(any("Max-Age=0" in value for value in logout.headers.get_list("set-cookie")))


if __name__ == "__main__":
    unittest.main()
