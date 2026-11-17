# A-667-06 — legacy 404 compatibility redirect

## Contract

The platform web router redirects browser page misses to the explicit legacy console entry point (`/app/?legacy=1`) with HTTP 302 and `Cache-Control: no-store`. This preserves a recovery path for old bookmarks and legacy page routes while the new workbench is being rolled out.

The fallback is intentionally narrow:

- Only requests accepting `text/html` are eligible.
- API paths (`/api/`), platform app/assets (`/app`), operational endpoints, well-known paths, and file-like paths keep a real HTTP 404.
- JSON/API clients never receive the legacy HTML redirect.
- The explicit legacy route still serves the existing `console.html`; this change does not claim that legacy backend APIs have been migrated.

## Automated verification

Run:

```bash
python -m unittest tests.test_issue_667_a_667_06_legacy_404_redirect -v
```

The tests cover browser-page redirect and cache policy, API 404 preservation, and file/non-browser 404 preservation. The suite is included in Ruff's explicit include list and the Platform Release Gate focused-unit job.

## Acceptance boundary

This is a router-level automated contract. A deployed gateway/browser smoke test is still required to confirm proxy routing and legacy console availability. No production deployment or browser session is represented by this PR.
