"""Google Drive Changes API adapter for the existing KnowledgeConnector contract.

The first call snapshots existing files and captures a Changes API watermark.
Subsequent calls consume deltas from that watermark. OAuth access tokens are
resolved from the existing secret reference at execution time. A host-owned
document factory must map source sharing policy into platform ACLs and fail
closed when it cannot represent the source policy safely.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlsplit

from agent_platform.connectors.base import BaseConnector, ConnectorAuthenticator
from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage
from agent_platform.knowledge.contracts import Document


class GoogleDriveConnectorError(RuntimeError):
    """Google Drive returned an unsafe or unusable response."""


class GoogleDriveCredentialResolver(Protocol):
    async def resolve(self, secret_ref: str) -> str: ...


DocumentFactory = Callable[[str, Mapping[str, Any], str], Document]


class GoogleDriveKnowledgeConnector(BaseConnector):
    """Incremental Google Drive connector using initial listing plus Changes API."""

    connector_id = "google-drive"
    _API = "https://www.googleapis.com/drive/v3"
    _EXPORTS = {
        "application/vnd.google-apps.document": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        ),
        "application/vnd.google-apps.spreadsheet": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        ),
        "application/vnd.google-apps.presentation": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pptx",
        ),
        "application/vnd.google-apps.drawing": ("application/pdf", "pdf"),
    }
    _FILE_FIELDS = (
        "id,name,mimeType,modifiedTime,webViewLink,description,md5Checksum,"
        "size,trashed,parents,owners(displayName,emailAddress),"
        "permissions(id,type,role,emailAddress,domain,allowFileDiscovery)"
    )

    def __init__(
        self,
        *,
        client: Any,
        credentials: GoogleDriveCredentialResolver,
        credential_ref: str,
        document_factory: DocumentFactory,
        max_file_bytes: int = 25 * 1024 * 1024,
        page_size: int = 100,
        authenticator: ConnectorAuthenticator | None = None,
        auth_scopes: tuple[str, ...] = ("files.read",),
    ) -> None:
        if not credential_ref.startswith("secret://"):
            raise ValueError("Google Drive credentials must use a secret:// reference")
        if (
            isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int)
            or max_file_bytes < 1024 or isinstance(page_size, bool)
            or not isinstance(page_size, int) or not 1 <= page_size <= 1000
        ):
            raise ValueError("invalid Google Drive size or page limit")
        super().__init__(
            connector_id=self.connector_id, credential_ref=credential_ref,
            credentials=credentials, authenticator=authenticator, auth_scopes=auth_scopes,
        )
        self.client = client
        self.document_factory = document_factory
        self.max_file_bytes = max_file_bytes
        self.page_size = page_size

    async def _token(self, tenant_id: str) -> str:
        try:
            return await self.authenticate(tenant_id=tenant_id)
        except Exception as exc:
            raise GoogleDriveConnectorError("Google Drive OAuth credential is unavailable") from exc

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleDriveConnectorError("Google Drive response must be a JSON object")
        return payload

    @staticmethod
    def _encode_cursor(state: Mapping[str, str | None]) -> str:
        raw = json.dumps(dict(state), separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded.encode()))
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise GoogleDriveConnectorError("invalid Google Drive sync cursor") from exc
        if not isinstance(value, dict) or value.get("mode") not in {"initial", "changes"}:
            raise GoogleDriveConnectorError("invalid Google Drive sync cursor")
        return value

    async def _api_get(
        self, path: str, *, token: str, params: Mapping[str, Any] | None = None,
    ) -> Any:
        response = await self.client.get(
            self._API + path,
            params=dict(params or {}),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            follow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise GoogleDriveConnectorError("unexpected redirect from Google Drive API")
        return response

    async def _download(
        self, *, token: str, file_id: str, export_extension: str | None,
    ) -> bytes:
        encoded_id = quote(file_id, safe="")
        if export_extension:
            path = f"/files/{encoded_id}/export"
            params = {
                "mimeType": next(
                    mime for mime, (_, extension) in self._EXPORTS.items()
                    if extension == export_extension
                )
            }
        else:
            path = f"/files/{encoded_id}"
            params = {"alt": "media"}
        response = await self.client.get(
            self._API + path,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            parsed = urlsplit(location)
            host = (parsed.hostname or "").lower()
            allowed = (
                host == "drive.usercontent.google.com"
                or host.endswith(".googleusercontent.com")
            )
            if (
                parsed.scheme != "https" or not allowed
                or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443)
            ):
                raise GoogleDriveConnectorError("Google Drive returned an unsafe download redirect")
            # Signed download URLs are bearer capabilities; never forward OAuth.
            response = await self.client.get(
                location, follow_redirects=False,
                headers={"Accept": "application/octet-stream"},
            )
            if 300 <= response.status_code < 400:
                raise GoogleDriveConnectorError("nested Google Drive download redirects are not allowed")
        response.raise_for_status()
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_file_bytes:
                    raise GoogleDriveConnectorError("Google Drive file exceeds the size limit")
            except ValueError as exc:
                raise GoogleDriveConnectorError("invalid Google Drive content length") from exc
        raw = response.content
        if not isinstance(raw, bytes) or not raw or len(raw) > self.max_file_bytes:
            raise GoogleDriveConnectorError("Google Drive file is empty or exceeds the size limit")
        return raw

    async def _document(
        self, *, tenant_id: str, token: str, file_meta: Mapping[str, Any],
    ) -> ConnectorDocument | None:
        file_id = file_meta.get("id")
        mime_type = file_meta.get("mimeType")
        if not isinstance(file_id, str) or not file_id:
            raise GoogleDriveConnectorError("Google Drive file is missing its ID")
        if not isinstance(mime_type, str):
            raise GoogleDriveConnectorError("Google Drive file is missing mimeType")
        permissions = file_meta.get("permissions")
        if not isinstance(permissions, list):
            raise GoogleDriveConnectorError(
                "Google Drive permissions were not returned; ACL mapping must fail closed"
            )
        # Folders and shortcuts are navigation metadata, not document content.
        if mime_type in {
            "application/vnd.google-apps.folder",
            "application/vnd.google-apps.shortcut",
        }:
            return None
        declared_size = file_meta.get("size")
        if declared_size is not None:
            try:
                if int(declared_size) > self.max_file_bytes:
                    raise GoogleDriveConnectorError("Google Drive file exceeds the size limit")
            except (TypeError, ValueError) as exc:
                raise GoogleDriveConnectorError("invalid Google Drive file size") from exc
        export = self._EXPORTS.get(mime_type)
        if mime_type.startswith("application/vnd.google-apps.") and export is None:
            raise GoogleDriveConnectorError("unsupported native Google Workspace document type")
        content_type = export[0] if export else mime_type
        content = await self._download(
            token=token, file_id=file_id,
            export_extension=export[1] if export else None,
        )
        # The host maps Drive sharing/ownership into platform ACLs.
        document = self.document_factory(tenant_id, file_meta, content_type)
        stable_id = f"gdrive:{file_id}"
        if (
            not isinstance(document, Document)
            or document.tenant_id != tenant_id
            or document.document_id != stable_id
            or document.media_type != content_type
        ):
            raise GoogleDriveConnectorError(
                "document factory must preserve tenant, stable Drive ID, and exported media type"
            )
        return ConnectorDocument(document=document, content=content)

    async def list_changed(
        self,
        *,
        tenant_id: str,
        cursor: str | None,
        changed_since=None,
        limit: int = 100,
    ) -> ConnectorPage:
        if not tenant_id.strip() or not 1 <= limit <= 500:
            raise ValueError("tenant_id and a page limit between 1 and 500 are required")
        token = await self._token(tenant_id)
        state = self._decode_cursor(cursor) if cursor is not None else None
        if state is None:
            start = self._json(await self._api_get(
                "/changes/startPageToken", token=token,
                params={"supportsAllDrives": "true"},
            ))
            watermark = start.get("startPageToken")
            if not isinstance(watermark, str) or not watermark:
                raise GoogleDriveConnectorError("Google Drive did not return a start page token")
            state = {"mode": "initial", "page_token": None, "change_token": watermark}

        items: list[ConnectorDocument] = []
        deleted: list[str] = []
        page_size = min(limit, self.page_size)
        if state["mode"] == "initial":
            watermark = state.get("change_token")
            if not isinstance(watermark, str) or not watermark:
                raise GoogleDriveConnectorError("Google Drive initial cursor is missing its watermark")
            params: dict[str, Any] = {
                "q": "trashed = false",
                "pageSize": page_size,
                "corpora": "allDrives",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "fields": f"nextPageToken,files({self._FILE_FIELDS})",
            }
            if state.get("page_token"):
                params["pageToken"] = state["page_token"]
            payload = self._json(await self._api_get("/files", token=token, params=params))
            if payload.get("incompleteSearch") is True:
                raise GoogleDriveConnectorError("Google Drive reported an incomplete initial snapshot")
            files = payload.get("files")
            if not isinstance(files, list):
                raise GoogleDriveConnectorError("Google Drive files must be an array")
            for file_meta in files:
                if not isinstance(file_meta, dict):
                    raise GoogleDriveConnectorError("Google Drive returned invalid file metadata")
                item = await self._document(
                    tenant_id=tenant_id, token=token, file_meta=file_meta,
                )
                if item is not None:
                    items.append(item)
            next_page = payload.get("nextPageToken")
            if next_page is not None and (not isinstance(next_page, str) or not next_page):
                raise GoogleDriveConnectorError("Google Drive returned an invalid initial page token")
            if next_page:
                next_cursor = self._encode_cursor({
                    "mode": "initial", "page_token": next_page,
                    "change_token": watermark,
                })
            else:
                next_cursor = self._encode_cursor({
                    "mode": "changes", "token": watermark,
                })
            return ConnectorPage(
                items=tuple(items), next_cursor=next_cursor,
                has_more=bool(next_page),
            )

        change_token = state.get("token")
        if not isinstance(change_token, str) or not change_token:
            raise GoogleDriveConnectorError("Google Drive change cursor is missing its token")
        payload = self._json(await self._api_get(
            "/changes",
            token=token,
            params={
                "pageToken": change_token,
                "pageSize": page_size,
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "fields": (
                    f"nextPageToken,newStartPageToken,changes(fileId,removed,file({self._FILE_FIELDS}))"
                ),
            },
        ))
        changes = payload.get("changes")
        if not isinstance(changes, list):
            raise GoogleDriveConnectorError("Google Drive changes must be an array")
        for change in changes:
            if not isinstance(change, dict):
                raise GoogleDriveConnectorError("Google Drive returned an invalid change")
            file_id = change.get("fileId")
            if not isinstance(file_id, str) or not file_id:
                raise GoogleDriveConnectorError("Google Drive change is missing fileId")
            stable_id = f"gdrive:{file_id}"
            file_meta = change.get("file")
            if change.get("removed") is True or (
                isinstance(file_meta, dict) and file_meta.get("trashed") is True
            ):
                deleted.append(stable_id)
                continue
            if not isinstance(file_meta, dict):
                raise GoogleDriveConnectorError("Google Drive change is missing file metadata")
            item = await self._document(
                tenant_id=tenant_id, token=token, file_meta=file_meta,
            )
            if item is not None:
                items.append(item)
        next_page = payload.get("nextPageToken")
        if next_page is not None and (not isinstance(next_page, str) or not next_page):
            raise GoogleDriveConnectorError("Google Drive returned an invalid next page token")
        if next_page is not None:
            next_cursor = self._encode_cursor({"mode": "changes", "token": next_page})
            has_more = True
        else:
            new_token = payload.get("newStartPageToken")
            if not isinstance(new_token, str) or not new_token:
                raise GoogleDriveConnectorError("Google Drive did not return a resumable page token")
            next_cursor = self._encode_cursor({"mode": "changes", "token": new_token})
            has_more = False
        return ConnectorPage(
            items=tuple(items), next_cursor=next_cursor, has_more=has_more,
            deleted_document_ids=tuple(deleted),
        )

    async def revoke(self, *, tenant_id: str, credential_ref: str) -> None:
        if credential_ref != self.credential_ref:
            raise PermissionError("Google Drive credential reference mismatch")
        token = await self._token()
        response = await self.client.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise GoogleDriveConnectorError("Google OAuth revocation redirects are not allowed")
        response.raise_for_status()
        invalidate = getattr(self.credentials, "invalidate", None)
        if callable(invalidate):
            result = invalidate(self.credential_ref)
            if hasattr(result, "__await__"):
                await result
