from __future__ import annotations

import unittest

from agent_platform.knowledge.contracts import Document, KnowledgeScope
from agent_platform.knowledge.google_drive_connector import (
    GoogleDriveConnectorError,
    GoogleDriveKnowledgeConnector,
)


class Credentials:
    async def resolve(self, secret_ref):
        self.secret_ref = secret_ref
        return "google-access-token"


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

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def document_factory(tenant_id, metadata, media_type):
    return Document(
        tenant_id=tenant_id,
        title=metadata["name"],
        source_uri=metadata.get("webViewLink") or f"https://drive.google.com/open?id={metadata['id']}",
        content_hash="pending",
        scope=KnowledgeScope.PRIVATE,
        owner_user_id="mapped-platform-owner",
        document_id=f"gdrive:{metadata['id']}",
        media_type=media_type,
        metadata={"source": "google-drive", "source_modified_time": metadata.get("modifiedTime")},
    )


class GoogleDriveKnowledgeConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_snapshot_then_delta_deletion(self):
        client = Client([
            Response({"startPageToken": "start-1"}),
            Response({
                "files": [{
                    "id": "file-doc", "name": "Design doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-11-16T10:00:00Z",
                    "webViewLink": "https://drive.google.com/open?id=file-doc",
                }],
            }),
            Response(content=b"docx-binary"),
            Response({
                "changes": [{"fileId": "deleted-file", "removed": True}],
                "newStartPageToken": "start-2",
            }),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        initial = await connector.list_changed(
            tenant_id="tenant-a", cursor=None, limit=50,
        )
        self.assertEqual(len(initial.items), 1)
        file_fields = client.calls[1][2]["params"]["fields"]
        self.assertIn("permissions(id,type,role,emailAddress,domain,allowFileDiscovery)", file_fields)
        self.assertNotIn("('", file_fields)
        self.assertEqual(initial.items[0].document.document_id, "gdrive:file-doc")
        self.assertEqual(
            initial.items[0].document.media_type,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(initial.items[0].content, b"docx-binary")
        self.assertFalse(initial.has_more)
        self.assertEqual(
            client.calls[2][1],
            "https://www.googleapis.com/drive/v3/files/file-doc/export",
        )
        delta = await connector.list_changed(
            tenant_id="tenant-a", cursor=initial.next_cursor, limit=50,
        )
        self.assertEqual(delta.deleted_document_ids, ("gdrive:deleted-file",))
        self.assertEqual(delta.next_cursor, connector._encode_cursor({
            "mode": "changes", "token": "start-2",
        }))

    async def test_initial_snapshot_paginates_without_losing_delta_watermark(self):
        client = Client([
            Response({"startPageToken": "watermark"}),
            Response({
                "files": [{
                    "id": "first", "name": "First", "mimeType": "text/plain",
                }],
                "nextPageToken": "files-page-2",
            }),
            Response(content=b"first"),
            Response({
                "files": [{
                    "id": "second", "name": "Second", "mimeType": "text/plain",
                }],
            }),
            Response(content=b"second"),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        first = await connector.list_changed(tenant_id="tenant-a", cursor=None)
        self.assertTrue(first.has_more)
        state = connector._decode_cursor(first.next_cursor)
        self.assertEqual(state["mode"], "initial")
        self.assertEqual(state["page_token"], "files-page-2")
        self.assertEqual(state["change_token"], "watermark")
        second = await connector.list_changed(
            tenant_id="tenant-a", cursor=first.next_cursor,
        )
        self.assertFalse(second.has_more)
        self.assertEqual(second.items[0].document.document_id, "gdrive:second")
        final_state = connector._decode_cursor(second.next_cursor)
        self.assertEqual(final_state, {"mode": "changes", "token": "watermark"})

    async def test_change_feed_pagination_preserves_next_page_token(self):
        cursor = GoogleDriveKnowledgeConnector._encode_cursor({
            "mode": "changes", "token": "change-start",
        })
        client = Client([
            Response({
                "changes": [],
                "nextPageToken": "change-page-2",
            }),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        page = await connector.list_changed(
            tenant_id="tenant-a", cursor=cursor, limit=20,
        )
        self.assertEqual(
            connector._decode_cursor(page.next_cursor),
            {"mode": "changes", "token": "change-page-2"},
        )
        self.assertTrue(page.has_more)

    async def test_signed_download_redirect_does_not_receive_oauth_token(self):
        client = Client([
            Response({"startPageToken": "start"}),
            Response({"files": [{
                "id": "binary-file", "name": "notes.txt", "mimeType": "text/plain",
            }]}),
            Response(
                headers={"location": "https://download.googleusercontent.com/signed"},
                status_code=302,
            ),
            Response(content=b"plain text"),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        page = await connector.list_changed(
            tenant_id="tenant-a", cursor=None, limit=20,
        )
        self.assertEqual(page.items[0].content, b"plain text")
        self.assertNotIn("Authorization", client.calls[3][2].get("headers", {}))
        self.assertFalse(client.calls[3][2]["follow_redirects"])

    async def test_rejects_unsafe_download_redirect(self):
        client = Client([
            Response({"startPageToken": "start"}),
            Response({"files": [{
                "id": "binary-file", "name": "notes.txt", "mimeType": "text/plain",
            }]}),
            Response(
                headers={"location": "https://attacker.example/collect"},
                status_code=302,
            ),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        with self.assertRaises(GoogleDriveConnectorError):
            await connector.list_changed(
                tenant_id="tenant-a", cursor=None, limit=20,
            )

    async def test_document_factory_must_preserve_tenant_and_stable_id(self):
        client = Client([
            Response({"startPageToken": "start"}),
            Response({"files": [{
                "id": "file-doc", "name": "Design doc", "mimeType": "text/plain",
            }]}),
            Response(content=b"plain text"),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=lambda tenant, meta, mime: document_factory(
                "other-tenant", meta, mime,
            ),
        )
        with self.assertRaises(GoogleDriveConnectorError):
            await connector.list_changed(
                tenant_id="tenant-a", cursor=None, limit=20,
            )

    async def test_revokes_google_oauth_token(self):
        client = Client([Response()])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        await connector.revoke(
            tenant_id="tenant-a", credential_ref="secret://tenant/drive",
        )
        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://oauth2.googleapis.com/revoke")
        self.assertEqual(kwargs["data"], {"token": "google-access-token"})
        self.assertNotIn("Authorization", kwargs["headers"])


if __name__ == "__main__":
    unittest.main()

    async def test_incomplete_initial_snapshot_fails_closed(self):
        client = Client([
            Response({"startPageToken": "start"}),
            Response({"files": [], "incompleteSearch": True}),
        ])
        connector = GoogleDriveKnowledgeConnector(
            client=client, credentials=Credentials(),
            credential_ref="secret://tenant/drive",
            document_factory=document_factory,
        )
        with self.assertRaises(GoogleDriveConnectorError):
            await connector.list_changed(tenant_id="tenant-a", cursor=None)
