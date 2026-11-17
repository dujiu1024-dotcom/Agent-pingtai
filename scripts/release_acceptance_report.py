#!/usr/bin/env python3
"""Render a durable production acceptance report from release evidence.

The report is an evidence summary only. It never deploys, migrates, changes
traffic, or contacts production infrastructure. Invalid or incomplete evidence
is rendered as NO-GO and the CLI exits non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.release_gate import REQUIRED_CHECKS, validate_evidence


def _cell(value: Any) -> str:
    """Keep untrusted evidence values on one Markdown table row."""
    if value is None:
        return "—"
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").replace("`", "'")


def render_acceptance_report(
    document: Any,
    *,
    expected_commit_sha: str | None = None,
    now: datetime | None = None,
    max_age_hours: int = 168,
) -> tuple[str, bool]:
    """Return (Markdown report, passed); failed evidence always yields NO-GO."""
    errors = validate_evidence(
        document,
        expected_commit_sha=expected_commit_sha,
        now=now,
        max_age_hours=max_age_hours,
    )
    payload = document if isinstance(document, dict) else {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    passed = not errors
    decision = "GO" if passed else "NO-GO"
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines = [
        "# Production Release Acceptance Report",
        "",
        f"> **Decision: {decision}**",
        "",
        "This report summarizes supplied release evidence. It is not proof that this tool performed a deployment, migration, rollback, restore, or production smoke test.",
        "",
        "## Release identity",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Release ID | {_cell(payload.get('release_id'))} |",
        f"| Environment | {_cell(payload.get('environment'))} |",
        f"| Candidate commit | {_cell(payload.get('commit_sha'))} |",
        f"| Expected commit | {_cell(expected_commit_sha)} |",
        f"| Evidence generated (UTC) | {_cell(payload.get('generated_at_utc'))} |",
        f"| Report generated (UTC) | {generated.isoformat().replace('+00:00', 'Z')} |",
        f"| Release owner | {_cell(payload.get('release_owner'))} |",
        f"| Independent approver | {_cell(payload.get('release_approver'))} |",
        f"| Evidence archive | {_cell(payload.get('evidence_archive_ref'))} |",
        f"| Archive SHA-256 | {_cell(payload.get('evidence_archive_sha256'))} |",
        "",
        "## Acceptance checks",
        "",
        "| Check | Status | Owner | Verified at (UTC) | Evidence |",
        "|---|---|---|---|---|",
    ]
    for name in REQUIRED_CHECKS:
        item = checks.get(name) if isinstance(checks.get(name), dict) else {}
        lines.append(
            f"| {name} | {_cell(item.get('status'))} | {_cell(item.get('owner'))} | "
            f"{_cell(item.get('verified_at_utc'))} | {_cell(item.get('evidence_ref'))} |"
        )
    lines.extend(["", "## Deployment and recovery controls", ""])
    deployment = checks.get("deployment_preflight")
    deployment = deployment if isinstance(deployment, dict) else {}
    migration = checks.get("migration")
    migration = migration if isinstance(migration, dict) else {}
    rollback = checks.get("rollback")
    rollback = rollback if isinstance(rollback, dict) else {}
    controls = [
        ("Deployment preflight", "preflight_passed", deployment),
        ("Environment verified", "environment_verified", deployment),
        ("Configuration validated", "configuration_validated", deployment),
        ("Dependencies healthy", "dependencies_healthy", deployment),
        ("Change window approved", "change_window_approved", deployment),
        ("Rollback ready before deployment", "rollback_ready", deployment),
        ("Artifact SHA-256", "artifact_digest", deployment),
        ("Migration preflight passed", "preflight_passed", migration),
        ("Migration compatibility window confirmed", "compatibility_window_confirmed", migration),
        ("Migration strategy", "strategy", migration),
        ("Rollback drill passed", "drill_passed", rollback),
        ("Rollback strategy", "strategy", rollback),
    ]
    for label, key, item in controls:
        lines.append(f"- **{label}:** {_cell(item.get(key))}")
    lines.extend(["", "## Blockers and validation", ""])
    blockers = payload.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.extend(f"- {_cell(value)}" for value in blockers)
    else:
        lines.append("- None declared")
    lines.append("")
    if errors:
        lines.append("### Blocking validation errors")
        lines.extend(f"- {_cell(error)}" for error in errors)
    else:
        lines.append("All required evidence fields passed the offline release validator.")
    lines.extend([
        "",
        "## Sign-off",
        "",
        f"- Release owner: {_cell(payload.get('release_owner'))}",
        f"- Independent approver: {_cell(payload.get('release_approver'))}",
        f"- Submitted release decision: {_cell(payload.get('release_decision'))}",
        f"- Computed decision: **{decision}**",
        "",
        "A GO result means only that the supplied evidence document passed validation. Authorized release personnel must still execute and record production operations through the approved change process.",
        "",
    ])
    return "\n".join(lines), passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--commit-sha")
    parser.add_argument("--max-age-hours", type=int, default=168)
    args = parser.parse_args(argv)
    if args.max_age_hours < 1:
        parser.error("--max-age-hours must be positive")
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ACCEPTANCE REPORT: BLOCKED: cannot read evidence: {exc}", file=sys.stderr)
        return 2
    report, passed = render_acceptance_report(
        document,
        expected_commit_sha=args.commit_sha,
        max_age_hours=args.max_age_hours,
    )
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(f"ACCEPTANCE REPORT: cannot write report: {exc}", file=sys.stderr)
        return 2
    print(f"ACCEPTANCE REPORT: {'GO' if passed else 'NO-GO'}")
    print(f"report={args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
