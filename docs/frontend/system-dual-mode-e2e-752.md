# Issue #752 — Dual-mode browser smoke and system gate

## What this change makes executable

The frontend CI already installs Chromium and invokes `pnpm test:e2e`. This branch closes the missing runner contract by adding the pinned Playwright test dependency and the script. It adds `e2e/system-dual-mode.spec.ts`, which runs against the real Vite-built frontend with only the bootstrap response isolated.

The browser suite verifies:

- Every registered business page, global capability page and entity-detail route can be directly opened in both auth modes.
- In `auth.enabled=false`, the application labels the mode as no-login, hides the login link and restricts identity-only routes without blocking business routes.
- In `auth.enabled=true`, the login entry and identity routes are available while business routes remain navigable.
- A Run deep link survives a hard browser refresh in both modes.
- Bootstrap 503 renders an explicit error and does not guess the auth mode.

The suite uses a deterministic bootstrap fixture; it is a frontend mode/navigation smoke test, not proof of backend authorization, real API data, SSE, file ACL, or end-to-end business mutations.

## Run

From `frontend/`:

```sh
pnpm install
pnpm exec playwright install chromium
pnpm test:e2e
```

CI installs Chromium with OS dependencies using the existing workflow.

## Required live integration evidence still outstanding

A final #752 GO decision still requires a deployed integration environment and real credentials/test fixtures. Execute the backend API/SSE/file matrix against both deployments (no token for auth=false; seeded role credentials for auth=true), and record build SHA, deployment config, role, API response, audit evidence and browser trace. Do not substitute mocked bootstrap tests for backend no-auth proof or RBAC proof.

The current browser suite does not claim completion of the full system gate: cross-module writes, Task→Run→Agent→Model/Cost→Audit, Approval→Run, Knowledge→Citation, Office→Agent/Run, reverse-proxy subpath, SSE reconnect/cursor compensation, file upload/download/range, browser matrix beyond Chromium, accessibility, performance, staged cutover and rollback rehearsal remain deployment-level acceptance work.

## Release safety

- Do not hot-switch auth mode; change deployment configuration and restart.
- For false→true, initialize the first administrator and explicitly map existing resources before traffic.
- For true→false, require operator acknowledgement that identity/data isolation is disabled.
- Rollback switches only the frontend entry point; never roll back business data or repeat completed external side effects.
- Keep the legacy entry available through the agreed rollback window.
