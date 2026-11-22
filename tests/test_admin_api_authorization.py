from __future__ import annotations

import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_platform.api.admin import create_admin_router
from agent_platform.identity.models import Principal


class FakeIdentityRepository:
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.calls = []

    async def has_permission(self, **kwargs):
        self.calls.append(kwargs)
        return self.allowed


class FakeCursor:
    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, query_log):
        self.query_log = query_log

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, query, params=()):
        self.query_log.append((query, params))
        return FakeCursor()

    async def close(self):
        return None


class AdminApiAuthorizationTests(unittest.TestCase):
    ROUTES = (
        "/api/platform/v1/admin/organization",
        "/api/platform/v1/admin/access-control",
        "/api/platform/v1/admin/settings",
    )

    def _client(self, *, allowed: bool, authenticated: bool = True):
        repository = FakeIdentityRepository(allowed)
        query_log = []

        async def connection_factory():
            return FakeConnection(query_log)

        app = FastAPI()
        principal = Principal(
            user_id="user-member",
            tenant_id="tenant-a",
            session_id="session-a",
            email="member@example.com",
            display_name="Member",
        )

        @app.middleware("http")
        async def inject_test_identity(request: Request, call_next):
            if authenticated:
                request.state.identity = type(
                    "IdentityState", (), {"principal": principal}
                )()
            return await call_next(request)

        app.include_router(create_admin_router(
            connection_factory=connection_factory,
            identity_repository=repository,
        ))
        return TestClient(app), repository, query_log

    def test_non_admin_gets_403_on_every_admin_endpoint_before_database_access(self):
        client, repository, query_log = self._client(allowed=False)
        for path in self.ROUTES:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(len(repository.calls), len(self.ROUTES))
        self.assertEqual(query_log, [])
        self.assertTrue(all(
            call["resource_type"] == "tenant"
            and call["resource_id"] == "tenant-a"
            and call["permission_key"] == "tenant.manage"
            for call in repository.calls
        ))

    def test_tenant_admin_can_read_all_three_admin_endpoints(self):
        client, repository, query_log = self._client(allowed=True)
        for path in self.ROUTES:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("items", response.json())
        self.assertEqual(len(repository.calls), len(self.ROUTES))
        self.assertGreater(len(query_log), 0)

    def test_unauthenticated_request_gets_401(self):
        client, repository, query_log = self._client(
            allowed=False, authenticated=False,
        )
        response = client.get(self.ROUTES[0])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(repository.calls, [])
        self.assertEqual(query_log, [])


if __name__ == "__main__":
    unittest.main()
