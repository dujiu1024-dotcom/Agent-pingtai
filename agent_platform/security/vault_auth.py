"""Vault AppRole token provider with in-memory renewal and fail-closed behavior."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class VaultAppRoleTokenProvider:
    """Authenticate via AppRole; never persist, log, or expose Vault tokens."""

    def __init__(
        self, *, address: str, role_id: str, secret_id: str, mount: str = "approle",
        namespace: str | None = None, timeout_seconds: float = 5.0,
        request_func: Callable[..., tuple[int, bytes]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from urllib.parse import urlsplit
        parsed = urlsplit(address.rstrip("/"))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Vault AppRole address must be a secure origin")
        if not role_id or not secret_id or any(c in role_id + secret_id for c in "\r\n\x00"):
            raise ValueError("Vault AppRole credentials must be non-empty single-line values")
        if not mount or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in mount):
            raise ValueError("invalid Vault AppRole auth mount")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Vault timeout must be in (0, 30] seconds")
        self._base, self._role_id, self._secret_id = address.rstrip("/"), role_id, secret_id
        self._mount, self._namespace, self._timeout = mount, namespace, timeout_seconds
        self._request = request_func or self._request_sync
        self._clock = clock
        self._token: str | None = None
        self._renew_at = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _request_sync(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> tuple[int, bytes]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read(65536)
        except HTTPError as exc:
            return exc.code, exc.read(65536)
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Vault authentication request failed") from exc

    async def _post(self, endpoint: str, payload: dict[str, str], token: str | None = None) -> tuple[int, bytes]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        if token:
            headers["X-Vault-Token"] = token
        status, body = await asyncio.to_thread(
            self._request, "POST", f"{self._base}/v1/{endpoint}", headers,
            json.dumps(payload, separators=(",", ":")).encode(), self._timeout,
        )
        return status, body

    async def __call__(self) -> str:
        async with self._lock:
            if self._token and self._clock() < self._renew_at:
                return self._token
            if self._token:
                try:
                    status, body = await self._post("auth/token/renew-self", {"increment": "3600"}, self._token)
                    if status == 200:
                        data = json.loads(body)["auth"]
                        token = data["client_token"]
                        ttl = int(data.get("lease_duration", 0))
                        if isinstance(token, str) and token and ttl > 30:
                            self._token = token
                            self._renew_at = self._clock() + max(15, ttl * 0.7)
                            return token
                except (ValueError, TypeError, KeyError):
                    pass
                self._token = None
                self._renew_at = 0.0
            status, body = await self._post(
                f"auth/{self._mount}/login",
                {"role_id": self._role_id, "secret_id": self._secret_id},
            )
            if status != 200:
                raise RuntimeError(f"Vault AppRole authentication failed (HTTP {status})")
            try:
                auth = json.loads(body)["auth"]
                token = auth["client_token"]
                ttl = int(auth.get("lease_duration", 0))
            except (ValueError, TypeError, KeyError) as exc:
                raise RuntimeError("Vault AppRole returned an invalid token response") from exc
            if not isinstance(token, str) or not token or ttl <= 30:
                raise RuntimeError("Vault AppRole returned a non-renewable or invalid token")
            self._token = token
            self._renew_at = self._clock() + max(15, ttl * 0.7)
            return token
