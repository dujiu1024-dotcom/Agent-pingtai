"""ClamAV clamd INSTREAM adapter for fail-closed file safety scanning."""
from __future__ import annotations

import asyncio
import ssl
import struct
from typing import Any


class ClamAVError(RuntimeError):
    """ClamAV is unavailable or returned an invalid protocol response."""


class ClamAVScanner:
    """Stream file bytes to clamd without writing untrusted files to local disk."""

    def __init__(
        self, *, host: str, port: int = 3310,
        timeout_seconds: float = 20, max_scan_bytes: int = 25 * 1024 * 1024,
        chunk_bytes: int = 64 * 1024, ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if not host.strip() or not 1 <= port <= 65535:
            raise ValueError("valid ClamAV host and port are required")
        if timeout_seconds <= 0 or max_scan_bytes < 1:
            raise ValueError("invalid ClamAV timeout or scan size")
        if chunk_bytes < 1024 or chunk_bytes > 1024 * 1024:
            raise ValueError("chunk_bytes must be between 1 KiB and 1 MiB")
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.max_scan_bytes = max_scan_bytes
        self.chunk_bytes = chunk_bytes
        self.ssl_context = ssl_context

    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        del filename, media_type
        if not content or len(content) > self.max_scan_bytes:
            raise ClamAVError("file is empty or exceeds ClamAV scan limit")
        writer: Any = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self.host, self.port, ssl=self.ssl_context,
                )
                writer.write(b"zINSTREAM\0")
                for offset in range(0, len(content), self.chunk_bytes):
                    chunk = content[offset:offset + self.chunk_bytes]
                    writer.write(struct.pack(">I", len(chunk)))
                    writer.write(chunk)
                    await writer.drain()
                writer.write(struct.pack(">I", 0))
                await writer.drain()
                response = await reader.readuntil(b"\0")
        except TimeoutError as exc:
            raise ClamAVError("ClamAV scan timed out") from exc
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise ClamAVError("ClamAV scanner is unavailable or returned an invalid response") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        try:
            result = response.rstrip(b"\0").decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ClamAVError("ClamAV returned a non-ASCII response") from exc
        if result.endswith(": OK"):
            return True
        if result.endswith(" FOUND"):
            return False
        raise ClamAVError("ClamAV returned an unexpected scan status")
