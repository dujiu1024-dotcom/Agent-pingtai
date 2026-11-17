# Outbox delivery guarantees and cost circuit breaking

## Outbox relay (C-16-04)

The PostgreSQL outbox is the durable source of truth. Relay claims are leased
with `FOR UPDATE SKIP LOCKED`, published messages retain a stable event ID,
and consumers must deduplicate effects using the tenant/event receipt key.
A crash after Redis publish but before the database acknowledgement can cause
redelivery; it must not cause duplicate business effects.

This increment adds a finite delivery budget. Each claim increments attempts;
transient failures use capped exponential backoff. Rows that reach
`max_attempts` are marked `dead_lettered_at` and excluded from subsequent
claims. Operators can inspect dead-lettered rows and replay only through the
existing explicit, tenant-scoped, time-bounded recovery operation. Do not
delete failed rows or silently reset their delivery counters.

## Cost budgets and provider circuit breaker (C-16-05)

The existing quota service remains authoritative for tenant/project/agent/model
and run token/cost budgets. The model router estimates cost before dispatch,
reserves budget atomically, then settles provider-reported usage. Unknown
pricing or failed reservation/settlement fails closed; soft-limit warnings are
emitted through the configured alert sink.

The new model circuit breaker is keyed by tenant + provider + model. Repeated
transport failures open the route; requests are rejected before provider
dispatch and quota reservation while open. After the reset timeout, exactly one
half-open probe is admitted. Success closes the breaker; failure reopens it.
The breaker is in-process, so multi-replica deployments should provide a shared
implementation or consistent provider-level routing policy.

## Validation boundary

Automated tests cover breaker state transitions, pre-dispatch rejection, outbox
retry exhaustion and dead-letter exclusion. CI remains the source of truth for
repository-wide validation. No production provider, external server or live
tenant budget is exercised here.
