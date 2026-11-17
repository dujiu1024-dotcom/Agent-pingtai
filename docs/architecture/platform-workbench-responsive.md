# Platform Workbench Responsive Layout and API Boundary

C-14-05 keeps the platform workbench usable from desktop through 320px narrow screens.

- Desktop/tablet use a fixed-width navigation rail and a min-width-safe content column.
- Narrow screens switch to a sticky, horizontally scrollable navigation strip; data tables remain locally horizontally scrollable rather than forcing page-wide overflow.
- Touch targets are at least 44px on narrow screens. Focus-visible outlines, active-page semantics, safe-area insets, and reduced-motion handling are retained.
- All browser API traffic uses the shared same-origin `request()` transport. It centralizes cookie credentials, CSRF headers for mutations, and HTTP error normalization. JSON API calls use `api()`; binary upload/download use the same transport.
- Capability metadata only hides unavailable navigation entries. It is not an authorization decision. Each API request is still authenticated and authorized by the backend; no tenant/resource permission or business workflow is implemented in the browser.

Automated coverage is in `tests/test_platform_web.py`. It checks the delivered shell, responsive breakpoints, accessibility hooks, and shared transport contract. Device/browser field verification is intentionally excluded from this task's acceptance run.
