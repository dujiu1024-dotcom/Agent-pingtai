# Issue #667 / S-34-08 首屏加载预算验收

## Scope

This acceptance contract covers the default platform workbench first-screen critical path and the explicit legacy fallback introduced by S-34-03/S-34-04. It measures bytes served by the application; it does not claim real-browser LCP, TTFB, CDN, mobile-network, or production performance.

## Budgets

| Path | Resource | Maximum |
|---|---|---:|
| Workbench | HTML document | 4 KiB |
| Workbench | Critical CSS | 12 KiB |
| Workbench | Deferred application JS | 48 KiB |
| Workbench | HTML + CSS + JS total | 64 KiB |
| Legacy fallback | HTML document | 32 KiB |
| Legacy fallback | Console module | 120 KiB |
| Legacy fallback | Configuration module | 8 KiB |
| Legacy fallback | HTML + both modules total | 160 KiB |

The budgets are intentionally explicit and deterministic. They protect against accidentally restoring the former large inline script, adding unbounded critical assets, or dropping the workbench preload/defer hints. The legacy aggregate is measured separately because it is an opt-in compatibility route, not the default landing path.

## Evidence and limits

- The S-34-04 branch currently serves a 1,739-byte workbench HTML document, 7,895-byte stylesheet, and 42,698-byte application script.
- Its legacy fallback serves a 30,602-byte HTML document, 116,801-byte console module, and 5,995-byte configuration module.
- The focused test re-reads actual TestClient response bodies and enforces the limits on every CI run.
- External Three.js is intentionally excluded from the byte sum because it is hosted outside this application; production browser verification must include third-party transfer, cache state, compression, device/network profile, and LCP.
- This test does not assert the legacy fallback is as fast as the default workbench. It asserts the legacy payload remains bounded and separately observable.

## Run

```bash
python -m unittest tests.test_issue_667_s_667_08_first_screen_performance -v
ruff check tests/test_issue_667_s_667_08_first_screen_performance.py
```

## Acceptance status

Automated byte-budget contract only. Real browser performance evidence (cold/warm cache, desktop/mobile throttling, LCP/CLS, and production CDN behavior) remains a deployment acceptance item.
