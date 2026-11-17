# Connector sync worker audit contract

The durable connector sync worker performs external provider reads and persists
provider results into the tenant's connector sink. Production composition must
supply an append-only audit sink when constructing `ConnectorSyncWorker`.

For each active connection attempt, the worker writes a tenant-scoped
`connector.sync` event with actor `system:connector-sync-worker`, job and
connection identifiers, attempt number, and outcome:

1. `started` is written before `DataConnectorService.sync_once`; if this
   write fails, the provider is not called and the queue job is retried.
2. `failed` records only the exception class after a provider/sync failure.
   It never records credentials, provider payloads, item content, or exception
   messages.
3. `succeeded` is written after the durable queue job is completed. If this
   result write fails, the worker logs the audit-store failure but does not
   retry the completed job: replaying a successful provider sync solely because
   its result audit failed could duplicate external work.

Revoked or missing connections are recorded as `skipped` without resolving
credentials or invoking the provider. The sink is host-owned so production can
reuse its durable append-only audit implementation; an in-memory sink is
appropriate only for tests.

This contract provides an audit boundary for connector sync execution. It does
not claim live provider, secret-manager, or production audit-store validation.
