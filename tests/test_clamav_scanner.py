from __future__ import annotations

import asyncio
import struct
import unittest

from agent_platform.storage.clamav import ClamAVError, ClamAVScanner


class ClamAVScannerTests(unittest.IsolatedAsyncioTestCase):
    async def _server(self, response: bytes):
        async def handle(reader, writer):
            command = await reader.readexactly(len(b"zINSTREAM\0"))
            self.assertEqual(command, b"zINSTREAM\0")
            total = 0
            while True:
                size = struct.unpack(">I", await reader.readexactly(4))[0]
                if size == 0:
                    break
                total += size
                await reader.readexactly(size)
            self.assertGreater(total, 0)
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        return server, server.sockets[0].getsockname()[1]

    async def test_instream_clean_file_is_accepted(self):
        server, port = await self._server(b"stream: OK\0")
        async with server:
            scanner = ClamAVScanner(host="127.0.0.1", port=port)
            self.assertTrue(await scanner.scan("a.txt", "text/plain", b"clean text"))

    async def test_instream_detected_malware_is_rejected(self):
        server, port = await self._server(b"stream: Eicar-Test-Signature FOUND\0")
        async with server:
            scanner = ClamAVScanner(host="127.0.0.1", port=port)
            self.assertFalse(await scanner.scan("eicar.txt", "text/plain", b"infected"))

    async def test_invalid_or_oversized_file_fails_closed(self):
        scanner = ClamAVScanner(
            host="127.0.0.1", port=3310, max_scan_bytes=4,
        )
        with self.assertRaises(ClamAVError):
            await scanner.scan("empty.txt", "text/plain", b"")
        with self.assertRaises(ClamAVError):
            await scanner.scan("large.txt", "text/plain", b"12345")


if __name__ == "__main__":
    unittest.main()
