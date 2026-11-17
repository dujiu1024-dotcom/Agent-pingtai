"""Explicit expiration-boundary tests for signed webhook deliveries."""
import unittest
from unittest.mock import patch

from agent_platform.events.webhooks import signature, verify_signature


class WebhookSignatureExpiryBoundaryTests(unittest.TestCase):
    def test_signature_is_valid_at_inclusive_window_edge_and_expired_one_second_later(self):
        secret = b"b" * 32
        body = b'{"event":"run.completed","id":"evt-1"}'
        issued_at = 1_800_000_000
        signed = signature(secret, str(issued_at), body)

        self.assertTrue(verify_signature(
            secret, str(issued_at), body, signed,
            now=issued_at + 300, tolerance_seconds=300,
        ))
        self.assertFalse(verify_signature(
            secret, str(issued_at), body, signed,
            now=issued_at + 301, tolerance_seconds=300,
        ))

    def test_future_timestamp_outside_window_is_rejected(self):
        secret = b"b" * 32
        body = b'{"event":"run.completed","id":"evt-2"}'
        issued_at = 1_800_000_000
        signed = signature(secret, str(issued_at), body)
        self.assertTrue(verify_signature(
            secret, str(issued_at), body, signed,
            now=issued_at - 300, tolerance_seconds=300,
        ))
        self.assertFalse(verify_signature(
            secret, str(issued_at), body, signed,
            now=issued_at - 301, tolerance_seconds=300,
        ))

    def test_default_clock_expires_previously_valid_signature(self):
        secret = b"b" * 32
        body = b"payload"
        issued_at = 1_800_000_000
        signed = signature(secret, str(issued_at), body)
        with patch("agent_platform.events.webhooks.time.time", return_value=issued_at + 301):
            self.assertFalse(verify_signature(secret, str(issued_at), body, signed))

    def test_expiry_does_not_mask_body_or_signature_tampering(self):
        secret = b"b" * 32
        body = b"payload"
        issued_at = 1_800_000_000
        signed = signature(secret, str(issued_at), body)
        self.assertFalse(verify_signature(
            secret, str(issued_at), body + b"-tampered", signed, now=issued_at,
        ))
        self.assertFalse(verify_signature(
            secret, str(issued_at), body, "sha256=" + "0" * 64, now=issued_at,
        ))


if __name__ == "__main__":
    unittest.main()
