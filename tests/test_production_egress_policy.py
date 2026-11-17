from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.validate_production_egress import parse_env_file, validate_egress_config


class ProductionEgressPolicyTests(unittest.TestCase):
    def setUp(self):
        self.valid = {
            "EGRESS_PROXY_URL": "https://proxy.example.internal:8443",
            "EGRESS_ALLOWED_HOSTS": "api.openai.com,api.anthropic.com",
            "NO_PROXY": "postgres,redis,minio,localhost,127.0.0.1",
        }

    def test_explicit_proxy_allowlist_and_internal_bypass_are_valid(self):
        self.assertEqual(validate_egress_config(self.valid), [])

    def test_missing_proxy_fails_closed(self):
        values = dict(self.valid)
        values.pop("EGRESS_PROXY_URL")
        self.assertTrue(any("EGRESS_PROXY_URL" in item for item in validate_egress_config(values)))

    def test_plain_http_proxy_is_rejected(self):
        values = dict(self.valid, EGRESS_PROXY_URL="http://proxy.example.internal:8080")
        self.assertTrue(any("EGRESS_PROXY_URL" in item for item in validate_egress_config(values)))

    def test_proxy_credentials_and_paths_are_rejected(self):
        for proxy in (
            "https://user:secret@proxy.example.internal:8443",
            "https://proxy.example.internal:8443/path",
        ):
            with self.subTest(proxy=proxy):
                values = dict(self.valid, EGRESS_PROXY_URL=proxy)
                self.assertTrue(validate_egress_config(values))

    def test_malformed_proxy_ports_and_hostnames_are_rejected(self):
        for proxy in (
            "https://proxy.example.internal:not-a-port",
            "https://proxy.example.internal:70000",
            "https://bad_host.internal:8443",
            "https://[invalid-ipv6]:8443",
            "https://proxy.example.internal:8443 ",
        ):
            with self.subTest(proxy=proxy):
                values = dict(self.valid, EGRESS_PROXY_URL=proxy)
                self.assertTrue(validate_egress_config(values))

    def test_duplicate_env_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "EGRESS_PROXY_URL=https://proxy.example.internal:8443\n"
                "EGRESS_PROXY_URL=https://attacker.example.internal:8443\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate environment key"):
                parse_env_file(path)

    def test_wildcard_and_invalid_allowlist_entries_are_rejected(self):
        for hosts in ("*.example.com", "api.example.com,*", "bad host"):
            with self.subTest(hosts=hosts):
                values = dict(self.valid, EGRESS_ALLOWED_HOSTS=hosts)
                self.assertTrue(any("exact DNS hostnames" in item for item in validate_egress_config(values)))

    def test_no_proxy_cannot_disable_proxy_for_all_destinations(self):
        values = dict(self.valid, NO_PROXY="*")
        self.assertTrue(any("NO_PROXY" in item for item in validate_egress_config(values)))

    def test_no_proxy_must_be_exact_internal_dependency_set(self):
        values = dict(self.valid, NO_PROXY="localhost,127.0.0.1")
        self.assertTrue(any("NO_PROXY" in item for item in validate_egress_config(values)))

    def test_env_file_parser_reads_comments_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# comment\nEGRESS_PROXY_URL=https://proxy.example.internal:8443\n", encoding="utf-8")
            self.assertEqual(parse_env_file(path)["EGRESS_PROXY_URL"], "https://proxy.example.internal:8443")


if __name__ == "__main__":
    unittest.main()
