"""A-665-07 acceptance: dependency outages must degrade without false success."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.security.secrets import SecretManager, SecretVersion
from agent_platform.security.vault_kv_v2 import VaultKvV2SecretBackend
from agent_platform.streaming.sse import SSEEvent, iter_sse


class _ActiveSecretMetadata:
    async def list_versions(self, secret_ref: str) -> list[SecretVersion]:
        return [SecretVersion(secret_ref, 1, "active", datetime.now(timezone.utc))]


class _NeverCalledModel:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _request):
        self.calls += 1
        return SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 1})


class Issue665A66507FaultDegradationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vault_outage_fails_secret_resolution_closed_without_stale_fallback(self):
        requests = []

        def unavailable_vault(method, url, headers, body, timeout):
            requests.append((method, url))
            return 503, b'{"errors":["vault unavailable"]}'

        backend = VaultKvV2SecretBackend(
            address="https://vault.invalid",
            token="test-token-not-a-real-credential",
            request_func=unavailable_vault,
        )
        manager = SecretManager(backend=backend, metadata=_ActiveSecretMetadata())

        with self.assertRaisesRegex(RuntimeError, "Vault secret read failed") as raised:
            await manager.resolve(secret_ref="secret://tenant-a/provider/key")

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "GET")
        # The backend reports only the HTTP status, not Vault response body/token.
        self.assertNotIn("test-token-not-a-real-credential", str(raised.exception))
        self.assertNotIn("vault unavailable", str(raised.exception))

    async def test_postgres_quota_store_outage_blocks_model_dispatch(self):
        async def postgres_primary_unavailable():
            raise ConnectionError("simulated PostgreSQL primary unavailable")

        store = PostgresQuotaStore(postgres_primary_unavailable)

        class QuotaService:
            async def reserve_run(self, **_kwargs):
                # The production quota service delegates its hard-budget decision
                # to the shared PostgreSQL store; connection loss is not a bypass.
                return await store.get_budget(
                    "tenant-a", "tenant", "tenant-a", datetime.now(timezone.utc)
                )

        model = _NeverCalledModel()
        adapter = QuotaEnforcedHarnessAdapter(
            model,
            quota_service=QuotaService(),
            estimate_request=lambda _request: (10, Decimal("0.01")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
        )
        request = SimpleNamespace(
            context=SimpleNamespace(tenant_id="tenant-a", run_id="run-a")
        )

        with self.assertRaisesRegex(ConnectionError, "primary unavailable"):
            await adapter.run(request)
        self.assertEqual(model.calls, 0)

    async def test_stream_transport_failure_never_emits_success_terminal_event(self):
        async def broken_source():
            yield SSEEvent("token_stream", {"token": "partial"})
            raise ConnectionError("simulated stream transport outage")

        stream = iter_sse(broken_source(), heartbeat_interval=0.01)
        first = await anext(stream)
        self.assertIn(b"event: token_stream", first)
        self.assertIn(b'"token":"partial"', first)
        with self.assertRaisesRegex(ConnectionError, "stream transport outage"):
            await anext(stream)
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
