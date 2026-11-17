#!/usr/bin/env python3
"""Fail-closed static validation for production outbound egress configuration."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

_REQUIRED_NO_PROXY = {"postgres", "redis", "minio", "localhost", "127.0.0.1"}
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(?:\.(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*$")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"line {number}: invalid environment key")
        values[key] = value
    return values


def validate_egress_config(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    proxy = values.get("EGRESS_PROXY_URL", "")
    parsed = urlsplit(proxy)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        errors.append("EGRESS_PROXY_URL must be an HTTPS URL without embedded credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        errors.append("EGRESS_PROXY_URL must not contain a path, query or fragment")

    raw_hosts = values.get("EGRESS_ALLOWED_HOSTS", "")
    hosts = [item.strip().lower() for item in raw_hosts.split(",") if item.strip()]
    if not hosts:
        errors.append("EGRESS_ALLOWED_HOSTS must contain at least one exact hostname")
    elif any("*" in host or not _HOST.fullmatch(host) for host in hosts):
        errors.append("EGRESS_ALLOWED_HOSTS must contain exact DNS hostnames without wildcards")
    elif len(set(hosts)) != len(hosts):
        errors.append("EGRESS_ALLOWED_HOSTS must not contain duplicate hostnames")

    no_proxy = {item.strip().lower() for item in values.get("NO_PROXY", "").split(",") if item.strip()}
    if "*" in no_proxy or no_proxy != _REQUIRED_NO_PROXY:
        errors.append("NO_PROXY must be exactly postgres,redis,minio,localhost,127.0.0.1")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        values = parse_env_file(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"EGRESS POLICY: BLOCKED: {exc}", file=sys.stderr)
        return 1
    errors = validate_egress_config(values)
    if errors:
        print("EGRESS POLICY: BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("EGRESS POLICY: CONFIGURATION VALID")
    print("Note: firewall/NetworkPolicy and proxy enforcement require separate production evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
