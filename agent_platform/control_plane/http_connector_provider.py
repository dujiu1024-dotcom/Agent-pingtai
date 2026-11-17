"""Configurable HTTPS JSON connector provider with redirect and host controls."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from agent_platform.connectors.base import BaseConnector, ConnectorAuthenticator
from agent_platform.control_plane.data_connectors import ConnectorError, ConnectorPage


@dataclass(frozen=True)
class JSONHTTPResponse:
    status_code: int
    body: bytes

    def raise_for_status(self) -> None:
        if self.status_code < 200 or self.status_code >= 300:
            raise ConnectorError(f"connector provider returned HTTP {self.status_code}")

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("connector provider returned invalid JSON") from exc


class ConnectorHTTPClient(Protocol):
    async def request(
        self, method: str, url: str, *,
        headers: Mapping[str, str], params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None, timeout_seconds: float = 20,
    ) -> JSONHTTPResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StdlibConnectorHTTPClient:
    """Async wrapper over urllib with bounded responses and redirects disabled."""

    def __init__(self, *, max_response_bytes: int = 10 * 1024 * 1024) -> None:
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirect())

    async def request(
        self, method: str, url: str, *,
        headers: Mapping[str, str], params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None, timeout_seconds: float = 20,
    ) -> JSONHTTPResponse:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        return await asyncio.to_thread(
            self._request_sync, method, url, headers, params, json_body, timeout_seconds,
        )

    def _request_sync(
        self, method: str, url: str, headers: Mapping[str, str],
        params: Mapping[str, str] | None, json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> JSONHTTPResponse:
        if params:
            separator = "&" if "?" in url else "?"
            url = url + separator + urllib.parse.urlencode(params)
        body = None if json_body is None else json.dumps(
            dict(json_body), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        request_headers = dict(headers)
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=body, headers=request_headers, method=method.upper(),
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read(self.max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ConnectorError("connector provider request failed") from None
        if len(raw) > self.max_response_bytes:
            raise ConnectorError("connector provider response exceeded size limit")
        return JSONHTTPResponse(status, raw)


class HTTPJSONConnectorProvider(BaseConnector):
    """Provider adapter for host-configured bearer-authenticated JSON APIs.

    Endpoints and allowed hosts are deployment configuration, never user input.
    """

    def __init__(
        self, *, list_endpoint: str, revoke_endpoint: str,
        allowed_hosts: frozenset[str], client: ConnectorHTTPClient | None = None,
        items_field: str = "items", next_cursor_field: str = "next_cursor",
        has_more_field: str = "has_more", timeout_seconds: float = 20,
        connector_id: str = "http-json", credential_ref: str | None = None,
        authenticator: ConnectorAuthenticator | None = None,
        auth_scopes: tuple[str, ...] = ("files.read",),
    ) -> None:
        hosts = {host.casefold() for host in allowed_hosts}
        for endpoint in (list_endpoint, revoke_endpoint):
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.hostname.casefold() not in hosts
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
            ):
                raise ValueError("connector endpoints must be HTTPS on an allowlisted host")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        super().__init__(
            connector_id=connector_id, credential_ref=credential_ref,
            authenticator=authenticator, auth_scopes=auth_scopes,
        )
        self.list_endpoint = list_endpoint
        self.revoke_endpoint = revoke_endpoint
        self.client = client or StdlibConnectorHTTPClient()
        self.items_field = items_field
        self.next_cursor_field = next_cursor_field
        self.has_more_field = has_more_field
        self.timeout_seconds = timeout_seconds

    async def fetch_page(
        self, *, credential: str | None = None, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None, tenant_id: str | None = None,
    ) -> ConnectorPage:
        if credential is None and tenant_id is not None:
            credential = await self.authenticate(tenant_id=tenant_id)
        if not credential:
            raise ConnectorError("connector credential is empty")
        params = {"scopes": " ".join(scopes)}
        if cursor is not None:
            params["cursor"] = json.dumps(dict(cursor), sort_keys=True, separators=(",", ":"))
        response = await self.client.request(
            "GET", self.list_endpoint,
            headers={"Authorization": "Bearer " + credential, "Accept": "application/json"},
            params=params, timeout_seconds=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ConnectorError("connector provider response must be a JSON object")
        raw_items = payload.get(self.items_field)
        if not isinstance(raw_items, list) or any(not isinstance(item, Mapping) for item in raw_items):
            raise ConnectorError("connector provider items field must be an array of objects")
        next_cursor = payload.get(self.next_cursor_field)
        if next_cursor is not None and not isinstance(next_cursor, Mapping):
            raise ConnectorError("connector provider cursor must be an object")
        has_more = payload.get(self.has_more_field, False)
        if not isinstance(has_more, bool):
            raise ConnectorError("connector provider has_more field must be boolean")
        if has_more and (next_cursor is None or dict(next_cursor) == dict(cursor or {})):
            raise ConnectorError("connector provider pagination cursor did not advance")
        return ConnectorPage(
            items=tuple(dict(item) for item in raw_items),
            next_cursor=dict(next_cursor) if next_cursor is not None else None,
            has_more=has_more,
        )

    async def revoke(self, *, credential: str | None = None, tenant_id: str | None = None) -> None:
        if credential is None and tenant_id is not None:
            credential = await self.authenticate(tenant_id=tenant_id)
        if not credential:
            raise ConnectorError("connector credential is empty")
        response = await self.client.request(
            "POST", self.revoke_endpoint,
            headers={"Authorization": "Bearer " + credential, "Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        response.raise_for_status()
