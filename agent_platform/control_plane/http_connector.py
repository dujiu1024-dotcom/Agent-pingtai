"""Configurable HTTPS JSON connector provider for REST-based source systems."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from agent_platform.control_plane.data_connectors import ConnectorPage


class HTTPConnectorError(RuntimeError):
    pass


class HTTPJSONConnectorProvider:
    """Provider adapter with fixed endpoints and host allowlist.

    The HTTP client is host-owned. Endpoints are deployment configuration, never
    supplied by a connector record or user request. Redirects are always disabled.
    """

    def __init__(
        self, *, client: Any, base_url: str, allowed_hosts: frozenset[str],
        list_path: str, revoke_path: str | None = None,
        items_field: str = "items", next_cursor_field: str = "next_cursor",
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.hostname not in allowed_hosts
        ):
            raise ValueError("connector base_url must be HTTPS on an approved host")
        if not list_path.startswith("/") or list_path.startswith("//"):
            raise ValueError("list_path must be an absolute path on the configured host")
        if revoke_path is not None and (
            not revoke_path.startswith("/") or revoke_path.startswith("//")
        ):
            raise ValueError("revoke_path must be an absolute path on the configured host")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.list_path = list_path
        self.revoke_path = revoke_path
        self.items_field = items_field
        self.next_cursor_field = next_cursor_field
        self.max_response_bytes = max_response_bytes

    async def _request(self, method: str, path: str, *, credential: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = await self.client.request(
            method, self.base_url + path, params=params,
            headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
            follow_redirects=False,
        )
        if response.is_redirect:
            raise HTTPConnectorError("connector redirects are not permitted")
        response.raise_for_status()
        body = response.content
        if len(body) > self.max_response_bytes:
            raise HTTPConnectorError("connector response exceeds configured size limit")
        try:
            value = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPConnectorError("connector returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise HTTPConnectorError("connector response must be a JSON object")
        return value

    async def fetch_page(self, *, credential: str, scopes: tuple[str, ...], cursor: dict[str, Any] | None) -> ConnectorPage:
        params = {"scope": " ".join(scopes)}
        if cursor is not None:
            params["cursor"] = json.dumps(dict(cursor), separators=(",", ":"), sort_keys=True)
        payload = await self._request("GET", self.list_path, credential=credential, params=params)
        items = payload.get(self.items_field)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise HTTPConnectorError("connector items field must be an array of objects")
        next_cursor = payload.get(self.next_cursor_field)
        if next_cursor is not None and not isinstance(next_cursor, dict):
            raise HTTPConnectorError("connector next cursor must be an object or null")
        has_more = payload.get("has_more", False)
        if not isinstance(has_more, bool):
            raise HTTPConnectorError("connector has_more must be boolean")
        if has_more and next_cursor is None:
            raise HTTPConnectorError("connector has_more requires next_cursor")
        return ConnectorPage(
            items=tuple(items), next_cursor=next_cursor, has_more=has_more,
        )

    async def revoke(self, *, credential: str) -> None:
        if self.revoke_path is None:
            return
        await self._request("POST", self.revoke_path, credential=credential)
