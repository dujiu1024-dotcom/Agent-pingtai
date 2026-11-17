# A-30-01 SSE latency and reconnect acceptance

This acceptance suite exercises the existing `RunEventBroker` and SSE encoder over a
real loopback TCP socket (not an in-memory ASGI transport).

## Automated assertions

- Publishes 30 sequential `TOKEN_STREAM` events and measures publish-to-client-read
  latency for each event. The median (P50) must be below 100 ms.
- Disconnects after event 30, publishes events 31–35 while disconnected, and reconnects
  with `Last-Event-ID: acceptance-run:30`.
- Asserts the resumed stream returns exactly event IDs 31–35 in order, with the
  original token payloads, followed by the terminal `run_complete` event.
- The test uses a loopback HTTP server and the production broker/encoder classes;
  it does not mock event publication, history, cursor validation, or SSE framing.

## Run

```bash
python -m unittest tests.test_issue_30_a_30_01_streaming_acceptance -v
ruff check tests/test_issue_30_a_30_01_streaming_acceptance.py
```

## Evidence boundary

The latency assertion is a local loopback transport measurement, not a claim about
internet, reverse-proxy, browser, or production-host latency. A deployment still needs
a real-host measurement with its normal proxy/network path and representative model
token cadence. The replay assertion validates the in-process broker's retained history;
production hosts must inject a shared broker/pub-sub adapter across API and worker
processes, as documented by `RunEventBroker`.
