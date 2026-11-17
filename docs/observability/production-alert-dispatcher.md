# Issue #16 A-16-03 — production alert dispatcher process

The durable alert notification dispatcher now has a repository-owned process
entrypoint: python -m agent_platform.observability.production_alert_worker.

## Required configuration

The process shares the normal production PostgreSQL settings
(AGENT_PLATFORM_DSN / DATABASE_URL, or the PostgreSQL host/database/user
and secret-file contract) and requires:

- ALERT_WEBHOOK_URL: one HTTPS notification endpoint.
- ALERT_WEBHOOK_ALLOWED_HOSTS: comma-separated exact bare hostnames; the
  provider rejects redirects, non-HTTPS URLs, userinfo, query strings, fragments
  and non-standard ports.
- ALERT_WEBHOOK_AUTHORIZATION_FILE (optional): set to
  /run/secrets/alert_webhook_authorization when using the Compose profile.
  Set ALERT_WEBHOOK_AUTHORIZATION_HOST_PATH to the host-side secret file path;
  Compose mounts it read-only. The authorization value is never accepted from a
  command-line argument and is not logged.
- ALERT_DISPATCH_POLL_INTERVAL_SECONDS: 0.1–3600 seconds (default 5).
- ALERT_DISPATCH_BATCH_SIZE: 1–500 (default 100).

The worker uses PostgresAlertEventStore and PostgresAuditWriter, and sends
only through WebhookAlertNotificationProvider. The dispatcher persists
delivery state and performs write-ahead audit before the external side effect.
Provider redirects are disabled by the HTTP client. SIGTERM/SIGINT stop the
polling task and close the HTTP client gracefully.

## Run

Configure the settings in the deployment secret/environment manager, then:

    python -m agent_platform.observability.production_alert_worker

For Docker Compose, the optional alerts profile provides a separate process
so the existing generic Outbox-to-Redis relay remains independent. Start it
only after the webhook endpoint, host allowlist and secret mount have been
reviewed:

    docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml --profile alerts up -d platform-alert-dispatcher

## Scope and acceptance boundary

This closes the repository entrypoint/composition gap for alert delivery
dispatch. It does not configure alert rules, elect a single alert evaluator,
or wire evaluation scheduling and Prompt rollout services. Multiple dispatcher
replicas are safe at the outbox claim layer (FOR UPDATE SKIP LOCKED), but
provider behavior and real endpoint delivery still require deployment evidence.
No real webhook, production credential or database was contacted by the tests.
