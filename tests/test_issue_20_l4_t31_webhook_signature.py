"""L4-T31 contract tests for webhook HMAC-SHA256 signatures."""

from __future__ import annotations

import unittest

from agent_platform.events.webhooks import signature, verify_signature


class WebhookSignatureAlgorithmTests(unittest.TestCase):
    SECRET = b"0123456789abcdef0123456789abcdef"
    TIMESTAMP = "1800000000"
    BODY = b'{"event":"run.completed","id":"evt-l4-t31"}'
    EXPECTED_SIGNATURE = (
        "sha256=26f46327012dc2fb09e8455a7cb24fc684fae85f7f5aa594"
        "6d6143a35a802bec"
    )

    def test_signature_matches_independent_hmac_sha256_vector(self):
        self.assertEqual(
            signature(self.SECRET, self.TIMESTAMP, self.BODY),
            self.EXPECTED_SIGNATURE,
        )

    def test_valid_signature_is_accepted(self):
        self.assertTrue(
            verify_signature(
                self.SECRET,
                self.TIMESTAMP,
                self.BODY,
                self.EXPECTED_SIGNATURE,
                now=int(self.TIMESTAMP),
            )
        )

    def test_invalid_signature_and_tampered_payload_are_rejected(self):
        self.assertFalse(
            verify_signature(
                self.SECRET,
                self.TIMESTAMP,
                self.BODY,
                "sha256=" + "0" * 64,
                now=int(self.TIMESTAMP),
            )
        )
        self.assertFalse(
            verify_signature(
                self.SECRET,
                self.TIMESTAMP,
                self.BODY + b" ",
                self.EXPECTED_SIGNATURE,
                now=int(self.TIMESTAMP),
            )
        )

    def test_expired_signature_is_rejected(self):
        self.assertFalse(
            verify_signature(
                self.SECRET,
                self.TIMESTAMP,
                self.BODY,
                self.EXPECTED_SIGNATURE,
                now=int(self.TIMESTAMP) + 301,
                tolerance_seconds=300,
            )
        )


if __name__ == "__main__":
    unittest.main()
