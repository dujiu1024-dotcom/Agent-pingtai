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
    def make_consumer(self, transport, receipts, handler, max_attempts=5, observer=None):
        return RedisStreamsConsumer(
            transport=transport, receipts=receipts, tenant_id="tenant-a",
            topic="topic-a", group="group-a", consumer_name="worker-a",
            handler=handler, max_attempts=max_attempts, observer=observer,
        )

    async def test_processes_event_and_acknowledges_after_receipt_completion(self):
        message = ("1-0", {"event_id": "00000000-0000-0000-0000-000000000001", "payload": '{"value": 7}'})
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
        claim = await receipts.claim(tenant_id="tenant-a", consumer_name="worker-a", event_id="00000000-0000-0000-0000-000000000002")
        receipts.active.clear()
        receipts.rows[("tenant-a", "worker-a", "00000000-0000-0000-0000-000000000002")]["status"] = "completed"
        transport.fresh = [("2-0", {"event_id": "00000000-0000-0000-0000-000000000002", "payload": "{}"})]
        result = await consumer.consume_batch()
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(handled, [])
        self.assertEqual(transport.acked, ["2-0"])
        self.assertIsNotNone(claim)

    async def test_failed_handler_releases_receipt_and_leaves_message_pending(self):
        message = ("3-0", {"event_id": "00000000-0000-0000-0000-000000000003", "payload": "{}"})
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
        transport = FakeTransport([("4-0", {"event_id": "00000000-0000-0000-0000-000000000004", "payload": "not-json"})])
        receipts = FakeReceipts()

        async def handler(event):
            self.fail("handler must not receive invalid payload")

        result = await self.make_consumer(transport, receipts, handler).consume_batch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(transport.acked, ["4-0"])
        self.assertEqual(transport.dead_letters[0]["fields"]["event_id"], "00000000-0000-0000-0000-000000000004")

    async def test_invalid_event_id_is_dead_lettered_before_receipt_claim(self):
        transport = FakeTransport([
            ("poison-id", {"event_id": "not-a-uuid", "payload": "{}"})
        ])
        receipts = FakeReceipts()

        async def handler(event):
            self.fail("handler must not receive an invalid event ID")

        result = await self.make_consumer(transport, receipts, handler).consume_batch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(transport.acked, ["poison-id"])
        self.assertEqual(receipts.rows, {})

    async def test_repeated_worker_interruptions_cannot_exceed_max_attempts(self):
        message = ("6-0", {"event_id": "00000000-0000-0000-0000-000000000006", "payload": "{}"})
        transport, receipts, handled = FakeTransport([message]), FakeReceipts(), []

        async def handler(event):
            handled.append(event.event_id)

        receipts.rows[("tenant-a", "worker-a", "00000000-0000-0000-0000-000000000006")] = {
            "status": "processing", "attempts": 5
        }
        result = await self.make_consumer(
            transport, receipts, handler, max_attempts=5
        ).consume_batch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(handled, [])
        self.assertEqual(transport.acked, ["6-0"])

    async def test_handler_failure_moves_to_dlq_after_max_attempts(self):
        message = ("5-0", {"event_id": "00000000-0000-0000-0000-000000000005", "payload": "{}"})
        transport, receipts = FakeTransport([message]), FakeReceipts()

        async def handler(event):
            raise RuntimeError("permanent")

        consumer = self.make_consumer(transport, receipts, handler, max_attempts=2)
        first = await consumer.consume_batch()
        self.assertEqual(first["failed"], 1)
        second = await consumer.consume_batch()
        self.assertEqual(second["dead_lettered"], 1)
        self.assertEqual(transport.acked, ["5-0"])
        self.assertEqual(receipts.rows[("tenant-a", "worker-a", "00000000-0000-0000-0000-000000000005")]["status"], "dead_lettered")


    async def test_event_handler_is_traced_without_capturing_payload(self):
        from contextlib import contextmanager

        class Span:
            def __init__(self):
                self.updates = []
            def update(self, **kwargs):
                self.updates.append(kwargs)

        class Observer:
            def __init__(self):
                self.calls = []
                self.span = Span()
            @contextmanager
            def observe(self, *, name, input, metadata):
                self.calls.append((name, input, metadata))
                yield self.span

        observer = Observer()
        transport = FakeTransport([
            ("trace-1", {"event_id": "00000000-0000-0000-0000-000000000007", "payload": '{"api_key":"must-not-be-traced"}'})
        ])
        receipts = FakeReceipts()

        async def handler(event):
            self.assertEqual(event.payload["api_key"], "must-not-be-traced")

        result = await self.make_consumer(
            transport, receipts, handler, observer=observer,
        ).consume_batch()
        self.assertEqual(result["processed"], 1)
        name, input_data, metadata = observer.calls[0]
        self.assertEqual(name, "event.consume")
        self.assertEqual(input_data, {"topic": "topic-a", "event_id": "00000000-0000-0000-0000-000000000007"})
        self.assertNotIn("payload", input_data)
        self.assertEqual(metadata["consumer"], "worker-a")
        self.assertEqual(observer.span.updates[0]["output"]["status"], "succeeded")

    async def test_event_handler_failure_is_recorded_without_error_text(self):
        from contextlib import contextmanager

        class Span:
            def __init__(self):
                self.updates = []
            def update(self, **kwargs):
                self.updates.append(kwargs)

        class Observer:
            def __init__(self):
                self.span = Span()
            @contextmanager
            def observe(self, **kwargs):
                yield self.span

        observer = Observer()
        transport = FakeTransport([
            ("trace-2", {"event_id": "00000000-0000-0000-0000-000000000008", "payload": "{}"})
        ])

        async def handler(event):
            raise RuntimeError("secret=must-not-be-recorded")

        result = await self.make_consumer(
            transport, FakeReceipts(), handler, observer=observer,
        ).consume_batch()
        self.assertEqual(result["failed"], 1)
        output = observer.span.updates[0]["output"]
        self.assertEqual(output, {"status": "failed", "error_type": "RuntimeError"})
        self.assertNotIn("must-not-be-recorded", repr(output))


