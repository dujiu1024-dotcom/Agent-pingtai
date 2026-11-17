# Issue #667 / A-667-07 offline fallback acceptance

## Runtime behavior

- The workbench registers a Service Worker scoped to `/app/`.
- Installation pre-caches the workbench HTML shell and its first-party JavaScript/CSS.
- Navigation requests use network-first behavior and fall back to the cached `/app/` shell when the network is unavailable.
- First-party static assets use cache-first behavior.
- Activation removes older workbench shell caches and claims existing clients.
- The worker only intercepts same-origin GET requests below `/app/`; API calls and other application paths are not cached or replayed.

## Automated checks

`tests/test_issue_667_a_667_07_offline_fallback.py` verifies the FastAPI worker route, scope header, browser registration and executes the Service Worker in Node's VM with Cache Storage/fetch stubs to exercise install, activate, cache hits and offline navigation fallback.

Run:

```bash
python -m unittest tests.test_issue_667_a_667_07_offline_fallback -v
ruff check tests/test_issue_667_a_667_07_offline_fallback.py agent_platform/web/app.py
```

The Node VM test exercises the actual Service Worker source but is not a full browser/device offline drill. Production CDN caching, browser-specific service-worker lifecycle, first-install offline behavior before the first successful online visit, and deployment-specific CSP/HTTPS must still be verified in the deployed environment.
