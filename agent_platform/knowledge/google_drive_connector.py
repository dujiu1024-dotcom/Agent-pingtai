"""Google Drive Changes API adapter for the existing KnowledgeConnector contract.

OAuth access tokens are resolved from the existing secret reference at execution
time. A host-owned document factory must map Drive sharing policy into platform
ACLs; files without an explicit policy mapping are not ingested.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage
from agent_platform.knowledge.contracts import Document


class GoogleDriveConnectorError(RuntimeError):
    """Google Drive returned an unsafe or unusable response."""


class GoogleDriveCredentialResolver:
    async def resolve(self, secret_ref: str) -> str:  # pragma: no cover - protocol shape
        raise NotImplementedError


DocumentFactory = Callable[[str, Mapping[str, Any], str], Document]


class GoogleDriveKnowledgeConnector:
    """Incremental Google Drive connector using Changes API page tokens.

    The document factory is mandatory because Drive sharing permissions must be
    translated into platform ACLs by the host. It must fail closed when the source
    ACL cannot be represented safely.
    """

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

    def __init__(
        self,
        *,
        client: Any,
        credentials: GoogleDriveCredentialResolver,
        credential_ref: str,
        document_factory: DocumentFactory,
        max_file_bytes: int = 25 * 1024 * 1024,
        page_size: int = 100,
    ) -> None:
        if not credential_ref.startswith("secret://"):
            raise ValueError("Google Drive credentials must use a secret:// reference")
        if max_file_bytes < 1024 or not 1 <= page_size <= 1000:
            raise ValueError("invalid Google Drive size or page limit")
        self.client = client
        self.credentials = credentials
        self.credential_ref = credential_ref
        self.document_factory = document_factory
        self.max_file_bytes = max_file_bytes
        self.page_size = page_size

    async def _token(self) -> str:
        token = await self.credentials.resolve(self.credential_ref)
        if not isinstance(token, str) or not token.strip():
            raise GoogleDriveConnectorError("Google Drive OAuth credential is unavailable")
        return token

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleDriveConnectorError("Google Drive response must be a JSON object")
        return payload

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
        if export_extension:
            path = f"/files/{file_id}/export"
            params = {
                "mimeType": next(
                    mime for mime, (_, extension) in self._EXPORTS.items()
                    if extension == export_extension
                )
            }
        else:
            path = f"/files/{file_id}"
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
            if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
                raise GoogleDriveConnectorError("Google Drive returned an unsafe download redirect")
            # Signed download URLs are bearer capabilities; never forward the OAuth token.
            response = await self.client.get(
                location, follow_redirects=False,
                headers={"Accept": "application/octet-stream"},
            )
            if 300 <= response.status_code < 400:
                raise GoogleDriveConnectorError("nested Google Drive download redirects are not allowed")
        response.raise_for_status()
        raw = response.content
        if not isinstance(raw, bytes) or not raw or len(raw) > self.max_file_bytes:
            raise GoogleDriveConnectorError("Google Drive file is empty or exceeds the size limit")
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_file_bytes:
                    raise GoogleDriveConnectorError("Google Drive file exceeds the size limit")
            except ValueError as exc:
                raise GoogleDriveConnectorError("invalid Google Drive content length") from exc
        return raw

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
        token = await self._token()
        page_token = cursor
        if page_token is None:
            start = self._json(await self._api_get(
                "/changes/startPageToken", token=token,
                params={"supportsAllDrives": "true"},
            ))
            page_token = start.get("startPageToken")
            if not isinstance(page_token, str) or not page_token:
                raise GoogleDriveConnectorError("Google Drive did not return a start page token")

        payload = self._json(await self._api_get(
            "/changes",
            token=token,
            params={
                "pageToken": page_token,
                "pageSize": min(limit, self.page_size),
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "fields": (
                    "nextPageToken,newStartPageToken,"
                    "changes(fileId,removed,file(id,name,mimeType,modifiedTime,"
                    "webViewLink,description,md5Checksum,size,trashed,parents,"
                    "owners(displayName,emailAddress)))"
                ),
            },
        ))
        changes = payload.get("changes")
        if not isinstance(changes, list):
            raise GoogleDriveConnectorError("Google Drive changes must be an array")

        items: list[ConnectorDocument] = []
        deleted: list[str] = []
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
            mime_type = file_meta.get("mimeType")
            if not isinstance(mime_type, str):
                raise GoogleDriveConnectorError("Google Drive file is missing mimeType")
            export = self._EXPORTS.get(mime_type)
            content_type = export[0] if export else mime_type
            extension = export[1] if export else None
            content = await self._download(
                token=token, file_id=file_id, export_extension=extension,
            )
            # The host maps Google Drive sharing/ownership into platform ACLs.
            document = self.document_factory(tenant_id, file_meta, content_type)
            if (
                not isinstance(document, Document)
                or document.tenant_id != tenant_id
                or document.document_id != stable_id
                or document.media_type != content_type
            ):
                raise GoogleDriveConnectorError(
                    "document factory must preserve tenant, stable Drive ID, and exported media type"
                )
            items.append(ConnectorDocument(document=document, content=content))

        next_page = payload.get("nextPageToken")
        if next_page is not None and (not isinstance(next_page, str) or not next_page):
            raise GoogleDriveConnectorError("Google Drive returned an invalid next page token")
        if next_page is None:
            next_page = payload.get("newStartPageToken")
            if not isinstance(next_page, str) or not next_page:
                raise GoogleDriveConnectorError("Google Drive did not return a resumable page token")
        return ConnectorPage(
            items=tuple(items),
            next_cursor=next_page,
            has_more=payload.get("nextPageToken") is not None,
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
