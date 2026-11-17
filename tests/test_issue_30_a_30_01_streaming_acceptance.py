from __future__ import annotations

import asyncio
import json
import queue
import statistics
import threading
import time
import unittest
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from agent_platform.streaming.run_events import RunEventBroker
from agent_platform.streaming.sse import encode_sse


def _read_sse_event(response):
    fields = {}
    while True:
        line = response.readline()
        if not line:
            raise EOFError("SSE connection closed before the next event")
        if line in (b"\n", b"\r\n"):
            if "data" not in fields:
                fields = {}
                continue
            return {
                "id": fields.get("id"),
                "event": fields.get("event"),
                "data": json.loads(fields["data"]),
            }
        name, separator, value = line.decode("utf-8").rstrip("\r\n").partition(":")
        if not separator:
            continue
        fields[name] = value[1:] if value.startswith(" ") else value


class _SSEReader:
    def __init__(self, response):
        self.response = response
        self.items = queue.Queue()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                self.items.put(_read_sse_event(self.response))
        except Exception as exc:
            self.items.put(exc)

    def next(self, timeout=3):
        item = self.items.get(timeout=timeout)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.response.close()
        self.thread.join(timeout=2)


class RunStreamingAcceptanceTests(unittest.TestCase):
    """Loopback TCP acceptance for token latency and Last-Event-ID replay."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._run_loop, name="run-event-loop", daemon=True,
        )
        self.loop_thread.start()
        self.broker = RunEventBroker(history_limit=256, subscriber_limit=8)
        broker = self.broker
        loop = self.loop

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                run_id = self.path.rsplit("/", 1)[-1]
                last_event_id = self.headers.get("Last-Event-ID")
                try:
                    events = asyncio.run_coroutine_threadsafe(
                        broker.subscribe(
                            tenant_id="tenant-acceptance",
                            run_id=run_id,
                            last_event_id=last_event_id,
                        ),
                        loop,
                    ).result(timeout=3)
                except ValueError:
                    self.send_error(400)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    while True:
                        event = asyncio.run_coroutine_threadsafe(
                            anext(events), loop,
                        ).result(timeout=5)
                        self.wfile.write(encode_sse(event))
                        self.wfile.flush()
                        if event.data.get("event_type") in {
                            "COMPLETE", "ERROR", "RECONNECT_REQUIRED",
                        }:
                            return
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return
                except StopAsyncIteration:
                    return
                finally:
                    asyncio.run_coroutine_threadsafe(events.aclose(), loop).result(timeout=3)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _publish(self, run_id, event_type, data):
        return asyncio.run_coroutine_threadsafe(
            self.broker.publish(
                tenant_id="tenant-acceptance",
                run_id=run_id,
                event_type=event_type,
                data=data,
            ),
            self.loop,
        ).result(timeout=3)

    def _connect(self, run_id, last_event_id=None):
        request = Request(f"{self.base_url}/runs/{run_id}/stream")
        if last_event_id:
            request.add_header("Last-Event-ID", last_event_id)
        return urlopen(request, timeout=5)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)
        self.loop.close()

    def test_loopback_token_p50_under_100ms_and_reconnect_replays_gap(self):
        run_id = "acceptance-run"
        response = self._connect(run_id)
        reader = _SSEReader(response)
        latencies = []
        first_sequences = []

        for sequence in range(1, 31):
            started = time.perf_counter_ns()
            self._publish(run_id, "TOKEN_STREAM", {"token": f"token-{sequence}"})
            event = reader.next()
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
            first_sequences.append(int(event["id"].rsplit(":", 1)[1]))
            self.assertEqual(event["event"], "token_stream")
            self.assertEqual(event["data"]["token"], f"token-{sequence}")

        p50_ms = statistics.median(latencies)
        self.assertLess(
            p50_ms,
            100.0,
            f"loopback token delivery P50 was {p50_ms:.2f}ms (limit: 100ms)",
        )
        self.assertEqual(first_sequences, list(range(1, 31)))

        reader.close()
        # Publish while the client is disconnected. These events must be replayed
        # from the durable-in-process broker history after the last acknowledged ID.
        for sequence in range(31, 36):
            self._publish(run_id, "TOKEN_STREAM", {"token": f"token-{sequence}"})

        resumed_response = self._connect(run_id, last_event_id="acceptance-run:30")
        resumed_reader = _SSEReader(resumed_response)
        replayed = [resumed_reader.next() for _ in range(5)]
        self.assertEqual(
            [event["id"] for event in replayed],
            [f"acceptance-run:{sequence}" for sequence in range(31, 36)],
        )
        self.assertEqual(
            [event["data"]["token"] for event in replayed],
            [f"token-{sequence}" for sequence in range(31, 36)],
        )

        self._publish(run_id, "COMPLETE", {"status": "completed"})
        terminal = resumed_reader.next()
        self.assertEqual(terminal["event"], "run_complete")
        self.assertEqual(terminal["data"]["event_type"], "COMPLETE")
        resumed_reader.close()


if __name__ == "__main__":
    unittest.main()
