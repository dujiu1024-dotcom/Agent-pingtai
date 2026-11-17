"""S-665-03 deterministic dependency fault drills.

The suite injects failures at adapter boundaries. It never stops or connects to
shared infrastructure; production Redis/PG/Vault failover remains a deployment
acceptance activity.
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from agent_platform.events.redis_streams import RedisStreamsTransport
from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter
from agent_platform.quotas.service import InMemoryQuotaStore, QuotaService
from agent_platform.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    DegradationPolicy,
    FailureMode,
    ResilientExecutor,
)
from agent_platform.security.vault_kv_v2 import VaultKvV2SecretBackend
from agent_platform.streaming.run_events import RunEventBroker


class UnavailableRedis:
    async def xadd(self, *_args, **_kwargs):
        raise ConnectionError("injected Redis outage")


class PrimarySwitchQuotaStore:
    """QuotaStore test adapter whose active PG primary can be promoted."""

    def __init__(self):
        self.primary = "pg-primary"
        self.primary_available = False
        self.store = InMemoryQuotaStore()

    def _require_primary(self):
        if not self.primary_available:
            raise ConnectionError(f"{self.primary} unavailable")

    async def get_budget(self, *args):
        self._require_primary()
        return await self.store.get_budget(*args)

    async def put_budget(self, budget):
        self._require_primary()
        await self.store.put_budget(budget)

    async def reserve(self, reservation):
        self._require_primary()
        return await self.store.reserve(reservation)

    async def settle(self, reservation_id, event):
        self._require_primary()
        await self.store.settle(reservation_id, event)

    async def release(self, reservation_id):
        self._require_primary()
        return await self.store.release(reservation_id)

    async def promote_standby(self):
        self.primary = "pg-standby-promoted"
        self.primary_available = True


class S66503FaultDrillTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_outage_opens_circuit_and_streaming_replays_local_degraded_event(self):
        transport = RedisStreamsTransport("redis://fault-injection.invalid")
        transport._client = UnavailableRedis()
        with self.assertRaisesRegex(ConnectionError, "Redis outage"):
            await transport.publish(
                tenant_id="tenant-fault",
                topic="run-events",
                event_id=str(uuid4()),
                payload={"type": "TOKEN_STREAM", "delta": "not-persisted"},
            )

        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        executor = ResilientExecutor(
            DegradationPolicy("redis-event-publish", FailureMode.OPTIONAL),
            circuit_breaker=breaker,
        )
        result = executor.execute(
            lambda: (_ for _ in ()).throw(ConnectionError("Redis unavailable")),
            fallback=lambda _error: {"delivery": "process_local_replay_only"},
        )
        self.assertEqual(result["delivery"], "process_local_replay_only")
        called = []
        second = executor.execute(
            lambda: called.append("redis"),
            fallback=lambda _error: {"delivery": "process_local_replay_only"},
        )
        self.assertEqual(second["delivery"], "process_local_replay_only")
        self.assertEqual(called, [])
        with self.assertRaises(CircuitBreakerOpen):
            breaker.call(lambda: called.append("unexpected"))

        broker = RunEventBroker(history_limit=8)
        published = await broker.publish(
            tenant_id="tenant-fault",
            run_id="run-fault",
            event_type="ERROR",
            data={"degraded": True, "delivery": result["delivery"]},
        )
        replay = await broker.subscribe(
            tenant_id="tenant-fault",
            run_id="run-fault",
            last_event_id=None,
        )
        event = await anext(replay)
        await replay.aclose()
        self.assertEqual(event.event_id, published.event_id)
        self.assertTrue(event.data["degraded"])
        self.assertEqual(event.data["delivery"], "process_local_replay_only")

    async def test_postgres_primary_loss_blocks_quota_dispatch_until_standby_promotion(self):
        store = PrimarySwitchQuotaStore()
        calls = []

        class Harness:
            async def run(self, _request):
                calls.append("provider-dispatched")
                return SimpleNamespace(
                    usage={"input_tokens": 3, "output_tokens": 2},
                    tool_calls=(),
                )

        adapter = QuotaEnforcedHarnessAdapter(
            Harness(),
            quota_service=QuotaService(store),
            estimate_request=lambda _request: (3, 2, Decimal("0.01")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.005"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-fault",
            run_id="run-pg-failover",
            model_template=SimpleNamespace(template_id="test-model", version=1),
            project_id=None,
            agent_id="agent-fault",
        ))

        with self.assertRaisesRegex(ConnectionError, "pg-primary unavailable"):
            await adapter.run(request)
        self.assertEqual(calls, [])

        await store.promote_standby()
        result = await adapter.run(request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.usage["input_tokens"], 3)
        self.assertEqual(store.primary, "pg-standby-promoted")

    async def test_vault_outage_fails_closed_without_secret_or_stale_value_fallback(self):
        def unavailable(_method, _url, _headers, _body, _timeout):
            raise ConnectionError("injected Vault outage")

        backend = VaultKvV2SecretBackend(
            address="https://vault.invalid",
            token="test-token-not-a-real-credential",
            request_func=unavailable,
        )
        with self.assertRaisesRegex(ConnectionError, "Vault outage"):
            await backend.get("secret://tenant-fault/provider", 1)

    async def test_vault_http_unavailable_response_is_not_treated_as_missing_secret(self):
        backend = VaultKvV2SecretBackend(
            address="https://vault.invalid",
            token="test-token",
            request_func=lambda *_args: (503, b"service unavailable"),
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await backend.get("secret://tenant-fault/provider", 1)


if __name__ == "__main__":
    unittest.main()
