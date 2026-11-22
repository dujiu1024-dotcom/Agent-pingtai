from __future__ import annotations

import os
import subprocess
import sys
import unittest

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


    def test_injected_settings_cannot_bypass_startup_security_validation(self):
        script = """
import os
from agent_platform.production import create_production_app
from agent_platform.production_config import ProductionConfigurationError, ProductionSettings

os.environ["AUTH_ENABLED"] = "false"
settings = ProductionSettings(
    postgres_dsn="postgresql://platform@db/platform",
    redis_url="redis://redis:6379/0",
    allowed_origins=("https://platform.example.com",),
)
try:
    create_production_app(settings=settings)
except ProductionConfigurationError as exc:
    assert "AUTH_ENABLED" in str(exc)
else:
    raise AssertionError("injected ProductionSettings bypassed startup validation")
"""
        env = os.environ.copy()
        env.update({
            "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
            "REDIS_URL": "redis://redis:6379/0",
            "ALLOWED_ORIGINS": "https://platform.example.com",
            "AUTH_ENABLED": "true",
            "CSRF_ENABLED": "true",
            "AUTH_SECRET": "a" * 48,
        })
        for key in (
            "DISABLE_CSRF", "CSRF_BYPASS", "AUTH_BYPASS", "DISABLE_AUTH",
            "SKIP_AUTHENTICATION", "DISABLE_AUTH_VALIDATION",
            "SKIP_AUTH_VALIDATION",
        ):
            env.pop(key, None)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
