from __future__ import annotations

import logging
import unittest

from agent_platform.security.log_redaction import (
    SecretRedactionFilter,
    SecretRedactor,
)


class SecretRedactionTests(unittest.TestCase):
    def test_exact_values_and_common_credentials_are_redacted(self):
        redactor = SecretRedactor(["super-secret-value"])
        text = "credential=super-secret-value Bearer abc.def.ghi vault_token=hvs.abcdefghijklmnop"
        result = redactor.redact_text(text)
        self.assertNotIn("super-secret-value", result)
        self.assertNotIn("abc.def.ghi", result)
        self.assertNotIn("hvs.abcdefghijklmnop", result)
        self.assertIn("vault_token=[REDACTED]", result)
        self.assertIn("[REDACTED]", result)

    def test_structured_values_are_redacted_without_mutating_input(self):
        redactor = SecretRedactor(["known-secret"])
        original = {"authorization": "Bearer token-value", "nested": ["known-secret"]}
        cleaned = redactor.redact(original)
        self.assertEqual(original["nested"][0], "known-secret")
        self.assertNotIn("known-secret", repr(cleaned))
        self.assertNotIn("token-value", repr(cleaned))

    def test_logging_filter_sanitizes_interpolated_message_and_extra(self):
        redactor = SecretRedactor(["sensitive-material"])
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "token=%s", ("sensitive-material",), None,
        )
        record.request_context = {"api_key": "abcd-secret"}
        SecretRedactionFilter(redactor).filter(record)
        self.assertNotIn("sensitive-material", record.getMessage())
        self.assertNotIn("abcd-secret", repr(record.request_context))
        self.assertEqual(record.args, ())

    def test_rejects_short_registry_values_and_supports_revocation(self):
        redactor = SecretRedactor()
        with self.assertRaises(ValueError):
            redactor.register("")
        redactor.register("abc")
        self.assertEqual(redactor.redact_text("value=abc"), "value=[REDACTED]")
        redactor.unregister("abc")
        redactor.register("long-secret")
        self.assertEqual(redactor.redact_text("long-secret"), "[REDACTED]")
        redactor.unregister("long-secret")
        self.assertEqual(redactor.redact_text("long-secret"), "long-secret")


if __name__ == "__main__":
    unittest.main()
