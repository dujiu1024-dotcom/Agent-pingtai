# Issue #667 / S-34-03 — legacy UI offline fallback

## Behavior

The platform UI remains the default for `GET /app` and `GET /app/`. The
legacy console is available through the explicit compatibility URL
`/app/?legacy=1` (also `/app?legacy=1`). If the new platform UI
`index.html` is absent from the deployment's static directory, the same
route automatically serves the legacy console rather than returning a dead
link. If neither UI exists, the route returns HTTP 503 with an explicit
availability error.

The fallback serves the repository-root `console.html` as HTML and sets
`Cache-Control: no-store` so a transient deployment state is not cached.
Existing allowlisted platform asset routing remains unchanged.

## Verification

Run:

```bash
python -m pytest tests/test_platform_web.py -q
ruff check agent_platform/web/app.py tests/test_platform_web.py
```

The focused tests cover the default platform UI, explicit `legacy=1`, automatic
fallback when the new index is missing, fail-closed behavior when both UIs are
missing, and the existing asset allowlist.

## Acceptance boundary

These tests exercise the FastAPI router and repository files in the CI
checkout. They do not prove that a deployed reverse proxy or CDN routes the
fallback correctly, nor that all legacy console API endpoints are available
after fallback. Those require a deployment smoke test against the actual
gateway and backend.
