# Issue #665 A-665-07 — dependency fault degradation acceptance

This acceptance suite validates the failure contract at dependency boundaries. It
uses deterministic fault injection and the existing PostgreSQL/Redis CI services;
it does not terminate shared service processes or request external infrastructure.

## Executable checks

- `tests/test_issue_665_a_665_07_fault_degradation.py`
  - Vault KV read returns HTTP 503: secret resolution fails closed, does not return
    a stale/default credential, and does not expose the response body or token.
  - PostgreSQL quota store connection failure: `QuotaEnforcedHarnessAdapter`
    propagates the reservation failure and never dispatches the model call.
  - Streaming transport failure after a partial token: SSE iteration surfaces the
    transport error and does not synthesize a successful terminal event.
- `tests/test_reliability_fault_injection.py` (real PostgreSQL + Redis CI services)
  - PostgreSQL receipt-store outage keeps Redis entry pending; after DB connectivity
    returns, the consumer processes it and records completion.
  - Redis consumer restart reclaims pending messages and deduplicates event effects.
- `tests/test_issue_21_c_21_03_fault_injection.py`
  - Redis claim/ack outages, worker retry loop, PostgreSQL receipt failures,
    model timeout retry and idempotent effect replay.

## Degradation contract

| Failure | Expected behavior |
|---|---|
| Redis unavailable | Do not acknowledge/drop work; worker retries/reclaims; durable PostgreSQL outbox/receipt identity supports replay |
| PostgreSQL unavailable | Keep Redis delivery pending; do not run handler without receipt claim; resume after DB connectivity returns |
| Vault unavailable | Secret resolution fails closed; never use empty/stale/default credential |
| Quota database unavailable | Reservation failure blocks model/provider dispatch; no in-memory quota bypass |
| Streaming transport unavailable | Surface stream failure; do not emit success-shaped completion |

## Limits of this evidence

The PostgreSQL test injects a connection-boundary outage and recovery against a
real disposable CI PostgreSQL service. It does **not** promote a standby, validate
replication lag, or prove an actual primary/standby failover. Redis outage/restart
behavior is deterministic fault injection plus service-backed consumer recovery;
it does not kill a production Redis node. Vault is exercised with an injected
HTTP 503 response, not a real Vault cluster. No production traffic, customer data,
real server, or external resource is used.

Therefore this is automated fault-contract evidence, not a production failover
or site-reliability sign-off. Record CI outcomes before checking A-665-07 complete.
