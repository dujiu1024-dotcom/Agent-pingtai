# Frontend navigation shell (UI-G1-743-e)

This package is an isolated SPA. The router uses the browser History API and the host must serve `index.html` for application paths (for example `/app`, `/agents/:id`, and `/settings/models`) to support direct refreshes and deep links.

## Optional service adapters

- `VITE_GLOBAL_SEARCH_URL`: same-origin search endpoint. The client sends `q` and optional `entity_type` query parameters and accepts an `items` or `results` array of `{ id, entity_type, title, snippet, url }`. No endpoint is assumed when unset.
- `VITE_NOTIFICATION_SSE_URL`: same-origin Server-Sent Events endpoint. The client consumes JSON messages with `notification_id`, `title`, optional `body` and `action_url`. The browser EventSource handles reconnects; action links are restricted to same-origin paths.

These adapters deliberately remain disabled until the backend API/event contracts are configured. No sample business data or synthetic notifications are generated.

The shell includes route metadata, grouped/collapsible navigation, breadcrumb generation, browser-local recent routes, entity-type search filtering, SSE notification presentation, error boundaries, and 403/404/empty/loading states.

## Automated browser tests

Run `pnpm test:e2e` from this directory. Playwright starts the Vite development server and runs Chromium projects at 1440×900 (desktop) and 768×1024 (tablet). The suite covers dashboard entry without a login UI prerequisite, deep-link refresh, navigation collapse/expand, 403/404 templates, and horizontal-overflow protection. CI installs Chromium and uploads the HTML report on every run.

## Dual-auth acceptance boundary

These browser tests currently verify the frontend shell and its public/no-login entry path only. They do **not** establish the backend's `auth.enabled=false` REST/SSE/file authorization behavior, nor the `auth.enabled=true` login/session/CSRF/RBAC path. Those assertions require the completed #755 endpoint adapters and the canonical server bootstrap/login contracts; the current frontend shell has no implemented bootstrap/session integration to drive a truthful authenticated E2E. Do not interpret a passing UI E2E run as final dual-mode acceptance. Track the remaining integration against #755 and the parent #752 acceptance matrix.
