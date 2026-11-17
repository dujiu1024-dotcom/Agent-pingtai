# Public API contract (v1)

## Compatibility and version negotiation
The stable public surface is rooted at `/api/platform/v1`. Major versions are immutable contracts: additive optional fields and endpoints are compatible; removing/renaming fields, changing types/meaning, or changing authorization semantics requires a new major path. Clients may send `X-API-Version: 1`; unsupported explicit versions receive HTTP 400 with `unsupported_api_version`. Responses expose `X-API-Version` and `X-Request-ID`.

## Authentication and authorization
Use the existing platform identity boundary: bearer API credentials or authenticated sessions are verified by `IdentityASGIMiddleware`. Business routes resolve the authenticated principal and authorize every resource action within its tenant/project scope. Never accept tenant or actor identity from request bodies. Missing credentials return 401; unauthorized operations return 403 (or tenant-safe 404 where existence must not be disclosed).

## Rate limiting
The identity boundary applies a Redis-backed sliding-window limiter to login attempts. The platform API middleware applies a shared Redis limiter keyed by client IP, a SHA-256 fingerprint of the bearer credential or session cookie (never the raw credential), HTTP method, and top-level resource class. Tenant-bound API credentials therefore receive independent buckets while the IP component remains an unauthenticated abuse-control boundary. Return 429 with `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`, and `Retry-After`. The limiter backend is shared across instances; backend failure fails closed with 503.

## Idempotency
Mutating requests should accept `Idempotency-Key`. `ApiIdempotencyService` binds the key to tenant, principal, method, route and exact request-body fingerprint. Matching retries replay the stored status, headers and body; mismatched reuse returns 409 `IDEMPOTENCY_KEY_CONFLICT`; a concurrent in-flight request returns 409 `IDEMPOTENCY_REQUEST_IN_PROGRESS`. Production uses `PostgresIdempotencyBackend` with migration `078_api_idempotency.sql`: the unique scope and row lock provide atomic claims, owner tokens fence completion/release, abandoned pending claims can be reclaimed after a bounded lease, and completed responses are retained for 24 hours by default (maximum 7 days). The in-memory backend is test/development only. Execute the side effect only after `claim` returns ownership; if completion loses its lease after the side effect, treat the outcome as uncertain and reconcile rather than blindly retrying the mutation.

## Pagination
Collection endpoints use `limit` (1–200) and opaque URL-safe `cursor`; responses return `items`, `next_cursor`, and `has_more`. Cursor values must encode stable unique ordering (normally created_at + id) and be bound to tenant/filter context in production.

## Errors
Stable error shape: `{"error":{"code":"...","message":"...","details":{}},"request_id":"..."}`. Use machine-readable codes, not message parsing. Status mapping: 400 invalid input/version/cursor; 401 unauthenticated; 403 forbidden; 404 absent or concealed resource; 409 state/idempotency conflict; 410 expired resource; 422 semantic validation; 429 rate limited; 500 unexpected failure; 503 dependency unavailable.

## OpenAPI and evolution
FastAPI's generated OpenAPI document is the machine-readable endpoint schema. Keep examples and response envelopes aligned. Deprecations require a migration window and documented replacement; never silently repurpose fields or routes.

## Version deprecation and sunset policy

A new major version does not by itself deprecate an existing version. When an operator
explicitly schedules retirement for a version, route owners may attach
`api_version_lifecycle_headers` to responses:

- `Deprecation` uses the RFC 9745 structured date form.
- `Sunset` uses the RFC 8594 HTTP-date form and must be later than the deprecation instant.
- `Link: rel="successor-version"` points to a same-origin absolute-path successor.
- Sunset/successor metadata cannot be emitted without a deprecation instant. Naive timestamps
  and unsafe link values are rejected.

The helper is deliberately opt-in; it does not silently change the lifecycle of v1
or advertise a successor that has not been configured by the API owner.
