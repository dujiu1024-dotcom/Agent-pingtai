from __future__ import annotations

import unittest

from agent_platform.knowledge.contracts import Document, KnowledgeScope
from agent_platform.knowledge.sharepoint_connector import (
    MicrosoftGraphConnectorError,
    SharePointKnowledgeConnector,
)


class Credentials:
    async def resolve(self, secret_ref):
        self.secret_ref = secret_ref
        return "graph-access-token"


class Response:
    def __init__(self, payload=None, *, content=b"", headers=None, status_code=200):
        self.payload = payload or {}
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def document_factory(tenant_id, drive_id, metadata, media_type):
    permissions = metadata["permissions"]
    if not permissions:
        raise ValueError("cannot map empty SharePoint ACL")
    return Document(
        tenant_id=tenant_id,
        title=metadata["name"],
        source_uri=metadata["webUrl"],
        content_hash="pending",
        scope=KnowledgeScope.PRIVATE,
        owner_user_id="mapped-platform-owner",
        document_id=f"sharepoint:{drive_id}:{metadata['id']}",
        media_type=media_type,
        metadata={
            "source": "sharepoint",
            "source_permissions": permissions,
            "source_modified_time": metadata.get("lastModifiedDateTime"),
            "_knowledge_acl": [],
        },
    )


class SharePointKnowledgeConnectorTests(unittest.IsolatedAsyncioTestCase):
    def make_connector(self, client, revoked=None):
        async def revoke(ref):
            revoked.append(ref)

        return SharePointKnowledgeConnector(
            client=client,
            credentials=Credentials(),
            credential_ref="secret://tenant/sharepoint",
            drive_id="drive-123",
            document_factory=document_factory,
            credential_revoker=revoke,
        )

    async def test_initial_delta_downloads_file_and_maps_source_permissions(self):
        client = Client([
            Response({"value": [{
                "id": "item-1", "name": "Policy.docx", "size": 12,
                "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                "webUrl": "https://tenant.sharepoint.com/sites/legal/Policy.docx",
                "lastModifiedDateTime": "2026-11-16T10:00:00Z",
                "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/download/signed",
            }], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/drive-123/root/delta?token=delta-1"}),
            Response({"value": [{"id": "permission-1", "roles": ["read"], "grantedToV2": {"user": {"id": "user-1"}}}]}),
            Response(content=b"office-document"),
        ])
        connector = self.make_connector(client, [])
        page = await connector.list_changed(tenant_id="tenant-a", cursor=None)
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].document.document_id, "sharepoint:drive-123:item-1")
        self.assertEqual(page.items[0].content, b"office-document")
        self.assertEqual(page.items[0].document.metadata["source_permissions"][0]["roles"], ["read"])
        self.assertFalse(page.has_more)
        self.assertEqual(
            client.calls[1][1],
            "https://graph.microsoft.com/v1.0/drives/drive-123/items/item-1/permissions",
        )
        self.assertNotIn("Authorization", client.calls[2][2].get("headers", {}))
        self.assertEqual(
            connector._decode_cursor(page.next_cursor),
            "https://graph.microsoft.com/v1.0/drives/drive-123/root/delta?token=delta-1",
        )

    async def test_delta_tombstones_and_pagination(self):
        client = Client([
            Response({"value": [{"id": "deleted-1", "deleted": {}}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive-123/root/delta?$skiptoken=next"}),
        ])
        connector = self.make_connector(client, [])
        page = await connector.list_changed(tenant_id="tenant-a", cursor=None)
        self.assertEqual(page.deleted_document_ids, ("sharepoint:drive-123:deleted-1",))
        self.assertTrue(page.has_more)
        self.assertEqual(
            connector._decode_cursor(page.next_cursor),
            "https://graph.microsoft.com/v1.0/drives/drive-123/root/delta?$skiptoken=next",
        )

    async def test_rejects_untrusted_delta_link(self):
        client = Client([
            Response({"value": [], "@odata.nextLink": "https://attacker.example/steal"}),
        ])
        connector = self.make_connector(client, [])
        with self.assertRaises(MicrosoftGraphConnectorError):
            await connector.list_changed(tenant_id="tenant-a", cursor=None)

    async def test_rejects_delta_cursor_for_another_drive(self):
        cursor = SharePointKnowledgeConnector._encode_cursor(
            "https://graph.microsoft.com/v1.0/drives/another-drive/root/delta?token=foreign"
        )
        connector = self.make_connector(Client([]), [])
        with self.assertRaises(MicrosoftGraphConnectorError):
            await connector.list_changed(tenant_id="tenant-a", cursor=cursor)

    async def test_rejects_untrusted_signed_download_host(self):
        client = Client([
            Response({"value": [{
                "id": "item-2", "name": "Secret.txt",
                "file": {"mimeType": "text/plain"},
                "@microsoft.graph.downloadUrl": "https://attacker.example/steal",
            }], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/drive-123/root/delta?token=delta"}),
            Response({"value": [{"id": "permission-2", "roles": ["read"]}]}),
        ])
        connector = self.make_connector(client, [])
        with self.assertRaises(MicrosoftGraphConnectorError):
            await connector.list_changed(tenant_id="tenant-a", cursor=None)

    async def test_revoke_delegates_to_host_identity_lifecycle(self):
        revoked = []
        connector = self.make_connector(Client([]), revoked)
        await connector.revoke(
            tenant_id="tenant-a", credential_ref="secret://tenant/sharepoint",
        )
        self.assertEqual(revoked, ["secret://tenant/sharepoint"])

    async def test_revoke_rejects_another_tenant_secret_reference(self):
        connector = self.make_connector(Client([]), [])
        with self.assertRaises(PermissionError):
            await connector.revoke(
                tenant_id="tenant-a", credential_ref="secret://other/sharepoint",
            )


if __name__ == "__main__":
    unittest.main()
