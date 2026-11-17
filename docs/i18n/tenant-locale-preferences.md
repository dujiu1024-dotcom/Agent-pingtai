# Tenant internationalization and regional preferences

Each tenant has an independent language, IANA timezone and ISO 3166-1 alpha-2 region preference. The preference API is authenticated and tenant-scoped:

- `GET /api/platform/v1/tenant/locale` reads the caller's tenant preference or the configured default.
- `PUT /api/platform/v1/tenant/locale` changes language, timezone and region. It requires the tenant locale-management authorizer and a successful write-ahead audit record. The tenant ID and actor ID always come from the authenticated principal, never the request body.

Supported language conventions currently include zh-CN, en-US, ja-JP, de-DE and fr-FR. Timezones are validated against the IANA zone database. Region values use uppercase two-letter ISO codes. The PostgreSQL migration creates a tenant-keyed preference row with update actor and timestamp.

Formatting uses explicit `LocaleContext` values and does not mutate process-global locale state. Datetimes must be timezone-aware and are converted to the tenant timezone; dates, wall-clock times, numbers and ISO currency codes use deterministic language conventions. Number separators and date conventions are selected by language; region is retained as tenant configuration for region-specific behavior.

The in-memory store is for tests/development. Production composition should use `PostgresLocalePreferenceStore` and supply a tenant-specific locale service, management authorizer and append-only audit writer.
