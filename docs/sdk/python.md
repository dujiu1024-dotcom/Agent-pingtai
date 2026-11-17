# Python SDK

The Python SDK is available from `agent_platform.sdk` and uses only the Python standard library.

```python
import os

from agent_platform.sdk import PlatformClient, RateLimitError, AuthenticationError

client = PlatformClient(
    base_url="https://api.example.com",
    token=os.environ["PLATFORM_API_TOKEN"],
    timeout=10,
    retry_attempts=3,
)
capabilities = client.get("/workbench/capabilities")
updated = client.put("/knowledge/collections/collection-id", {"name": "Research v2"})
client.patch("/knowledge/collections/collection-id", {"description": "Updated"})
client.delete("/knowledge/collections/old-collection-id")
created = client.post(
    "/knowledge/collections",
    {"name": "Research"},
    idempotency_key="create-collection-2026-01",
)
for document in client.iter_pages("/knowledge/documents", limit=100):
    print(document)
```

## Contract
- Supply exactly one bearer `token`; it may be a user access token or an API credential accepted by the platform identity middleware.
- The SDK targets `/api/platform/v1` (`API_VERSION == "v1"`) and sends the contract header `X-API-Version: 1`, a unique request ID, JSON accept headers and a stable user agent.
- Every request uses the configured finite timeout. The default transport refuses redirects so credentials are never forwarded to a different host.
- `get`, `post`, `put`, `patch` and `delete` are available as convenience methods. Retries are bounded. GET/PUT/DELETE may retry; POST/PATCH retries require an explicit `idempotency_key`. HTTP 429 honors `Retry-After` (seconds or HTTP date); transient 5xx/network failures use bounded exponential backoff.
- `PlatformApiError` exposes `status_code`, `code`, `message`, `details`, `request_id`, `body` and response headers. Specialized subclasses distinguish authentication, authorization, rate-limit, conflict and server errors.
- `get_page` returns a typed `Page`; `iter_pages` follows opaque cursors while preserving the server's page order.
- The public package exports stable SDK types from `agent_platform.sdk`. API v1 remains the compatibility target; breaking changes require a new major API path and SDK compatibility policy.
