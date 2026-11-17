# Issue #667 / S-34-07 — offline legacy fallback acceptance

## Change

The legacy console's optional Three.js CDN script is now loaded with the
`async` attribute. A slow or unreachable CDN therefore does not hold the
HTML parser while the legacy UI is being opened through `/app/?legacy=1`.

The console already detects an unavailable global `THREE`, hides only the 3D
canvas, displays a degraded-mode notice, and continues initializing the
operational panels and job polling. This change makes that fallback path
reachable promptly when the browser has no network access.

## Automated acceptance

Run:

```bash
python -m unittest tests.test_issue_667_s_34_07_offline_fallback -v
ruff check tests/test_issue_667_s_34_07_offline_fallback.py
```

The tests verify the served fallback response is non-cacheable, the sole
external dependency is asynchronous, and panel/job initialization remains
outside the optional Three.js branch.

## Boundary

This is a deterministic source/router contract, not a real browser airplane
mode test. Deployed gateway routing, browser offline behavior, and legacy API
availability still require a desktop/mobile smoke test in the target
environment. The 3D scene is intentionally unavailable when Three.js cannot
be downloaded; the non-3D console panels remain the supported degraded mode.
