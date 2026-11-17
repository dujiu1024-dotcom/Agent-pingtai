# Production release acceptance report

This report is generated from a schema-v2 release evidence document. It is an
offline summary and does not execute deployment, migrations, rollback, restore,
traffic changes, or production smoke checks.

## Generate the report

    python scripts/release_acceptance_report.py \
      --evidence release-evidence.json \
      --output release-acceptance-report.md \
      --commit-sha "$(git rev-parse HEAD)"

The command always writes a Markdown report when the evidence file is readable.
It exits with code 0 only when scripts/release_gate.py accepts the complete
evidence for the exact candidate SHA. Invalid, missing, stale, pending, failed,
or mismatched evidence produces a **NO-GO** report and exit code 1. An unreadable
input or unwritable output exits 2.

The report contains release identity, candidate and expected SHA, release owner,
independent approver, immutable evidence archive reference and digest, all nine
required checks with their owner/time/evidence reference, deployment preflight,
migration and rollback controls, blockers, validation errors, and the computed
decision. Evidence-provided text is escaped for Markdown table safety.

## Required evidence

Fill docs/release/RELEASE_EVIDENCE_TEMPLATE.json using the procedure in
PRODUCTION_RELEASE_ACCEPTANCE.md. Each check must have a named owner, UTC
verification time, and durable evidence reference. Migration preflight and
compatibility confirmation, plus a successful rollback drill and supported
strategy, are mandatory. The release owner and independent approver must be
different identities.

## Archive and sign-off

Archive the source JSON, generated Markdown report, validator output, CI links,
and approval record together in the immutable evidence archive. The generated
report is not a substitute for production evidence or independent approval.
Never set a check to passed merely because a workflow exists or a test is
planned; unexecuted production smoke, backup/restore, migration, and rollback
checks remain blockers.
