"""Microsoft Graph / SharePoint delta connector for the existing KnowledgeConnector.

The adapter uses Graph driveItem delta feeds, downloads file bytes through short-lived
pre-authenticated URLs without forwarding Graph bearer tokens, and delegates source
permission mapping to a required host-owned document factory. It does not implement a
second connector service or alter public API contracts.
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


class MicrosoftGraphConnectorError(RuntimeError):
    """Microsoft Graph returned unsafe or unusable SharePoint data."""


class MicrosoftGraphCredentialResolver(Protocol):
    async def resolve(self, secret_ref: str) -> str: ...


DocumentFactory = Callable[[str, str, Mapping[str, Any], str], Document]
CredentialRevoker = Callable[[str], Any]


class SharePointKnowledgeConnector(BaseConnector):
    """Incremental SharePoint/OneDrive for Business connector using Graph delta."""

    connector_id = "sharepoint"
    _GRAPH = "https://graph.microsoft.com/v1.0"
    _FIELDS = (
        "id,name,size,file,folder,deleted,webUrl,lastModifiedDateTime,"
        "createdBy,lastModifiedBy,parentReference,sharepointIds"
    )
    _DOWNLOAD_SUFFIXES = (
        ".sharepoint.com", ".sharepointonline.com", ".1drv.com",
        ".onedrive.com", ".1drv.ms",
    )

    def __init__(
        self,
        *,
        client: Any,
        credentials: MicrosoftGraphCredentialResolver | None = None,
        credential_ref: str,
        drive_id: str,
        document_factory: DocumentFactory,
        credential_revoker: CredentialRevoker,
        max_file_bytes: int = 25 * 1024 * 1024,
        page_size: int = 100,
        authenticator: ConnectorAuthenticator | None = None,
        auth_scopes: tuple[str, ...] = ("sites.read.all",),
    ) -> None:
        if not credential_ref.startswith("secret://"):
            raise ValueError("SharePoint credentials must use a secret:// reference")
        if not isinstance(drive_id, str) or not drive_id.strip():
            raise ValueError("SharePoint drive_id is required")
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes < 1024:
            raise ValueError("invalid SharePoint file size limit")
        if isinstance(page_size, bool) or not 1 <= page_size <= 999:
            raise ValueError("invalid SharePoint page limit")
        if not callable(credential_revoker):
            raise ValueError("a host-owned Microsoft OAuth grant revoker is required")
        super().__init__(
            connector_id=self.connector_id, credential_ref=credential_ref,
            credentials=credentials, authenticator=authenticator, auth_scopes=auth_scopes,
        )
        self.client = client
        self.drive_id = drive_id
        self.document_factory = document_factory
        self.credential_revoker = credential_revoker
        self.max_file_bytes = max_file_bytes
        self.page_size = page_size

    async def _token(self, tenant_id: str) -> str:
        try:
            return await self.authenticate(tenant_id=tenant_id)
        except Exception as exc:
            raise MicrosoftGraphConnectorError("Microsoft Graph OAuth credential is unavailable") from exc

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise MicrosoftGraphConnectorError("Microsoft Graph response must be a JSON object")
        return payload

    @staticmethod
    def _encode_cursor(url: str) -> str:
        raw = json.dumps({"delta_url": url}, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> str:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded.encode()))
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise MicrosoftGraphConnectorError("invalid SharePoint delta cursor") from exc
        if not isinstance(value, dict) or not isinstance(value.get("delta_url"), str):
            raise MicrosoftGraphConnectorError("invalid SharePoint delta cursor")
        return value["delta_url"]

    @classmethod
    def _graph_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "graph.microsoft.com"
            or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)
            or not parsed.path.startswith("/v1.0/")
        ):
            raise MicrosoftGraphConnectorError("SharePoint delta URL is outside Microsoft Graph")
        return value

    def _delta_url(self, value: str) -> str:
        safe = self._graph_url(value)
        expected_path = (
            f"/v1.0/drives/{quote(self.drive_id, safe='')}/root/delta"
        )
        if urlsplit(safe).path != expected_path:
            raise MicrosoftGraphConnectorError("SharePoint cursor is not scoped to the configured drive delta feed")
        return safe

    @classmethod
    def _download_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        allowed = any(host.endswith(suffix) and host != suffix.lstrip(".") for suffix in cls._DOWNLOAD_SUFFIXES)
        if (
            parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)
        ):
            raise MicrosoftGraphConnectorError("SharePoint returned an unsafe download URL")
        return value

    async def _api_get(self, url: str, token: str) -> Any:
        safe_url = self._graph_url(url)
        response = await self.client.get(
            safe_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            follow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise MicrosoftGraphConnectorError("unexpected redirect from Microsoft Graph API")
        return response

    async def _download(self, *, token: str, item: Mapping[str, Any]) -> bytes:
        download_url = item.get("@microsoft.graph.downloadUrl")
        if not isinstance(download_url, str) or not download_url:
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise MicrosoftGraphConnectorError("SharePoint item is missing its ID")
            url = (
                f"{self._GRAPH}/drives/{quote(self.drive_id, safe='')}"
                f"/items/{quote(item_id, safe='')}/content"
            )
            response = await self.client.get(
                self._graph_url(url),
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                location = self._download_url(response.headers.get("location", ""))
                response = await self.client.get(
                    location, headers={"Accept": "application/octet-stream"},
                    follow_redirects=False,
                )
                if 300 <= response.status_code < 400:
                    raise MicrosoftGraphConnectorError("nested SharePoint download redirects are not allowed")
        else:
            safe_url = self._download_url(download_url)
            # Graph download URLs are short-lived bearer capabilities. Do not forward OAuth.
            response = await self.client.get(
                safe_url, headers={"Accept": "application/octet-stream"},
                follow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise MicrosoftGraphConnectorError("SharePoint signed download redirects are not allowed")
        response.raise_for_status()
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_file_bytes:
                    raise MicrosoftGraphConnectorError("SharePoint file exceeds the size limit")
            except ValueError as exc:
                raise MicrosoftGraphConnectorError("invalid SharePoint content length") from exc
        raw = response.content
        if not isinstance(raw, bytes) or not raw or len(raw) > self.max_file_bytes:
            raise MicrosoftGraphConnectorError("SharePoint file is empty or exceeds the size limit")
        return raw

    async def _permissions(self, *, token: str, item_id: str) -> list[Mapping[str, Any]]:
        url = (
            f"{self._GRAPH}/drives/{quote(self.drive_id, safe='')}"
            f"/items/{quote(item_id, safe='')}/permissions"
        )
        permissions: list[Mapping[str, Any]] = []
        for _ in range(100):
            payload = self._json(await self._api_get(url, token))
            rows = payload.get("value")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise MicrosoftGraphConnectorError("SharePoint permissions response is invalid")
            permissions.extend(rows)
            next_link = payload.get("@odata.nextLink")
            if not isinstance(next_link, str) or not next_link:
                return permissions
            url = self._graph_url(next_link)
        raise MicrosoftGraphConnectorError("SharePoint permissions pagination limit exceeded")

    async def _document(
        self, *, tenant_id: str, token: str, item: Mapping[str, Any],
    ) -> ConnectorDocument | None:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise MicrosoftGraphConnectorError("SharePoint item is missing its ID")
        if item.get("folder") is not None or item.get("package") is not None:
            return None
        file_info = item.get("file")
        if not isinstance(file_info, dict):
            return None
        media_type = file_info.get("mimeType")
        if not isinstance(media_type, str) or not media_type:
            raise MicrosoftGraphConnectorError("SharePoint file is missing its media type")
        size = item.get("size")
        if size is not None:
            try:
                if isinstance(size, bool) or int(size) > self.max_file_bytes:
                    raise MicrosoftGraphConnectorError("SharePoint file exceeds the size limit")
            except (TypeError, ValueError) as exc:
                raise MicrosoftGraphConnectorError("invalid SharePoint file size") from exc
        permissions = await self._permissions(token=token, item_id=item_id)
        metadata = dict(item)
        metadata["permissions"] = permissions
        content = await self._download(token=token, item=item)
        document = self.document_factory(tenant_id, self.drive_id, metadata, media_type)
        stable_id = f"sharepoint:{self.drive_id}:{item_id}"
        if (
            not isinstance(document, Document)
            or document.tenant_id != tenant_id
            or document.document_id != stable_id
            or document.media_type != media_type
        ):
            raise MicrosoftGraphConnectorError(
                "document factory must preserve tenant, stable SharePoint ID, and media type"
            )
        return ConnectorDocument(document=document, content=content)

    async def list_changed(
        self, *, tenant_id: str, cursor: str | None,
        changed_since=None, limit: int = 100,
    ) -> ConnectorPage:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        token = await self._token(tenant_id)
        if cursor is None:
            url = (
                f"{self._GRAPH}/drives/{quote(self.drive_id, safe='')}"
                f"/root/delta?$select={quote(self._FIELDS, safe=',')}"
                f"&$top={min(limit, self.page_size)}"
            )
        else:
            url = self._delta_url(self._decode_cursor(cursor))
        payload = self._json(await self._api_get(url, token))
        rows = payload.get("value")
        if not isinstance(rows, list):
            raise MicrosoftGraphConnectorError("SharePoint delta response value must be an array")
        items: list[ConnectorDocument] = []
        deleted: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise MicrosoftGraphConnectorError("SharePoint delta contains an invalid item")
            item_id = row.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise MicrosoftGraphConnectorError("SharePoint delta item is missing its ID")
            if row.get("deleted") is not None:
                deleted.append(f"sharepoint:{self.drive_id}:{item_id}")
                continue
            item = await self._document(tenant_id=tenant_id, token=token, item=row)
            if item is not None:
                items.append(item)
        next_link = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink")
        if isinstance(next_link, str) and next_link:
            next_cursor = self._encode_cursor(self._delta_url(next_link))
            has_more = True
        elif isinstance(delta_link, str) and delta_link:
            next_cursor = self._encode_cursor(self._delta_url(delta_link))
            has_more = False
        else:
            raise MicrosoftGraphConnectorError("SharePoint delta response has no resumable link")
        return ConnectorPage(
            items=tuple(items), next_cursor=next_cursor,
            has_more=has_more, deleted_document_ids=tuple(deleted),
        )

    async def revoke(self, *, tenant_id: str, credential_ref: str) -> None:
        if credential_ref != self.credential_ref:
            raise PermissionError("SharePoint credential reference mismatch")
        # Microsoft grant revocation is tenant/app policy-specific; delegate it to
        # the host's identity provider integration instead of pretending a local
        # token discard revokes the remote grant.
        result = self.credential_revoker(self.credential_ref)
        if hasattr(result, "__await__"):
            await result
        invalidate = getattr(self.credentials, "invalidate", None)
        if callable(invalidate):
            result = invalidate(self.credential_ref)
            if hasattr(result, "__await__"):
                await result
