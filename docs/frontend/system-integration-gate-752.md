# System Integration Gate & Rollback Runbook — Issue #752

Status: **preparatory scaffold only**. This document and its route-contract tests do not claim that the cross-module E2E Gate has passed.

## Selection and scope

Issue #752 is the single cross-module integration gate. Module-level auth=false/auth=true core-path smoke tests remain with each module Issue. #752 owns integrated journeys, complete system matrices, browser/performance/accessibility regression summary, staged cutover, monitoring and rollback rehearsal.

Issue #752 explicitly allows preparing the test framework and rollback plan before module delivery. Final E2E execution is gated on #743 Gate 2, #755, #753, and module PRs being merged or on a controller-approved integration branch.

## Added executable contract checks

frontend/src/acceptance/system-gate.test.ts verifies:
- Canonical page IDs and paths remain unique.
- The key cross-module route inventory exists.
- Global Search, Notifications, Audit and Operations entry points are registered.
- auth=false keeps business navigation available and hides identity-only navigation.
- auth=true exposes identity/RBAC navigation.
- Task, Run, Approval, Document, Conversation, File, Workflow Run, Agent and Workflow deep links resolve IDs.
- Route ownership metadata remains present.

Run from repository root:

```sh
corepack enable
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend exec vitest run src/acceptance/system-gate.test.ts
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

These are deterministic route-contract checks, not browser E2E and not API security tests.

## Integrated journey matrix

| ID | Journey | Required assertions | Current gate |
|---|---|---|---|
| SYS-01 | Dashboard → Task → Run → Agent → Model/Cost → Audit | Entity IDs preserved; each page reads the same canonical entity; authorization remains server-side | Blocked until module integration |
| SYS-02 | Approval → Run | Approval decision and Run reference are consistent; rejected/expired decisions cannot resume execution | Blocked until H4 + runtime integration |
| SYS-03 | Knowledge → Citation | Document version, chunk, page/offset and retrieval trace remain traceable | Blocked until H1 + RAG integration |
| SYS-04 | Office → Agent/Run | Office state is sourced from real events; reconnect and 2D fallback are correct | Blocked until Office API + #755 |
| SYS-05 | Search → Entity detail | Query, filters and return context survive navigation; server filters results in auth=true | Blocked until search API contract |
| SYS-06 | Notification → Event context | Dedupe, durable read state, safe same-origin action URL and reconnect compensation | Blocked until notification delivery API |
| SYS-07 | Audit → Trace/Run | actor may be null in no-auth; metadata is redacted and append-only | Blocked until audit integration |
| SYS-08 | Operations → Queue/consumer/DLQ | Metrics have units, sample time and stale state; UI is read-only | Blocked until real probes are wired |

## Authentication matrix

Run each applicable journey in two separate deployments. Do not hot-switch modes in a running process.

### auth.enabled=false
- Fresh instance has no login/register/administrator initialization dependency.
- From a non-loopback client, REST, SSE/WebSocket and file paths work without session/Bearer/Principal where the feature is supported.
- No anonymous user, default role or fabricated user_id is created.
- No user/department/project membership ACL is applied; instance/workspace context remains explicit.
- CORS, TLS/proxy boundary, rate limits, body/file limits, security headers and audit remain enabled.
- Audit actor is system/instance or null.

### auth.enabled=true
- Login/logout/session expiry and CSRF are enforced.
- Administrator initialization, invitations, disabled accounts, role/menu/action/API permissions and data-scope checks are verified.
- Cross-project and cross-tenant access is denied and audited.
- Search, notifications, files, SSE and deep links use the same authorization decision as their backing APIs.

### Mode transition
1. Capture current deployment config and current entry point.
2. For false → true, initialize the first administrator and produce an explicit ownership/tenant mapping report before opening traffic.
3. For true → false, require an operator acknowledgement that identity/data isolation will no longer apply; record the change in deployment/audit evidence.
4. Change deployment configuration and restart. Verify the running instance does not change mode before restart and bootstrap reports the new mode after restart.
5. Never expose a hot-switch UI/API.

## Browser, accessibility and resilience matrix

Run against the supported browser policy (Chrome/Edge/Firefox/Safari) and desktop/tablet/narrow viewports:
- Deep link open, refresh, back/forward, query/filter restoration and reverse-proxy subpath.
- Keyboard-only navigation, visible focus, semantic labels, reduced motion and screen-reader smoke.
- Long lists, slow network, API timeout/429/5xx, partial data, stale state, optimistic concurrency conflict.
- SSE heartbeat timeout, Last-Event-ID resume, duplicate/out-of-order events and snapshot compensation.
- File upload, preview, download, expired URL and Range requests.
- Responsive layout and visual comparison against approved dashboard and 3D office reference assets.

No screenshot or browser result is considered passed until produced by an actual supported browser runner against the integration deployment.

## Release gate and evidence

A release candidate cannot be marked GO unless:
- #743 Gate 2, #755 and #753 prerequisites have explicit evidence.
- Every module PR records auth=false/auth=true core-path smoke and module-level test/CI.
- All required cross-module journeys above are executed with environment, build SHA, mode, actor/role, steps, expected/actual result and evidence links.
- P0/P1 defects are zero or have an explicit owner, accepted risk and expiry approved by the controller.
- Browser, accessibility, performance and security matrices have results.
- Rollback rehearsal is successful and old entry remains usable.
- The integration report links each check to PR, commit and CI run. Mock data, static route tests and a merged PR alone do not satisfy release evidence.

## Cutover procedure
1. Build and publish the new frontend as an independent artifact; retain the previous platform-ui artifact unchanged.
2. Verify API/schema backward compatibility and deployment config before changing traffic.
3. Deploy to development, then internal trial, then acceptance environment. Record artifact SHA and config checksum at every stage.
4. Smoke health/bootstrap, core route loading, asset paths, API origin, SSE and file paths through the actual reverse proxy/subpath.
5. Enable the new entry point only after the relevant gate is GO. Keep the legacy entry available for at least one rollback window.
6. Monitor HTTP 4xx/5xx, frontend uncaught errors, API latency, SSE disconnect/reconnect, queue/consumer health and file errors.
7. Do not dual-write business data and do not migrate business state as part of an entry-point switch.

## Rollback procedure
Trigger rollback on auth-mode mismatch, P0/P1 security/data exposure, critical journey failure, sustained 5xx/error-budget breach, broken asset/API base path, or loss of Run/approval/file visibility.
1. Freeze further rollout and record current build SHA, config checksum, timestamp and incident owner.
2. Switch only the frontend entry point/proxy route back to the last known-good legacy artifact.
3. Do not roll back business database data or reverse completed Task/Run/Approval actions.
4. Verify legacy /app and platform-ui/index.html, API calls, SSE and file operations remain functional.
5. Verify in-flight Tasks/Runs continue and no duplicate external side effects are created.
6. Preserve logs, trace IDs and audit evidence; open defects with reproduction and affected build SHA.
7. Re-attempt rollout only after fix, regression CI and a new rehearsal.

## Current known blockers
- #753 page-map PR #754 remains unmerged at time of preparation.
- #755 has stacked implementation PRs and known failing/unfinished CI; do not treat dual-mode acceptance as complete.
- Several module PRs are open and module CI is pending or failed. No integrated environment/browser evidence is available in this connector-only session.
- This PR does not modify production routing, reverse-proxy configuration, deployment workflows or business data. No live service/device test was performed.