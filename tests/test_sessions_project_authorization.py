from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.sessions import create_sessions_router
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)
from agent_platform.identity.models import Principal


class SessionProjectAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemorySessionRepository()
        self.service = SessionAssetService(self.repository, InMemoryObjectStorage())
        self.project_read = True
        self.project_write = True
        self.file_read = True

        async def resolve_principal(request):
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="auth-session",
                email="user@example.test", display_name="User",
            )

        async def resolve_service(principal):
            return self.service

        async def authorize(principal, resource_type, resource_id, action):
            if resource_type == "session":
                return True
            if resource_type == "file" and action == "read":
                return self.file_read
            if resource_type == "project":
                if action == "read":
                    return self.project_read
                if action == "write":
                    return self.project_write
            return True

        app = FastAPI()
        app.include_router(create_sessions_router(
            resolve_principal=resolve_principal,
            resolve_service=resolve_service,
            authorize=authorize,
        ))
        self.client = TestClient(app)
        self.session = asyncio.run(self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
        ))

    def test_list_messages_requires_linked_project_read_permission(self):
        self.project_read = False
        response = self.client.get(
            f"/api/platform/v1/sessions/{self.session.session_id}/messages",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("linked project", response.json()["detail"])

    def test_append_message_requires_linked_project_write_permission(self):
        self.project_write = False
        response = self.client.post(
            f"/api/platform/v1/sessions/{self.session.session_id}/messages",
            json={"role": "user", "content": "must be denied"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("linked project", response.json()["detail"])
        self.assertEqual(
            asyncio.run(self.repository.list_messages("tenant-a", self.session.session_id)),
            (),
        )

    def _upload_project_file(self):
        return asyncio.run(self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="report.txt",
            media_type="text/plain", content=b"project file content",
            session_id=self.session.session_id,
        ))

    def test_file_metadata_requires_linked_project_read_permission(self):
        asset = self._upload_project_file()
        self.project_read = False
        response = self.client.get(
            f"/api/platform/v1/files/{asset.asset_id}/metadata",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("linked project", response.json()["detail"])

    def test_file_metadata_returns_safe_fields_after_resource_acl_checks(self):
        asset = self._upload_project_file()
        response = self.client.get(
            f"/api/platform/v1/files/{asset.asset_id}/metadata",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["asset_id"], asset.asset_id)
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["project_id"], "project-a")
        self.assertNotIn("object_key", body)
        self.assertNotIn("storage_url", body)

    def test_file_metadata_requires_file_read_permission(self):
        asset = self._upload_project_file()
        self.file_read = False
        response = self.client.get(
            f"/api/platform/v1/files/{asset.asset_id}/metadata",
        )
        self.assertEqual(response.status_code, 403)
