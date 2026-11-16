"""Refresh-token OAuth credential resolver backed by the existing SecretManager.

A secret reference resolves to a JSON object containing client_id, client_secret,
and refresh_token (plus optional scope). Rotated refresh tokens are versioned through
SecretManager.rotate; access tokens remain process-local and are never persisted.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlsplit


class OAuthCredentialError(RuntimeError):
    """OAuth refresh or credential storage failed closed."""


class OAuthRefreshTokenResolver:
    """Refresh Google/Microsoft access tokens from versioned secret references."""

    _ALLOWED_HOSTS = {"oauth2.googleapis.com", "login.microsoftonline.com"}

    def __init__(
        self, *, client: Any, secret_manager: Any, token_url: str,
        refresh_skew_seconds: int = 90,
    ) -> None:
        parsed = urlsplit(token_url)
        if (
            parsed.scheme != "https" or parsed.hostname not in self._ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("OAuth token endpoint must be an approved Google or Microsoft HTTPS endpoint")
        if isinstance(refresh_skew_seconds, bool) or not 0 <= refresh_skew_seconds <= 600:
            raise ValueError("refresh skew must be between 0 and 600 seconds")
        self.client = client
        self.secret_manager = secret_manager
        self.token_url = token_url
        self.refresh_skew_seconds = refresh_skew_seconds
        self._lock = asyncio.Lock()
        self._cached_access_token: str | None = None
        self._expires_at = 0.0

    async def resolve(self, secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not secret_ref.startswith("secret://"):
            raise ValueError("OAuth credentials must use a secret:// reference")
        async with self._lock:
            now = time.monotonic()
            if self._cached_access_token and now < self._expires_at - self.refresh_skew_seconds:
                return self._cached_access_token
            raw = await self.secret_manager.resolve(secret_ref=secret_ref)
            try:
                credentials = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise OAuthCredentialError("OAuth secret must contain a JSON credential object") from exc
            if not isinstance(credentials, dict):
                raise OAuthCredentialError("OAuth secret must contain a JSON credential object")
            client_id = credentials.get("client_id")
            client_secret = credentials.get("client_secret")
            refresh_token = credentials.get("refresh_token")
            if any(not isinstance(value, str) or not value.strip()
                   for value in (client_id, client_secret, refresh_token)):
                raise OAuthCredentialError("OAuth secret is missing required client or refresh credentials")
            form = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            scope = credentials.get("scope")
            if isinstance(scope, str) and scope.strip():
                form["scope"] = scope
            response = await self.client.post(
                self.token_url,
                data=form,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise OAuthCredentialError("OAuth token endpoint redirects are not allowed")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise OAuthCredentialError("OAuth token response must be a JSON object")
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(access_token, str) or not access_token.strip()
                or isinstance(expires_in, bool) or not isinstance(expires_in, int)
                or expires_in < 60 or expires_in > 86400
            ):
                raise OAuthCredentialError("OAuth token response is missing a valid access token lifetime")
            rotated_refresh_token = payload.get("refresh_token")
            if rotated_refresh_token is not None:
                if not isinstance(rotated_refresh_token, str) or not rotated_refresh_token.strip():
                    raise OAuthCredentialError("OAuth provider returned an invalid refresh token")
                if rotated_refresh_token != refresh_token:
                    updated = dict(credentials)
                    updated["refresh_token"] = rotated_refresh_token
                    await self.secret_manager.rotate(
                        secret_ref=secret_ref,
                        value=json.dumps(updated, separators=(",", ":"), sort_keys=True),
                    )
            self._cached_access_token = access_token
            self._expires_at = time.monotonic() + expires_in
            return access_token
