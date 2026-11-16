"""Pinned HTTPS transport for outbound webhook delivery.

DNS resolution is performed by WebhookEgressPolicy. This transport connects only
to the supplied public IPs while retaining the original hostname for Host and
TLS SNI/certificate validation. Redirects are never followed.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        address: str,
        port: int,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._pinned_address, self.port), timeout=self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PinnedWebhookHttpTransport:
    """POST to a DNS-pinned HTTPS destination without proxy or redirect use."""

    def __init__(
        self,
        *,
        ssl_context: ssl.SSLContext | None = None,
        connection_factory: Callable[..., http.client.HTTPSConnection] | None = None,
    ) -> None:
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._connection_factory = connection_factory or _PinnedHTTPSConnection

    async def post_pinned(
        self,
        url: str,
        *,
        resolved_addresses: tuple[str, ...],
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> int:
        import asyncio

        return await asyncio.to_thread(
            self._post_pinned_sync,
            url,
            resolved_addresses=resolved_addresses,
            body=body,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

    def _post_pinned_sync(
        self,
        url: str,
        *,
        resolved_addresses: tuple[str, ...],
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> int:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("webhook destination must be credential-free HTTPS")
        if not isinstance(body, bytes):
            raise TypeError("webhook body must be bytes")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be in (0, 60]")
        if not resolved_addresses:
            raise ValueError("at least one DNS-pinned address is required")

        addresses: list[str] = []
        for raw_address in resolved_addresses:
            address = str(ipaddress.ip_address(raw_address))
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("webhook destination must resolve only to public IP addresses")
            if address not in addresses:
                addresses.append(address)

        hostname = parsed.hostname
        port = parsed.port or 443
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        request_headers = {
            key: value for key, value in headers.items()
            if key.lower() not in {"host", "content-length", "connection", "transfer-encoding"}
        }
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        request_headers["Host"] = host_header if port == 443 else f"{host_header}:{port}"
        request_headers["Content-Length"] = str(len(body))
        request_headers["Connection"] = "close"

        last_error: Exception | None = None
        for address in addresses:
            connection = self._connection_factory(
                hostname, address, port,
                timeout=float(timeout_seconds),
                context=self._ssl_context,
            )
            try:
                connection.request("POST", target, body=body, headers=request_headers)
                response = connection.getresponse()
                return int(response.status)
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
            finally:
                connection.close()
        raise ConnectionError("all DNS-pinned webhook destination addresses failed") from last_error
