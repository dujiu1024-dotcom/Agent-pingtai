from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.credentials import create_credentials_router
from agent_platform.credentials.service import CredentialService
from agent_platform.identity.models import Principal


class FakeSecrets:
    def __init__(self):
        self.values = {}

    async def create(self, *, secret_ref, value):
        self.values[secret_ref] = value

    async def resolve(self, *, secret_ref):
        return self.values.get(secret_ref)

    async def revoke(self, *, secret_ref):
        self.values.pop(secret_ref, None)


class FakeRepository:
    def __init__(self):
        self.credentials = {}
        self.grants = {}

    async def create(self, item):
        self.credentials[(item.tenant_id, item.credential_id)] = item

    async def get(self, tenant_id, credential_id):
        return self.credentials.get((tenant_id, credential_id))

    async def list(self, tenant_id, *, limit=100):
        return [v for (t, _), v in self.credentials.items() if t == tenant_id][:limit]

    async def grant(self, item):
        self.grants[(item.tenant_id, item.credential_id, item.grantee_type, item.grantee_id)] = item

    async def list_grants(self, tenant_id, credential_id):
        return [v for k, v in self.grants.items() if k[:2] == (tenant_id, credential_id)]

    async def get_grant(self, tenant_id, credential_id, grantee_type, grantee_id):
        return self.grants.get((tenant_id, credential_id, grantee_type, grantee_id))

    async def revoke(self, tenant_id, credential_id, at):
        from dataclasses import replace
        key = (tenant_id, credential_id)
        item = self.credentials.get(key)
        if item is None or item.status != "active":
            return False
        self.credentials[key] = replace(item, status="revoked", revoked_at=at)
        for grant_key, grant in list(self.grants.items()):
            if grant_key[:2] == key:
                self.grants[grant_key] = replace(grant, revoked_at=at)
        return True


class Audit:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


class CredentialApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.secrets = FakeSecrets()
        self.audit = Audit()
        self.service = CredentialService(
            repository=self.repository, secret_manager=self.secrets, audit_writer=self.audit,
        )
        app = FastAPI()
        app.include_router(create_credentials_router(
            resolve_service=lambda actor: self.service,
            authorize=lambda actor, resource_type, resource_id, action: (
                actor.tenant_id == "tenant-a"
                and (resource_type != "workflow" or resource_id == "workflow-a")
            ),
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-a", "tenant-a", "session-a", "a@example.test", "A"),
            )
            return await call_next(request)

        self.client = TestClient(app)

    def test_create_returns_metadata_only_and_tenant_is_server_derived(self):
        response = self.client.post("/api/platform/v1/credentials", json={
            "credential_id": "drive-main", "provider": "google_drive",
            "display_name": "Drive", "value": "plaintext-token",
            "scopes": ["files.read", "files.write"],
        })
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("plaintext-token", response.text)
        self.assertNotIn("secret_ref", response.json())
        self.assertEqual(response.json()["credential_id"], "drive-main")
        self.assertIn("secret://tenant-a/credentials/drive-main", self.secrets.values)

    def test_grant_requires_target_resource_authorization(self):
        self.client.post("/api/platform/v1/credentials", json={
            "credential_id": "drive-main", "provider": "google_drive",
            "display_name": "Drive", "value": "plaintext-token",
            "scopes": ["files.read"],
        })
        denied = self.client.post("/api/platform/v1/credentials/drive-main/grants", json={
            "grantee_type": "workflow", "grantee_id": "workflow-other",
            "scopes": ["files.read"],
        })
        self.assertEqual(denied.status_code, 403)
        allowed = self.client.post("/api/platform/v1/credentials/drive-main/grants", json={
            "grantee_type": "workflow", "grantee_id": "workflow-a",
            "scopes": ["files.read"],
        })
        self.assertEqual(allowed.status_code, 201)

    def test_list_and_get_never_expose_secret_reference_or_plaintext(self):
        self.client.post("/api/platform/v1/credentials", json={
            "credential_id": "drive-main", "provider": "google_drive",
            "display_name": "Drive", "value": "plaintext-token", "scopes": ["files.read"],
        })
        listing = self.client.get("/api/platform/v1/credentials")
        detail = self.client.get("/api/platform/v1/credentials/drive-main")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("plaintext-token", listing.text + detail.text)
        self.assertNotIn("secret_ref", listing.text + detail.text)

    def test_revoke_disables_credential_and_removes_secret(self):
        self.client.post("/api/platform/v1/credentials", json={
            "credential_id": "drive-main", "provider": "google_drive",
            "display_name": "Drive", "value": "plaintext-token", "scopes": ["files.read"],
        })
        response = self.client.delete("/api/platform/v1/credentials/drive-main")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "revoked")
        self.assertEqual(self.secrets.values, {})


if __name__ == "__main__":
    unittest.main()
