"""Fail-closed ClamAV INSTREAM adapter for uploaded file content."""
from __future__ import annotations

import asyncio
import ssl
import struct
from typing import Any


class ClamAVError(RuntimeError):
    pass


class ClamAVScanner:
    """Scan in-memory bytes through clamd INSTREAM; no filesystem paths are sent."""

    def __init__(
        self, *, host: str, port: int = 3310, timeout_seconds: float = 30.0,
        max_file_bytes: int = 25 * 1024 * 1024, chunk_bytes: int = 64 * 1024,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if not host.strip() or not 1 <= port <= 65535:
            raise ValueError("valid ClamAV host and port are required")
        if timeout_seconds <= 0 or max_file_bytes < 1 or not 1024 <= chunk_bytes <= 1024 * 1024:
            raise ValueError("invalid ClamAV timeout or size limits")
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.chunk_bytes = chunk_bytes
        self.ssl_context = ssl_context

    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        del filename, media_type  # clamd scans bytes, never a caller-supplied path
        if not content or len(content) > self.max_file_bytes:
            raise ClamAVError("file is empty or exceeds scanner size limit")
        writer: Any = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, self.port, ssl=self.ssl_context,
                    server_hostname=self.host if self.ssl_context else None,
                ),
                timeout=self.timeout_seconds,
            )
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(content), self.chunk_bytes):
                chunk = content[offset:offset + self.chunk_bytes]
                writer.write(struct.pack("!I", len(chunk)))
                writer.write(chunk)
            writer.write(struct.pack("!I", 0))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            response = await asyncio.wait_for(
                reader.readuntil(b"\0"), timeout=self.timeout_seconds,
            )
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise ClamAVError("ClamAV scan request failed") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        message = response.rstrip(b"\0").decode("utf-8", errors="replace")
        if message.endswith(": OK"):
            return True
        if message.endswith(" FOUND"):
            return False
        raise ClamAVError("ClamAV returned an error response")
