# Usage and cost reporting

The tenant-scoped endpoint `GET /api/platform/v1/quotas/usage/summary` aggregates
the append-only usage ledger over a required, timezone-aware `start` / `end`
window (maximum 366 days). It accepts the same optional `run_id`, `model_key`
and bounded `limit` filters as the event export.

The response includes:
- input/output/total tokens and event count;
- tool call count, runtime milliseconds and storage bytes;
- model, project and agent attribution;
- model pricing-card versions recorded on events;
- separate estimated, provider-reported and reconciled-event cost totals;
- estimated-versus-provider-reported cost variance.

Costs are never silently combined across evidence classes. The variance is
provider-reported event cost minus estimated event cost for the selected events.
Invoice-level reconciliation remains a separate, idempotent operation through
`POST /api/platform/v1/quotas/reconciliation`, which compares ledger estimates
with an external billing source's actual amount.

The current immutable price catalog prices model input/output tokens in USD.
Tool calls and storage are measured and attributed as independent counters, but
their monetary charges are not fabricated from model token prices. A future tool
or storage price catalog can attach independently versioned rates without
changing the meaning of existing usage events. Unknown model prices remain
unknown rather than zero.

All report reads are tenant-bound to the authenticated principal. Resource
breakdowns are derived only from events returned for that tenant and selected
time window. CSV and event-level exports remain available for audit and
downstream finance processing.
