#!/usr/bin/env python3
"""Fail-closed production release evidence and sign-off validator.

This tool validates evidence only; it never deploys, migrates, changes traffic,
or accesses production infrastructure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REQUIRED_CHECKS = (
    "e2e", "fault_injection", "performance", "security",
    "backup_restore", "production_smoke", "migration", "rollback",
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PLACEHOLDER_RE = re.compile(
    r"(?:^|[\\s/_-])(?:replace[-_ ]with|todo|tbd|changeme|placeholder|pending)(?:$|[\\s/_-])",
    re.IGNORECASE,
)


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return (normalized.startswith("<") and normalized.endswith(">")) or bool(
        PLACEHOLDER_RE.search(normalized)
    )


def _parse_utc(value: Any, field: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field}: missing UTC timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field}: invalid ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        errors.append(f"{field}: timestamp must be UTC with an explicit offset")
        return None
    return parsed.astimezone(timezone.utc)


def _fresh(timestamp: datetime | None, field: str, *, current: datetime,
           max_age_hours: int, errors: list[str]) -> None:
    if timestamp is None:
        return
    if timestamp > current + timedelta(minutes=5):
        errors.append(f"{field}: timestamp is in the future")
    if current - timestamp > timedelta(hours=max_age_hours):
        errors.append(f"{field}: evidence is stale")


def _required_text(document: dict[str, Any], field: str, errors: list[str]) -> None:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field}: required non-empty string")
    elif _is_placeholder(value):
        errors.append(f"{field}: must not contain a template placeholder")


def validate_evidence(
    document: Any,
    *,
    expected_commit_sha: str | None = None,
    now: datetime | None = None,
    max_age_hours: int = 168,
) -> list[str]:
    """Return blocking errors; no errors means the candidate is eligible for GO."""
    if not isinstance(document, dict):
        return ["evidence document must be a JSON object"]
    errors: list[str] = []
    if type(document.get("schema_version")) is not int or document["schema_version"] != 2:
        errors.append("schema_version must be the integer 2")
    for field in ("release_id", "environment", "release_owner", "release_approver",
                  "evidence_archive_ref"):
        _required_text(document, field, errors)
    if document.get("environment") != "production":
        errors.append("environment: must equal production")
    if document.get("release_owner") == document.get("release_approver"):
        errors.append("release_approver: must be independent from release_owner")

    commit_sha = document.get("commit_sha")
    if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
        errors.append("commit_sha: must be a full 40-character lowercase Git SHA")
    elif expected_commit_sha and commit_sha != expected_commit_sha:
        errors.append("commit_sha: evidence does not match the release candidate")

    current = now or datetime.now(timezone.utc)
    generated = _parse_utc(document.get("generated_at_utc"), "generated_at_utc", errors)
    _fresh(generated, "generated_at_utc", current=current,
           max_age_hours=max_age_hours, errors=errors)

    blockers = document.get("blockers")
    if not isinstance(blockers, list) or any(
        not isinstance(item, str) or not item.strip() for item in blockers
    ):
        errors.append("blockers: must be an array of non-empty strings")
    elif blockers:
        errors.append("blockers: must be empty before release")

    checks = document.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks: required object")
        checks = {}
    for name in REQUIRED_CHECKS:
        result = checks.get(name)
        if not isinstance(result, dict):
            errors.append(f"checks.{name}: missing required check")
            continue
        if result.get("status") != "passed":
            errors.append(f"checks.{name}.status: must be passed")
        for field in ("owner", "evidence_ref"):
            value = result.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"checks.{name}.{field}: required non-empty string")
            elif _is_placeholder(value):
                errors.append(f"checks.{name}.{field}: must not contain a template placeholder")
        verified = _parse_utc(
            result.get("verified_at_utc"), f"checks.{name}.verified_at_utc", errors
        )
        _fresh(verified, f"checks.{name}.verified_at_utc", current=current,
               max_age_hours=max_age_hours, errors=errors)
    migration = checks.get("migration")
    if isinstance(migration, dict):
        if migration.get("preflight_passed") is not True:
            errors.append("checks.migration.preflight_passed: must be true")
        if migration.get("compatibility_window_confirmed") is not True:
            errors.append("checks.migration.compatibility_window_confirmed: must be true")
        if migration.get("strategy") not in {"expand_contract", "forward_fix"}:
            errors.append("checks.migration.strategy: must be expand_contract or forward_fix")
    rollback = checks.get("rollback")
    if isinstance(rollback, dict):
        if rollback.get("drill_passed") is not True:
            errors.append("checks.rollback.drill_passed: must be true")
        if rollback.get("strategy") not in {
            "compensating_migration", "backup_restore", "forward_fix"
        }:
            errors.append("checks.rollback.strategy: unsupported rollback strategy")

    decision = document.get("release_decision")
    if decision != "go":
        errors.append("release_decision: must be go; otherwise release is blocked")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path, help="JSON release evidence file")
    parser.add_argument("--commit-sha", help="Expected full Git SHA for the release candidate")
    parser.add_argument("--max-age-hours", type=int, default=168)
    args = parser.parse_args(argv)
    if args.max_age_hours < 1:
        parser.error("--max-age-hours must be positive")
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"RELEASE GATE: BLOCKED: cannot read evidence: {exc}", file=sys.stderr)
        return 1
    errors = validate_evidence(
        document, expected_commit_sha=args.commit_sha, max_age_hours=args.max_age_hours
    )
    if errors:
        print("RELEASE GATE: BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("RELEASE GATE: PASSED")
    print(f"release_id={document['release_id']} commit_sha={document['commit_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
