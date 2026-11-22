# Issue #8 / L3-N16 — production storage and connector composition

The production host must call `build_production_asset_connector_runtime` with:
- an async S3-compatible client and the configured bucket (AWS S3 or MinIO);
- an async psycopg connection factory using the platform PostgreSQL DSN;
- the already configured `DataConnectorService` (provider registry, tenant secret resolver,
  repository and sink);
- a reachable clamd endpoint and a stable worker ID.

The composition installs the same PostgreSQL-backed queue into both the API service
and the worker. It does not allow an in-memory queue fallback. The returned scanner
is the ClamAV INSTREAM implementation used by the existing file-processing worker;
the returned object store implements the existing tenant-prefixed ObjectStorage
contract. The host owns the S3 client's async lifecycle and should start/stop
`connector_worker` from its application lifespan.

## Configuration contract

`S3_BUCKET` is mandatory. Supply the S3 endpoint/region/credentials when creating
the async client; do not put credentials in logs or source control. Configure
`CLAMAV_HOST` (and optionally `CLAMAV_PORT`) for clamd. PostgreSQL credentials
must be provided through the existing platform secret/DSN mechanism. S3 encryption
defaults to AES256; KMS mode requires an explicit KMS key ID.

## Verification boundary

Unit tests use injected fakes to verify composition and fail-closed behavior.
No live MinIO, clamd, or PostgreSQL instance is contacted by these tests. Live
service smoke tests are intentionally skipped for this task and remain deployment
acceptance evidence, not inferred from mocked tests.
