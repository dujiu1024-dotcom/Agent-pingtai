from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_platform.identity.models import Principal
from agent_platform.tools.production_reconciliation import build_tool_effect_reconciliation_service
from agent_platform.security.audit import PostgresAuditWriter


class ToolEffectProductionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.principal = Principal(
            user_id="operator-1",
            tenant_id="tenant-1",
            session_id="session-1",
            email="operator@example.test",
            display_name="Operator",
        )
        self.identity_repository = SimpleNamespace(
            is_active_member=AsyncMock(return_value=True),
        )
        self.authorize_run = AsyncMock(return_value=True)
        self.audit_writer = AsyncMock(spec=PostgresAuditWriter)
        self.connection_factory = AsyncMock()

    def build_service(self):
        return build_tool_effect_reconciliation_service(
            principal=self.principal,
            identity_repository=self.identity_repository,
            authorize_run=self.authorize_run,
            connection_factory=self.connection_factory,
            audit_writer=self.audit_writer,
        )

    async def test_resolver_uses_active_membership_and_run_authorization(self) -> None:
        service = self.build_service()
        allowed = await service.authorize_operator(
            "operator-1", "tenant-1", "run-1", "call-1",
        )
        self.assertTrue(allowed)
        self.identity_repository.is_active_member.assert_awaited_once_with(
            tenant_id="tenant-1", user_id="operator-1",
        )
        self.authorize_run.assert_awaited_once()
        authorized_principal, resource_type, run_id, action = self.authorize_run.await_args.args
        self.assertEqual(authorized_principal.user_id, "operator-1")
        self.assertEqual(authorized_principal.tenant_id, "tenant-1")
        self.assertEqual((resource_type, run_id, action), ("run", "run-1", "reconcile"))

    async def test_inactive_membership_denies_without_run_authorization(self) -> None:
        self.identity_repository.is_active_member.return_value = False
        service = self.build_service()
        allowed = await service.authorize_operator(
            "operator-1", "tenant-1", "run-1", "call-1",
        )
        self.assertFalse(allowed)
        self.authorize_run.assert_not_awaited()

    async def test_operator_or_tenant_mismatch_fails_closed(self) -> None:
        service = self.build_service()
        self.assertFalse(await service.authorize_operator(
            "other-operator", "tenant-1", "run-1", "call-1",
        ))
        self.assertFalse(await service.authorize_operator(
            "operator-1", "tenant-2", "run-1", "call-1",
        ))
        self.identity_repository.is_active_member.assert_not_awaited()
        self.authorize_run.assert_not_awaited()

    async def test_audit_adapter_writes_started_and_completed_to_postgres_writer(self) -> None:
        service = self.build_service()
        await service.audit_sink.record(
            tenant_id="tenant-1", actor_id="operator-1",
            action="tool_effect.reconcile", resource="run-1/call-1",
            action_digest="a" * 64, outcome="reconciliation_started",
            run_id="run-1", detail="confirmed_succeeded",
        )
        await service.audit_sink.record(
            tenant_id="tenant-1", actor_id="operator-1",
            action="tool_effect.reconcile", resource="run-1/call-1",
            action_digest="a" * 64, outcome="reconciliation_completed",
            run_id="run-1", detail="confirmed_succeeded: checked provider",
        )
        self.assertEqual(self.audit_writer.await_count, 2)
        first, second = self.audit_writer.await_args_list
        self.assertEqual(first.kwargs["outcome"], "started")
        self.assertEqual(second.kwargs["outcome"], "succeeded")
        self.assertEqual(first.kwargs["subject_id"], "run-1/call-1")
        self.assertEqual(first.kwargs["detail"]["action_digest"], "a" * 64)
        self.assertEqual(second.kwargs["detail"]["run_id"], "run-1")

    async def test_unknown_audit_outcome_is_rejected(self) -> None:
        service = self.build_service()
        with self.assertRaises(ValueError):
            await service.audit_sink.record(
                tenant_id="tenant-1", actor_id="operator-1",
                action="tool_effect.reconcile", resource="run-1/call-1",
                action_digest="a" * 64, outcome="unknown-state",
                run_id="run-1", detail="details",
            )
        self.audit_writer.assert_not_awaited()
