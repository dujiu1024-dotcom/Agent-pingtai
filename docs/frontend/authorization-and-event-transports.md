# Frontend authorization and event transports

## Authorization boundary

- The server bootstrap remains the sole source of `auth.enabled`; frontend environment variables never override it.
- In server no-auth mode, frontend permission gates are bypassed and the UI enters business routes without creating a principal.
- In auth mode, `/auth/me` may provide a `permissions: string[]` field. Missing or malformed permission data fails closed for a route/action that declares a permission.
- Route metadata uses exact permission identifiers; navigation filtering and the `Can` component share the same `canAccess` policy. Route checks are presentation controls only. Every API and resource operation must still be authorized by the backend.
- The current session contract does not yet publish a confirmed permission vocabulary in this repository baseline. The example keys `platform.organization.read` and `platform.rbac.read` are frontend integration identifiers pending confirmation/alignment with the backend identity contract; they are not claims about an already deployed backend policy.

## Query and client state

- TanStack Query remains the only server-state cache. Query functions call the unified `apiClient` and pass the Query AbortSignal.
- Zustand remains limited to transient UI state; server entities must not be copied into the UI store.
- Event transports only validate/deduplicate/dispatch events. `connectQueryEvents` invalidates or safely updates the Query cache; it does not keep a second entity store.

## SSE and WebSocket

- SSE uses `SseEventBus`; WebSocket uses `WebSocketEventBus`. Both implement `EventBusPort` and emit the same `PlatformEvent` envelope.
- Both transports are explicitly started by a feature, not automatically by app bootstrap. This preserves no-auth startup and avoids inventing endpoints or authentication headers.
- WebSocket sends a resume control message containing the last accepted `event_id` after reconnect. The server must implement this message contract or provide an adapter; no live server contract was assumed here.
- Both transports enforce event-id deduplication and per-entity monotonic sequence checks. The Query bridge remains the only cache synchronization layer.

## Integration limits

The frontend repository currently confirms the SSE contract but does not establish a production WebSocket URL, handshake auth scheme, resume message contract, or the exact backend permission vocabulary. These are explicit integration inputs, not guessed defaults. Real-server/device validation is intentionally skipped for this task.
