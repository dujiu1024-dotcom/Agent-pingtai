from __future__ import annotations

import asyncio
import struct
import unittest

from agent_platform.security.clamav import ClamAVError, ClamAVScanner


class ClamAVScannerTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, response: bytes):
        async def handle(reader, writer):
            command = await reader.readuntil(b"\0")
            self.assertEqual(command, b"zINSTREAM\0")
            received = bytearray()
            while True:
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                if size == 0:
                    break
                received.extend(await reader.readexactly(size))
            self.received = bytes(received)
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        return server, server.sockets[0].getsockname()[1]

    async def test_clean_file_is_accepted_and_streamed_without_path(self):
        server, port = await self._serve(b"stream: OK\0")
        async with server:
            scanner = ClamAVScanner(
                host="127.0.0.1", port=port, chunk_bytes=1024,
            )
            self.assertTrue(await scanner.scan("../../etc/passwd", "text/plain", b"safe bytes"))
            self.assertEqual(self.received, b"safe bytes")

    async def test_infected_file_is_rejected(self):
        server, port = await self._serve(b"stream: Eicar-Test-Signature FOUND\0")
        async with server:
            scanner = ClamAVScanner(host="127.0.0.1", port=port)
            self.assertFalse(await scanner.scan("file.txt", "text/plain", b"infected"))

    async def test_scanner_error_and_oversize_fail_closed(self):
        server, port = await self._serve(b"stream: daemon ERROR\0")
        async with server:
            scanner = ClamAVScanner(
                host="127.0.0.1", port=port, max_file_bytes=4,
            )
            with self.assertRaises(ClamAVError):
                await scanner.scan("large.txt", "text/plain", b"12345")
            with self.assertRaises(ClamAVError):
                await scanner.scan("file.txt", "text/plain", b"data")


if __name__ == "__main__":
    unittest.main()
