# Public API v1 contract

This document defines the compatibility and transport contract for public platform endpoints. The canonical prefix is `/api/platform/v1`; the unversioned `/auth` routes are identity bootstrap endpoints and are not a substitute for versioned resource APIs.

## Compatibility and versioning

- Existing v1 fields and meanings are stable. Additive optional response fields and new endpoints are backward-compatible.
- Removing/renaming fields, changing field types or semantics, tightening accepted values, and changing pagination meaning are breaking changes and require a separately published `v2` route.
- Responses identify their contract version with `X-API-Version: v1`. Clients must not assume an unknown version is compatible.
- JSON uses UTF-8. Timestamps are RFC 3339 with an explicit UTC offset. Unknown response fields must be ignored by clients.

## Authentication and authorization

- Authenticate with the platform Identity middleware using the supported session cookie or service identity credentials. HTTPS is mandatory outside local development.
- Authorization is evaluated for every request against the authenticated principal, tenant, resource and requested action. A valid credential does not imply resource access.
- Tenant identifiers supplied in paths or bodies never override the authenticated principal's tenant. Cross-tenant access is denied.
- Cookie-authenticated state-changing requests must satisfy the configured CSRF protection. Do not put bearer/session credentials in URLs.

## Rate limiting

Rate limits are enforced by the shared Redis-backed limiter and quota controls, not process-local counters. A throttled response is HTTP `429`, public error code `RATE_LIMITED`, and should include `Retry-After` when the server can estimate the retry time. When emitted, `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` describe the applicable bucket. Clients should use bounded exponential backoff with jitter and honor `Retry-After`.

## Idempotency

Clients send `Idempotency-Key` on retryable state-changing requests. Keys are 1–128 safe ASCII characters. The server-side uniqueness scope must include tenant, authenticated principal, HTTP method and normalized route; the same key with a different request payload must return `409 CONFLICT`, not replay a different operation. A successful replay returns the original logical result. Keys must be retained for the documented operation retry window; callers must not assume global or cross-tenant deduplication. The helper in `agent_platform.api.contract` validates keys and constructs the isolation scope; endpoint handlers must connect it to a durable idempotency store before advertising endpoint-level idempotency.

## Pagination

List endpoints use opaque cursor pagination: `limit` defaults to 50 and is bounded to 1–200; `cursor` is an opaque token that clients must return unchanged. Responses use `{"items": [], "next_cursor": null}`. Ordering must be deterministic and cursor-bound to the tenant and query filters. Offset pagination should not be introduced for mutable high-volume collections.

## Error envelope

Errors use `{"error":{"code":"...","message":"...","request_id":"..."}}` with optional safe `details`. Codes are stable machine-readable identifiers; messages are human-readable and must not expose stack traces, credentials, SQL, provider payloads or internal exception text.

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `INVALID_REQUEST` | Malformed request |
| 401 | `UNAUTHENTICATED` | Missing or invalid authentication |
| 403 | `FORBIDDEN` | Authenticated but not authorized |
| 404 | `NOT_FOUND` | Resource absent or not visible |
| 409 | `CONFLICT` | State or idempotency conflict |
| 412 | `PRECONDITION_FAILED` | Conditional request precondition failed |
| 422 | `VALIDATION_ERROR` | Semantic validation failed |
| 429 | `RATE_LIMITED` | Rate/quota limit reached |
| 500 | `INTERNAL_ERROR` | Unexpected server failure |
| 502/503/504 | `UPSTREAM_ERROR` / `SERVICE_UNAVAILABLE` / `UPSTREAM_TIMEOUT` | Dependency failure |

## Implementation boundary

The shared contract helpers are reusable primitives, not automatic middleware. Each route must explicitly apply idempotency storage, pagination, error translation and rate-limit headers as appropriate. Existing identity middleware, per-resource authorizers and Redis-backed controls remain authoritative. OpenAPI generation should expose these schemas and headers as routes migrate to the contract.
