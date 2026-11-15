import unittest

from agent_platform.events.consumer import EventEnvelope, RedisStreamsConsumer
from agent_platform.events.receipts import EventReceiptClaim


class FakeReceipts:
    def __init__(self):
        self.rows = {}
        self.active = set()

    async def claim(self, *, tenant_id, consumer_name, event_id):
        key = (tenant_id, consumer_name, event_id)
        row = self.rows.get(key)
        if row and row["status"] in {"completed", "dead_lettered"}:
            return None
        if key in self.active:
            return None
        attempts = (row["attempts"] + 1) if row else 1
        self.active.add(key)
        self.rows[key] = {"status": "processing", "attempts": attempts}
        return EventReceiptClaim(tenant_id, consumer_name, event_id, f"lease-{attempts}", attempts)

    async def is_completed(self, *, tenant_id, consumer_name, event_id):
        row = self.rows.get((tenant_id, consumer_name, event_id))
        return bool(row and row["status"] in {"completed", "dead_lettered"})

    async def complete(self, claim):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        if key not in self.active:
            return False
        self.active.remove(key)
        self.rows[key]["status"] = "completed"
        return True

    async def fail(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        self.active.discard(key)
        self.rows[key]["last_error"] = error
        return True

    async def dead_letter(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        if key not in self.active:
            return False
        self.active.remove(key)
        self.rows[key].update(status="dead_lettered", last_error=error)
        return True


class FakeTransport:
    def __init__(self, messages=None):
        self.pending = list(messages or [])
        self.fresh = []
        self.acked = []
        self.dead_letters = []

    async def claim_pending(self, **kwargs):
        return list(self.pending)

    async def read_group(self, **kwargs):
        messages, self.fresh = self.fresh, []
        return messages

    async def acknowledge(self, *, message_id, **kwargs):
        self.acked.append(message_id)
        self.pending = [row for row in self.pending if row[0] != message_id]
        return 1

    async def dead_letter(self, *, message_id, fields, reason, **kwargs):
        self.dead_letters.append({"message_id": message_id, "fields": fields, "reason": reason})
        self.acked.append(message_id)
        self.pending = [row for row in self.pending if row[0] != message_id]
        return "dlq-1"


class RedisStreamsConsumerTests(unittest.IsolatedAsyncioTestCase):
    def make_consumer(self, transport, receipts, handler, max_attempts=5):
        return RedisStreamsConsumer(
            transport=transport, receipts=receipts, tenant_id="tenant-a",
            topic="topic-a", group="group-a", consumer_name="worker-a",
            handler=handler, max_attempts=max_attempts,
        )

    async def test_processes_event_and_acknowledges_after_receipt_completion(self):
        message = ("1-0", {"event_id": "event-1", "payload": '{"value": 7}'})
        transport, receipts, handled = FakeTransport([message]), FakeReceipts(), []

        async def handler(event: EventEnvelope):
            handled.append(event)

        result = await self.make_consumer(transport, receipts, handler).consume_batch()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(transport.acked, ["1-0"])
        self.assertEqual(handled[0].payload, {"value": 7})

    async def test_completed_duplicate_is_acknowledged_without_reprocessing(self):
        receipts, transport, handled = FakeReceipts(), FakeTransport(), []

        async def handler(event):
            handled.append(event.event_id)

        consumer = self.make_consumer(transport, receipts, handler)
        claim = await receipts.claim(tenant_id="tenant-a", consumer_name="worker-a", event_id="event-2")
        receipts.active.clear()
        receipts.rows[("tenant-a", "worker-a", "event-2")]["status"] = "completed"
        transport.fresh = [("2-0", {"event_id": "event-2", "payload": "{}"})]
        result = await consumer.consume_batch()
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(handled, [])
        self.assertEqual(transport.acked, ["2-0"])
        self.assertIsNotNone(claim)

    async def test_failed_handler_releases_receipt_and_leaves_message_pending(self):
        message = ("3-0", {"event_id": "event-3", "payload": "{}"})
        transport, receipts, attempts = FakeTransport([message]), FakeReceipts(), []

        async def handler(event):
            attempts.append(event.event_id)
            if len(attempts) == 1:
                raise RuntimeError("temporary")

        consumer = self.make_consumer(transport, receipts, handler)
        first = await consumer.consume_batch()
        self.assertEqual(first["failed"], 1)
        self.assertEqual(transport.acked, [])
        second = await consumer.consume_batch()
        self.assertEqual(second["processed"], 1)
        self.assertEqual(transport.acked, ["3-0"])
        self.assertEqual(len(attempts), 2)

    async def test_invalid_payload_is_sent_to_dlq_and_acknowledged(self):
        transport = FakeTransport([("4-0", {"event_id": "event-4", "payload": "not-json"})])
        receipts = FakeReceipts()

        async def handler(event):
            self.fail("handler must not receive invalid payload")

        result = await self.make_consumer(transport, receipts, handler).consume_batch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(transport.acked, ["4-0"])
        self.assertEqual(transport.dead_letters[0]["fields"]["event_id"], "event-4")

    async def test_handler_failure_moves_to_dlq_after_max_attempts(self):
        message = ("5-0", {"event_id": "event-5", "payload": "{}"})
        transport, receipts = FakeTransport([message]), FakeReceipts()

        async def handler(event):
            raise RuntimeError("permanent")

        consumer = self.make_consumer(transport, receipts, handler, max_attempts=2)
        first = await consumer.consume_batch()
        self.assertEqual(first["failed"], 1)
        second = await consumer.consume_batch()
        self.assertEqual(second["dead_lettered"], 1)
        self.assertEqual(transport.acked, ["5-0"])
        self.assertEqual(receipts.rows[("tenant-a", "worker-a", "event-5")]["status"], "dead_lettered")


if __name__ == "__main__":
    unittest.main()
