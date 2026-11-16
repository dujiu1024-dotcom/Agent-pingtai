"""HashiCorp Vault KV v2 backend for versioned platform secrets.
Each platform secret version is stored at an immutable, version-addressed KV path.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class VaultKvV2SecretBackend:
    """Store secret values in Vault KV v2; never return them in errors or logs.

    The Vault token is held only in memory and is never persisted by this adapter.
    """

    def __init__(
        self, *, address: str, token: str, mount: str = "secret",
        namespace: str | None = None, timeout_seconds: float = 5.0,
        allow_http: bool = False, request_func: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        parsed = urlsplit(address.rstrip("/"))
        schemes = {"https", "http"} if allow_http else {"https"}
        if (parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("Vault address must be a secure origin without credentials or path")
        if not isinstance(token, str) or not token or any(ch in token for ch in "\r\n\x00"):
            raise ValueError("Vault token must be a non-empty single-line value")
        if not isinstance(mount, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", mount):
            raise ValueError("Vault KV mount path is invalid")
        if namespace is not None and (not namespace or any(ch in namespace for ch in "\r\n\x00")):
            raise ValueError("Vault namespace is invalid")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Vault timeout must be in (0, 30] seconds")
        self._base = address.rstrip("/")
        self._token = token
        self._mount = mount
        self._namespace = namespace
        self._timeout = timeout_seconds
        self._request_func = request_func or self._request_sync

    @staticmethod
    def _request_sync(method: str, url: str, headers: dict[str, str],
                      body: bytes | None, timeout: float) -> tuple[int, bytes]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read(1024 * 1024)
        except HTTPError as exc:
            return exc.code, exc.read(1024 * 1024)
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Vault request failed") from exc

    def _path(self, secret_ref: str, version: int) -> str:
        from agent_platform.security.secrets import SecretManager
        ref = SecretManager._validate_ref(secret_ref)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("secret version must be a positive integer")
        segments = [quote(part, safe="") for part in ref[len("secret://"):].split("/") ]
        return "/".join((*segments, "versions", str(version)))

    async def _call(self, method: str, endpoint: str,
                    payload: dict[str, object] | None = None) -> tuple[int, bytes]:
        headers = {"X-Vault-Token": self._token, "Accept": "application/json"}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return await asyncio.to_thread(self._request_func, method,
            f"{self._base}/v1/{endpoint}", headers, body, self._timeout)

    def _endpoint(self, operation: str, secret_ref: str, version: int) -> str:
        return f"{self._mount}/{operation}/{self._path(secret_ref, version)}"

    async def put(self, secret_ref: str, version: int, value: str) -> None:
        status, _ = await self._call("POST", self._endpoint("data", secret_ref, version),
                                     {"data": {"value": value}})
        if status not in (200, 204):
            raise RuntimeError(f"Vault rejected secret write (HTTP {status})")

    async def get(self, secret_ref: str, version: int) -> str | None:
        status, body = await self._call("GET", self._endpoint("data", secret_ref, version))
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"Vault secret read failed (HTTP {status})")
        try:
            value = json.loads(body)["data"]["data"]["value"]
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("Vault returned an invalid secret payload") from exc
        if not isinstance(value, str):
            raise RuntimeError("Vault secret payload value is not text")
        return value

    async def revoke(self, secret_ref: str, version: int) -> None:
        path = self._path(secret_ref, version)
        status, _ = await self._call("POST", f"{self._mount}/destroy/{path}",
                                     {"versions": [version]})
        if status == 404:
            return
        if status not in (200, 204):
            raise RuntimeError(f"Vault secret destruction failed (HTTP {status})")
        status, _ = await self._call("DELETE", f"{self._mount}/metadata/{path}")
        if status not in (200, 204, 404):
            raise RuntimeError(f"Vault secret metadata cleanup failed (HTTP {status})")
