from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.observability.alert_store import PostgresAlertEventStore
from agent_platform.observability.alerts import AlertEvent


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
    async def fetchone(self):
        return self.row
    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.events = {}
        self.last = None
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def transaction(self):
        return self
    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if sql.startswith("INSERT INTO platform_alert_events"):
            event_id, scope, dedupe, rule, metric, value, threshold, severity, fired, description = params
            key = (scope, dedupe)
            if key in self.events:
                return FakeCursor()
            row = {"event_id": event_id, "scope_key": scope, "rule_id": rule,
                   "metric_name": metric, "observed_value": value, "threshold": threshold,
                   "severity": severity, "fired_at": fired, "description": description,
                   "status": "open", "delivery_status": "pending", "delivery_attempts": 0, "acknowledged_by": None, "acknowledged_at": None,
                   "resolved_at": None}
            self.events[key] = row
            return FakeCursor(row)
        if sql.startswith("WITH candidates AS"):
            requested_id, _, limit = params
            rows = []
            for row in self.events.values():
                if row["delivery_status"] in {"pending", "failed"} and (requested_id is None or row["event_id"] == requested_id):
                    row["delivery_status"] = "sending"
                    row["delivery_attempts"] += 1
                    rows.append(dict(row))
                    if len(rows) >= limit:
                        break
            return FakeCursor(rows=rows)
        if sql.startswith("UPDATE platform_alert_events") and "delivery_status=%s" in sql:
            status, success, delivered_at, error, event_id = params
            row = next((v for v in self.events.values() if v["event_id"] == event_id), None)
            if row and row["delivery_status"] == "sending":
                row["delivery_status"] = status
                row["delivered_at"] = delivered_at if success else None
                row["last_delivery_error"] = error
                return FakeCursor(row)
            return FakeCursor()
        if sql.startswith("SELECT event_id FROM platform_alert_events"):
            return FakeCursor({"event_id": self.events[(params[0], params[1])]["event_id"]})
        if sql.startswith("SELECT event_id,scope_key"):
            return FakeCursor(rows=[v for (scope, _), v in self.events.items()
                                    if scope == params[0] and v["status"] in {"open", "acknowledged"}][:params[1]])
        if sql.startswith("UPDATE platform_alert_events"):
            event_id, scope = (params[2], params[3]) if "acknowledged_by" in sql else (params[2], params[3])
            row = next((v for (s, _), v in self.events.items()
                        if s == scope and v["event_id"] == event_id), None)
            if row is None or row["status"] not in ({"open"} if "acknowledged_by" in sql else {"open", "acknowledged"}):
                return FakeCursor()
            if "acknowledged_by" in sql:
                row.update(status="acknowledged", acknowledged_by=params[0], acknowledged_at=params[1])
            else:
                row.update(status="resolved", resolved_by=params[0], resolved_at=params[1])
            return FakeCursor(row)
        raise AssertionError(sql)


class AlertStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_is_idempotent_and_acknowledge_resolve_are_guarded(self):
        connection = FakeConnection()
        async def connect():
            return connection
        store = PostgresAlertEventStore(connect)
        event = AlertEvent("cpu", "cpu_percent", 95, 90, "critical",
                           datetime.now(timezone.utc), "CPU high")
        first = await store.record(event, scope_key="tenant-a", dedupe_key="cpu:window-1")
        second = await store.record(event, scope_key="tenant-a", dedupe_key="cpu:window-1")
        self.assertEqual(first, second)
        self.assertTrue(await store.acknowledge(first, scope_key="tenant-a",
                                                actor_id="operator", now=datetime.now(timezone.utc)))
        self.assertFalse(await store.acknowledge(first, scope_key="tenant-a",
                                                 actor_id="operator", now=datetime.now(timezone.utc)))
        self.assertTrue(await store.resolve(first, scope_key="tenant-a",
                                            actor_id="operator", now=datetime.now(timezone.utc)))
        self.assertFalse(await store.resolve(first, scope_key="tenant-a",
                                             actor_id="operator", now=datetime.now(timezone.utc)))


    async def test_delivery_outbox_claims_once_and_records_success_or_failure(self):
        connection = FakeConnection()
        async def connect():
            return connection
        store = PostgresAlertEventStore(connect)
        event = AlertEvent("cpu", "cpu_percent", 95, 90, "critical",
                           datetime.now(timezone.utc), "CPU high")
        event_id = await store.record(event, scope_key="tenant-a", dedupe_key="cpu:delivery")
        claimed = await store.claim_pending_deliveries(event_id=event_id, limit=1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["event_id"], event_id)
        self.assertEqual(await store.claim_pending_deliveries(event_id=event_id, limit=1), [])
        await store.mark_delivery(
            event_id, success=False, now=datetime.now(timezone.utc),
            error="provider unavailable",
        )
        claimed_again = await store.claim_pending_deliveries(event_id=event_id, limit=1)
        self.assertEqual(len(claimed_again), 1)
        await store.mark_delivery(event_id, success=True, now=datetime.now(timezone.utc))
        row = next(iter(connection.events.values()))
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(row["delivery_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
