# Issue #667 / L3-T12 — legacy console route migration and platform UI fallback

## Route contract

- Historical bookmarks to `GET /console.html` receive a temporary redirect
  to `/app/?legacy=1`, preserving the legacy experience through the new
  gateway route without exposing the archive as a separate root static mount.
- `GET /app` and `GET /app/` serve the React platform UI when
  `agent_platform/web/static/index.html` is present.
- The platform UI HTML response receives a small, accessible
  **旧版功能兼容入口** link outside the React root. It points to the explicit
  `/app/?legacy=1` route, so a rebuilt React bundle cannot accidentally
  remove the gateway-level fallback entry.
- `GET /app/?legacy=1` serves the archived `console.html` document with
  `Cache-Control: no-store`.
- If the new platform UI build is absent, both `/app` and SPA deep links
  under `/app/{path}` fall back to the archived console. If both frontends
  are absent, the route returns HTTP 503 instead of a misleading blank page.
- Legacy console modules are served from the same-origin
  `/app/assets/legacy-console.js` and
  `/app/assets/legacy-console-config.js` paths. The optional Three.js CDN
  script is asynchronous, so it does not block console startup while offline.
- Static asset resolution is constrained to the `static/assets` directory.

## Restored legacy assets

The repository root `console.html` was absent from current main even though
historical PR #692/#723 had introduced and verified the fallback. The fallback
source is restored from the last merged modularized version (PR #704), with
the module URL namespaced under the current Vite asset router. The split
console/config modules are restored under `agent_platform/web/static/assets`.

## API route migration boundary

Legacy console API calls retain the versioned `/api/v1` contract and are
handled by the existing API v1 compatibility middleware, which maps to the
current `/api/platform/v1` router surface. This is route compatibility, not
business-function parity. The existing mapping inventory remains authoritative
in `docs/switch/issue-667-s-34-01-legacy-route-alignment.md`; it records
unimplemented legacy aggregations (including legacy state, roundtable,
content, finance, sync, subscription toggles, dispatch diagnosis and jobs).
Unknown API routes must remain fail-closed JSON 404s rather than bypassing
platform authorization through the retired CLI/SQLite backend.

## Automated acceptance

`tests/test_platform_web.py` now verifies:
- React UI remains the default and exposes the compatibility entry.
- Explicit `legacy=1` serves the modularized console and same-origin modules.
- Missing new UI automatically serves the legacy console.
- Missing both UIs returns 503.
- SPA deep links use the same fallback and unknown/traversal asset paths stay 404.

The focused test is included in Ruff's explicit file list and the Platform
Release Gate's focused-unit job.

## Acceptance boundary

These are repository/router contract tests. They do not claim a production
gateway, CDN, browser-offline or physical-device rehearsal. Business/content/
roundtable native API parity and deployed reverse-proxy verification remain
open acceptance work; this change restores the compatibility path, not those
business implementations.
