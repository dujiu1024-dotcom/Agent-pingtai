Quota concurrency admission
============================

`TenantFairQuotaAdmissionController` applies FIFO ordering within a tenant and round-robin turns across tenants for callers sharing one controller instance. PostgreSQL reservations remain the cross-process authority for hard concurrency limits.

The in-memory waiting room is bounded by `max_pending_total` (default 1,000) and `max_pending_per_tenant` (default 100). A request that would exceed either bound fails immediately with `BudgetExceeded`; it does not silently drop an existing waiter. Both limits must be positive integers, and the per-tenant limit cannot exceed the global limit.

Use `strategy="reject"` for immediate admission and `strategy="queue"` with an optional positive timeout for bounded waiting. Call `notify_capacity_changed(scope_type, scope_id)` when capacity changes outside this controller (for example, a lease expiry handled by another process). The scheduler does not claim distributed FIFO fairness; cross-worker concurrency correctness is enforced by the shared quota store.
