# Frontend navigation shell (UI-G1-743-e)

This package is an isolated SPA. The router uses the browser History API and the host must serve \`index.html\` for application paths (for example \`/app\`, \`/agents/:id\`, and \`/settings/models\`) to support direct refreshes and deep links.

## Optional service adapters

- \`VITE_GLOBAL_SEARCH_URL\`: same-origin search endpoint. The client sends \`q\` and optional \`entity_type\` query parameters and accepts an \`items\` or \`results\` array of \`{ id, entity_type, title, snippet, url }\`. No endpoint is assumed when unset.
- \`VITE_NOTIFICATION_SSE_URL\`: same-origin Server-Sent Events endpoint. The client consumes JSON messages with \`notification_id\`, \`title\`, optional \`body\` and \`action_url\`. The browser EventSource handles reconnects; action links are restricted to same-origin paths.

These adapters deliberately remain disabled until the backend API/event contracts are configured. No sample business data or synthetic notifications are generated.

The shell includes route metadata, grouped/collapsible navigation, breadcrumb generation, browser-local recent routes, entity-type search filtering, SSE notification presentation, error boundaries, and 403/404/empty/loading states.
