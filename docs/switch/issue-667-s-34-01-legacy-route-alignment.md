# S-34-01 / S-667-01 — legacy console API route alignment

## Current route contract

The legacy console API calls use the versioned `/api/v1/...` surface. Production composition installs `ApiV1CompatibilityMiddleware`, which rewrites that public alias to `/api/platform/v1/...`, preserves the query string, and normalizes an unmounted route to the stable JSON `NOT_FOUND` envelope with `X-API-Version: v1`.

This is a routing compatibility contract, not a claim that every legacy business operation has a platform implementation.

## Legacy endpoint inventory and successor status

| Legacy console route | Current platform successor | Status |
|---|---|---|
| `/api/v1/legacy/state` | No single equivalent; aggregates legacy task, agent, project, finance and event data | Backend aggregation required |
| `/api/v1/legacy/tasks`, `/tasks/` | `/api/platform/v1/workbench/tasks` | Payload/response adapter required |
| `/api/v1/legacy/agents/` | Registry API | Resource and response adapter required |
| `/api/v1/legacy/roundtable` | No equivalent multi-expert history/run API | Gap |
| `/api/v1/legacy/output` | Session/file asset API | Path-to-asset authorization adapter required |
| `/api/v1/legacy/selfcheck` | Health/readiness API | Semantics differ; UI adapter required |
| `/api/v1/legacy/content`, `/finance`, `/sync`, `/subscriptions/toggle`, `/dispatch/diagnose`, `/jobs` | No verified one-to-one router | Gap; keep fail-closed 404 |

## Guardrails

- Do not redirect a write request to a superficially similar endpoint unless payload, authorization and idempotency semantics are equivalent.
- Do not turn unknown API paths into the legacy HTML fallback; API clients must receive JSON 404.
- `?legacy=1` selects the legacy HTML shell only. It does not create the old CLI/SQLite backend routes.
- Follow-up adapters must use authenticated platform services and tenant/resource authorization, not import the legacy CLI or open the legacy SQLite database.

## Verification

`python -m pytest tests/test_issue_667_s_667_01_legacy_route_contract.py -q`

The focused tests verify route inventory, `/api/v1` rewrite/query preservation, and stable JSON 404 behavior. They do not claim the listed business APIs are implemented; remaining gaps are explicit above.