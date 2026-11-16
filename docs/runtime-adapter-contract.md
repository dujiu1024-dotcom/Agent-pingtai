# Runtime Adapter Contract

This document defines the platform boundary for Harness adapters. The source of
truth is `agent_platform/runtime/protocols.py`.

## Contract and adapter versions

- The platform contract version is exported as
  `RUNTIME_ADAPTER_CONTRACT_VERSION`.
- Every concrete adapter publishes a `RuntimeAdapterDescriptor` containing a
  stable adapter ID, the adapter implementation version, the contract version it
  implements, and its immutable `HarnessCapabilities`.
- Versions use strict `MAJOR.MINOR.PATCH` syntax.
- Compatibility is fail-closed: contract major versions must match, and an
  adapter may not implement a contract older than the platform requirement.
  Newer minor/patch revisions within the same major are accepted.
- Adapter package versions are diagnostic metadata; they do not override
  contract compatibility.

## Capability declarations

Capabilities are explicit declarations, not inferred from SDK availability:

| Capability | Meaning |
| --- | --- |
| `input_modalities` / `output_modalities` | Supported normalized text/image/PDF/audio/video input and output |
| `streaming` | Adapter provides normalized stream events and a terminal result |
| `cancellation` | Adapter supports its declared cancellation control surface |
| `structured_output` | Adapter accepts a response schema |
| `timeout` | Adapter enforces a bounded execution timeout |
| `resume` | Adapter can resume from a durable checkpoint |
| `tool_gateway` | Adapter can expose tools only through the platform Tool Gateway |

`CapabilityAwareHarnessAdapter` rejects requests that require undeclared
capabilities before dispatch. A stream must end with exactly one `completed`
event carrying a normalized `HarnessResult`; an incomplete stream is a
failure, not a successful empty response.

## Request and result envelope

`HarnessRequest` carries the trusted `RunContext`, instructions, input,
authorized tool names, optional output schema, and verified media references.
Optional timeout and checkpoint-resume intent are explicit. A per-request timeout
overrides the model-template timeout when the adapter declares timeout support.
A checkpoint reference is invalid unless resume is requested.

`HarnessResult` normalizes output text, tool calls, token usage, finish reason,
trace identity, metadata and verified media artifact references. Provider SDK
objects must not escape this boundary.

## Execution lifecycle

`LifecycleManagedHarnessAdapter` composes compatibility checks, capability
enforcement and a required lifecycle observer. The observer receives ordered
`started` followed by exactly one terminal callback:

- `succeeded` with a normalized result;
- `failed` with the original exception;
- `timed_out` with the timeout error; or
- `cancelled` when the execution task is cancelled.

Streaming uses the same lifecycle callbacks and is successful only after a
normalized completion event. Observer startup failure prevents adapter dispatch.
Terminal observer failures propagate rather than silently reporting a completed
execution.

## Current adapter declarations

- `DeepSeekHarnessAdapter`: text-only, non-streaming, no structured output,
  no Tool Gateway tools, no checkpoint resume. Cancellation is not advertised
  because its SDK call runs in a worker thread and cancelling the awaiting
  coroutine cannot reliably stop that work.
- `StrandsAgentsAdapter`: declares its configured modality and streaming
  support; Tool Gateway capability is true only when a run-scoped Gateway
  provider is configured. It does not advertise an independent
  `cancel(run_id)` control surface or checkpoint resume.

A new adapter must publish a descriptor, pass compatibility validation, and
be wrapped by `LifecycleManagedHarnessAdapter` at its composition boundary.
Do not claim support based only on a provider's feature list; verify that the
platform adapter enforces the capability end-to-end.
