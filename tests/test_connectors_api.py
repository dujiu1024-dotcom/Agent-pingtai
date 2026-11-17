from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.connectors import create_connectors_router
from agent_platform.control_plane.data_connectors import (
    ConnectorPage, DataConnectorService, InMemoryConnectorRepository,
)
from agent_platform.identity.models import Principal


class Provider:
    async def fetch_page(self, *, credential, scopes, cursor):
        return ConnectorPage((), cursor, False)

    async def revoke(self, *, credential):
        return None


class Secrets:
    async def resolve(self, secret_ref):
        return "runtime-secret"


class SyncQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, *, tenant_id, connection_id):
        self.enqueued.append((tenant_id, connection_id))


class Sink:
    async def upsert_page(self, *, tenant_id, connection_id, items):
        return None

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        return None


class ConnectorApiTests(unittest.TestCase):
    def setUp(self):
        self.repositories = {}
        self.sync_queue = SyncQueue()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def service(p):
            repo = self.repositories.setdefault(p.tenant_id, InMemoryConnectorRepository())
            return DataConnectorService(
                repository=repo, providers={"drive": Provider()},
                secrets=Secrets(), sink=Sink(), sync_queue=self.sync_queue,
            )

        async def authorize(p, resource_type, resource_id, action):
            return resource_type == "connector"

        app = FastAPI()
        app.include_router(create_connectors_router(
            resolve_principal=principal, resolve_service=service, authorize=authorize,
        ))
        self.client = TestClient(app)

    def test_register_and_revoke_connector(self):
        created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-1", "provider": "drive",
                "display_name": "Shared Drive",
                "secret_ref": "secret://tenant-a/drive-1",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["display_name"], "Shared Drive")
        self.assertNotIn("secret_ref", created.json())
        revoked = self.client.post("/api/platform/v1/connectors/drive-1/revoke")
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["status"], "revoked")

    def test_sync_request_is_authorized_and_enqueued(self):
        created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-sync", "provider": "drive",
                "display_name": "Drive", "secret_ref": "secret://tenant-a/drive-sync",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(created.status_code, 201)
        queued = self.client.post("/api/platform/v1/connectors/drive-sync/sync")
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(self.sync_queue.enqueued, [("tenant-a", "drive-sync")])

    def test_plaintext_secret_reference_is_rejected(self):
        response = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-2", "provider": "drive",
                "display_name": "Unsafe", "secret_ref": "plain-token",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
