from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_platform.production_config import (
    ProductionConfigurationError,
    load_production_settings,
)


class DangerousProductionAuthConfigurationTests(unittest.TestCase):
    BASE = {
        "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
        "REDIS_URL": "redis://redis:6379/0",
        "ALLOWED_ORIGINS": "https://platform.example.com",
    }

    def test_safe_explicit_production_configuration_is_accepted(self):
        settings = load_production_settings({
            **self.BASE,
            "AUTH_ENABLED": "true",
            "CSRF_ENABLED": "true",
            "AUTH_SECRET": "a" * 48,
        })
        self.assertEqual(settings.allowed_origins, ("https://platform.example.com",))

    def test_authentication_cannot_be_disabled_or_misconfigured(self):
        for value in ("false", "0", "no", "off", "invalid"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ProductionConfigurationError, "AUTH_ENABLED"
            ):
                load_production_settings({**self.BASE, "AUTH_ENABLED": value})

    def test_csrf_and_auth_validation_cannot_be_disabled(self):
        for key in (
            "CSRF_ENABLED",
            "CSRF_PROTECTION_ENABLED",
            "AUTH_VALIDATION_ENABLED",
            "AUTHORIZATION_VALIDATION_ENABLED",
            "TOKEN_VALIDATION_ENABLED",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                ProductionConfigurationError, key
            ):
                load_production_settings({**self.BASE, key: "false"})

    def test_auth_and_csrf_bypass_switches_are_rejected_when_enabled(self):
        for key in (
            "DISABLE_CSRF",
            "CSRF_BYPASS",
            "AUTH_BYPASS",
            "DISABLE_AUTH",
            "SKIP_AUTHENTICATION",
            "DISABLE_AUTH_VALIDATION",
            "SKIP_AUTH_VALIDATION",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                ProductionConfigurationError, key
            ):
                load_production_settings({**self.BASE, key: "true"})

    def test_default_and_short_authentication_secrets_are_rejected(self):
        for value in ("secret", "changeme", "development", "your-secret-key", "short"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ProductionConfigurationError, "AUTH_SECRET"
            ):
                load_production_settings({**self.BASE, "AUTH_SECRET": value})

    def test_missing_origins_and_wildcard_origins_are_rejected(self):
        without_origins = dict(self.BASE)
        without_origins.pop("ALLOWED_ORIGINS")
        with self.assertRaisesRegex(ProductionConfigurationError, "ALLOWED_ORIGINS"):
            load_production_settings(without_origins)

        for origin in ("*", "https://*", "https://*.example.com"):
            with self.subTest(origin=origin), self.assertRaisesRegex(
                ProductionConfigurationError, "exact HTTPS"
            ):
                load_production_settings({**self.BASE, "ALLOWED_ORIGINS": origin})


if __name__ == "__main__":
    unittest.main()
