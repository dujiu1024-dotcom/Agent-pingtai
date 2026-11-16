from __future__ import annotations

import unittest

from agent_platform.events.webhook_http import PinnedWebhookHttpTransport


class _Response:
    status = 202


class _Connection:
    def __init__(self, hostname, address, port, *, timeout, context):
        self.hostname = hostname
        self.address = address
        self.port = port
        self.timeout = timeout
        self.context = context
        self.request_args = None
        self.closed = False

    def request(self, method, target, *, body, headers):
        self.request_args = (method, target, body, headers)

    def getresponse(self):
        return _Response()

    def close(self):
        self.closed = True


class PinnedWebhookHttpTransportTests(unittest.TestCase):
    def test_connects_to_pinned_ip_but_preserves_tls_hostname_and_host_header(self):
        made = []

        def factory(hostname, address, port, **kwargs):
            item = _Connection(hostname, address, port, **kwargs)
            made.append(item)
            return item

        transport = PinnedWebhookHttpTransport(connection_factory=factory)
        status = transport._post_pinned_sync(
            "https://hooks.example.test/events?source=agent",
            resolved_addresses=("93.184.216.34",),
            body=b'{"ok":true}',
            headers={"X-Signature": "sha256=abc", "Host": "attacker.invalid"},
            timeout_seconds=3,
        )
        self.assertEqual(status, 202)
        connection = made[0]
        self.assertEqual(connection.hostname, "hooks.example.test")
        self.assertEqual(connection.address, "93.184.216.34")
        self.assertEqual(connection.port, 443)
        method, target, body, headers = connection.request_args
        self.assertEqual((method, target, body), ("POST", "/events?source=agent", b'{"ok":true}'))
        self.assertEqual(headers["Host"], "hooks.example.test")
        self.assertEqual(headers["Content-Length"], "11")
        self.assertEqual(headers["X-Signature"], "sha256=abc")
        self.assertTrue(connection.closed)

    def test_rejects_private_or_empty_pinned_address_set(self):
        transport = PinnedWebhookHttpTransport(connection_factory=lambda *a, **k: self.fail("must not connect"))
        for addresses in ((), ("127.0.0.1",), ("10.0.0.1",)):
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                transport._post_pinned_sync(
                    "https://hooks.example.test/",
                    resolved_addresses=addresses,
                    body=b"",
                    headers={},
                    timeout_seconds=1,
                )

    def test_rejects_non_https_and_invalid_timeout(self):
        transport = PinnedWebhookHttpTransport(connection_factory=lambda *a, **k: self.fail("must not connect"))
        with self.assertRaises(ValueError):
            transport._post_pinned_sync(
                "http://hooks.example.test/",
                resolved_addresses=("93.184.216.34",),
                body=b"",
                headers={},
                timeout_seconds=1,
            )
        for timeout in (True, 0, 61):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                transport._post_pinned_sync(
                    "https://hooks.example.test/",
                    resolved_addresses=("93.184.216.34",),
                    body=b"",
                    headers={},
                    timeout_seconds=timeout,
                )


if __name__ == "__main__":
    unittest.main()
