# S-34-02 Workbench page entry points

This change completes the four top-level workbench surfaces required by Issue #667:
Workbench overview, Batch, Run Replay and Observability.

| Surface | Navigation | Authenticated API surface | Main operations |
|---|---|---|---|
| Workbench | `overview` | sessions, files, workbench tasks, approvals | tenant work summary and navigation to task/session workflows |
| Batch | `batch` | `/api/platform/v1/workbench/batches` | submit JSON dataset, list/detail, cancel and idempotent retry |
| Replay | `replay` | `/api/platform/v1/runs/{run_id}/replay` | persisted event timeline, pause/resume, step/seek, paused context edit |
| Observability | `observability` | `/api/platform/v1/observability/*`, quota usage and audit APIs | capacity health, alerts, metrics, usage and audit views |

## Security and behavior

- All requests use the existing same-origin API helper, session credentials and CSRF header on mutations.
- Tenant and actor identity are not accepted from browser form fields; backend routers derive them from the authenticated Principal.
- Batch template authorization, cancellation/retry authorization and tenant-bound service checks remain server-side.
- Alert acknowledgement/resolution remains server-authorized. Capability flags only hide or show navigation; they are not authorization.
- Data-source failures are rendered as unavailable/error states. The UI does not substitute mock data or infer healthy status from missing responses.

## Scope boundary

The existing governance page continues to host knowledge, memory, Blackboard, evaluation, quota and audit administration. The new Observability page is a focused operational view over capacity, alerts, metrics, usage and audit data.

This page-entry delivery does not claim that every agency-only panel in the legacy console has a functionally equivalent new-system API. Legacy business/content/roundtable functions without a corresponding platform router remain explicit compatibility gaps for the separate S-34-05 feature-parity acceptance.
