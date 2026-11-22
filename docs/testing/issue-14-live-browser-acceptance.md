# Issue #14 live browser acceptance

The Playwright suite has three browser projects: desktop Chromium (1440×900), tablet Chromium (768×1024), and Pixel 7 mobile emulation. Mobile emulation is automated viewport/device emulation, not physical-device acceptance.

## Local/CI browser suite

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

The regular UI suite runs against the local Vite server and may use its existing bootstrap fixtures. The live integration journey is a separate test and installs no API route mocks. It is skipped unless all of these GitHub Actions variables/secrets are supplied:

- Variables: `PLATFORM_E2E_BASE_URL` (HTTPS deployed environment), `PLATFORM_E2E_MODEL_TEMPLATE_ID`, `PLATFORM_E2E_PROJECT_ID`, `PLATFORM_E2E_SESSION_ID`.
- Secrets: `PLATFORM_E2E_TENANT_ID`, `PLATFORM_E2E_EMAIL`, `PLATFORM_E2E_PASSWORD`.

Use a dedicated disposable tenant/account and a non-production environment with a real model provider, Graph Factory/worker, Gateway and object-storage/upload pipeline wired. The account needs permissions to publish Agent definitions, execute the chosen project/session, and upload a private knowledge document. The configured model template must point to the real provider. Do not use production credentials or real customer data.

The live test logs in through the browser, creates and publishes a model-bound Agent, submits a task in the authenticated browser session, waits for a worker terminal/approval state, then uploads a generated Markdown document through the Knowledge UI and checks the real upload response. It fails on 5xx API responses. The test is executed on desktop/tablet/mobile emulation by the same Playwright project matrix.

A skipped live journey is not proof of real integration. CI without these environment variables can prove browser-shell responsiveness only; it cannot prove model/Gateway/object-store wiring. Physical desktop/mobile device acceptance and production host evidence remain separate A-14-03 items.
