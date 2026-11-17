# Public API contract (v1)

## Compatibility and version negotiation
The stable public surface is rooted at `/api/platform/v1`. Major versions are immutable contracts: additive optional fields and endpoints are compatible; removing/renaming fields, changing types/meaning, or changing authorization semantics requires a new major path. Clients may send `X-API-Version: 1`; unsupported explicit versions receive HTTP 400 with `unsupported_api_version`. Responses expose `X-API-Version` and `X-Request-ID`.

## Authentication and authorization
Use the existing platform identity boundary: bearer API credentials or authenticated sessions are verified by `IdentityASGIMiddleware`. Business routes resolve the authenticated principal and authorize every resource action within its tenant/project scope. Never accept tenant or actor identity from request bodies. Missing credentials return 401; unauthorized operations return 403 (or tenant-safe 404 where existence must not be disclosed).

## Rate limiting
The identity boundary applies a Redis-backed sliding-window limiter to login attempts. Public integrations should apply a shared Redis limiter keyed by tenant + credential + route class; return 429 and `Retry-After`. The in-memory limiter is development-only and must not enforce multi-worker production quotas.

## Idempotency
Mutating requests should accept `Idempotency-Key`. Helpers bind the key to tenant, method, path and body fingerprint; matching replays return the stored response and mismatched reuse returns 409 `idempotency_key_conflict`. The included in-memory store is test/development only; production must inject a durable shared store with atomic claim/complete semantics and 24-hour default retention. Do not execute a side effect until the key is atomically claimed.

## Pagination
Collection endpoints use `limit` (1–200) and opaque URL-safe `cursor`; responses return `items`, `next_cursor`, and `has_more`. Cursor values must encode stable unique ordering (normally created_at + id) and be bound to tenant/filter context in production.

## Errors
Stable error shape: `{"error":{"code":"...","message":"...","details":{}},"request_id":"..."}`. Use machine-readable codes, not message parsing. Status mapping: 400 invalid input/version/cursor; 401 unauthenticated; 403 forbidden; 404 absent or concealed resource; 409 state/idempotency conflict; 410 expired resource; 422 semantic validation; 429 rate limited; 500 unexpected failure; 503 dependency unavailable.

## OpenAPI and evolution
FastAPI's generated OpenAPI document is the machine-readable endpoint schema. Keep examples and response envelopes aligned. Deprecations require a migration window and documented replacement; never silently repurpose fields or routes.
