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
    def __init__(self):
        self.revocations = []

    async def upsert_page(self, *, tenant_id, connection_id, items):
        return None

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        self.revocations.append((tenant_id, connection_id, disposition))


class ConnectorApiTests(unittest.TestCase):
    def setUp(self):
        self.repositories = {}
        self.sync_queue = SyncQueue()
        self.sink = Sink()
        self.denied_permissions = set()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def service(p):
            repo = self.repositories.setdefault(p.tenant_id, InMemoryConnectorRepository())
            return DataConnectorService(
                repository=repo, providers={"drive": Provider()},
                secrets=Secrets(), sink=self.sink, sync_queue=self.sync_queue,
            )

        async def authorize(p, resource_type, resource_id, action):
            return (
                resource_type in {"connector", "project"}
                and (resource_type, resource_id, action) not in self.denied_permissions
            )

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
        self.assertEqual(revoked.json()["disposition"], "retain")
        self.assertEqual(self.sink.revocations, [("tenant-a", "drive-1", "retain")])

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

    def test_revoke_api_applies_delete_disposition_and_rejects_unknown_policy(self):
        created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-delete", "provider": "drive",
                "display_name": "Drive", "secret_ref": "secret://tenant-a/drive-delete",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(created.status_code, 201)
        revoked = self.client.post(
            "/api/platform/v1/connectors/drive-delete/revoke?disposition=delete",
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["disposition"], "delete")
        self.assertEqual(self.sink.revocations, [("tenant-a", "drive-delete", "delete")])

        invalid_created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-invalid-policy", "provider": "drive",
                "display_name": "Drive", "secret_ref": "secret://tenant-a/drive-invalid",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(invalid_created.status_code, 201)
        invalid = self.client.post(
            "/api/platform/v1/connectors/drive-invalid-policy/revoke?disposition=purge-everything",
        )
        self.assertEqual(invalid.status_code, 422)

    def test_project_resource_permission_is_required_for_sync_and_revoke(self):
        created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-project", "provider": "drive",
                "display_name": "Project Drive",
                "secret_ref": "secret://tenant-a/drive-project",
                "scopes": ["files.read"], "project_id": "project-a",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.denied_permissions.add(("project", "project-a", "sync"))
        denied_sync = self.client.post("/api/platform/v1/connectors/drive-project/sync")
        self.assertEqual(denied_sync.status_code, 403)
        self.assertEqual(self.sync_queue.enqueued, [])
        self.denied_permissions.remove(("project", "project-a", "sync"))

        self.denied_permissions.add(("project", "project-a", "revoke"))
        denied_revoke = self.client.post(
            "/api/platform/v1/connectors/drive-project/revoke?disposition=delete",
        )
        self.assertEqual(denied_revoke.status_code, 403)
        self.assertEqual(self.sink.revocations, [])
        saved = self.repositories["tenant-a"].records[("tenant-a", "drive-project")]
        self.assertEqual(saved.status, "active")

    def test_tenant_header_cannot_access_another_tenants_connection(self):
        created = self.client.post(
            "/api/platform/v1/connectors",
            json={
                "connection_id": "drive-tenant", "provider": "drive",
                "display_name": "Tenant Drive",
                "secret_ref": "secret://tenant-a/drive-tenant",
                "scopes": ["files.read"],
            },
        )
        self.assertEqual(created.status_code, 201)
        denied = self.client.post(
            "/api/platform/v1/connectors/drive-tenant/sync",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(denied.status_code, 404)

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
