# A-12-03 Runtime execution acceptance

This acceptance suite exercises the existing runtime lifecycle and structured-output boundary without external model services or production infrastructure.

Run locally:

```bash
python -m unittest tests.test_issue_12_a_12_03_runtime_acceptance -v
```

Coverage:
- Cancellation moves a running Run to cancelling, cancels active work, and can be finalized as cancelled with a monotonic state version.
- Timeout cancels the operation and persists the timed_out terminal state and error.
- Recovery loads the durable checkpoint reference, transitions the interrupted Run back to running, and invokes the resume callback.
- Interrupted work without a checkpoint fails closed rather than restarting from an unknown state.
- Invalid structured output receives only the configured bounded repair attempt; valid repaired JSON is marked valid and reports attempt count.
- Exhausting the repair budget raises StructuredOutputValidationError with the number of attempts instead of returning malformed output.

The suite uses the in-memory state adapter and a deterministic fake Harness only for repeatable contract testing. It does not claim production multi-worker, PostgreSQL, or real-provider verification; those are separate deployment checks and are intentionally skipped for this task.
