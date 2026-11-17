# Runtime self-monitoring and hot configuration

## Runtime self-monitoring

RuntimeSelfMonitor accepts host-injected probes for worker heartbeats, event-loop
lag, queue consumers, and internal subsystem health. Each probe has an independent
timeout and criticality flag. Failures are counted consecutively and transition
through warning to critical; a successful probe resets the counter. State
transitions can be forwarded to the existing alert/outbox integration with
on_transition. Exception messages are deliberately excluded from snapshots.

Start and stop the monitor from the application lifespan. The monitor does not
create infrastructure clients or infer readiness from a single process-local
metric. Supply probes with bounded, low-cost operations. Non-critical probe
failures are visible but do not mark the runtime unhealthy.

## Configuration hot reload

HotConfigurationManager validates a complete candidate before acquiring its
activation lock, then checks expected_version under the lock. This prevents
stale concurrent updates from overwriting newer values. Audit is written before
activation. Audit events include versions, digests, changed top-level keys and
activation time, never configuration values.

Subscribers receive old/new immutable snapshots after activation. A subscriber
failure is returned in listener_errors and does not roll back the active
configuration. An identical digest is a no-op and does not increment the version.

The manager is process-local. Multi-worker deployments must distribute a
versioned reload event through the platform's existing event bus and apply the
same expected-version check on each worker. Secrets should remain references to
the configured secret manager, not plaintext values in application configuration.
