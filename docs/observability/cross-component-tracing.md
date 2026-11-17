# Cross-component Trace / Span correlation

The platform uses the existing W3C `traceparent` context as the trace identity and a ContextVar-backed span scope for in-process parent/child relationships. The ASGI middleware creates a request root span and stores both the validated trace context and active span in request state. `x-request-id` is accepted only as a bounded, control-character-free correlation dimension.

## Instrumented boundaries

- **Request**: `TraceContextASGIMiddleware` creates `http.request`.
- **Run**: `LangGraphRunExecutor` creates `run.execute` and `run.resume`.
- **Prompt**: `PromptTemplateHarnessAdapter` creates `prompt.render` with tenant, Run, Agent, prompt ID/version and content hash (never rendered prompt text).
- **Agent**: `ObservedHarnessAdapter` creates `agent.run`.
- **Model**: `OpenAICompatibleModelRouter` creates `model.complete` with provider/model/template dimensions.
- **Tool**: `SupervisedToolGateway` creates `tool.call` without capturing arguments.
- **Knowledge / retrieval**: `RAGService` creates `rag.answer`, `knowledge.retrieve` and `rag.generate`.

The span API also supports `blackboard`, `connector`, and custom boundaries. Use `trace_span(...)` to add a child span; child async tasks inherit the current span. Each span includes trace/span/parent IDs, tenant, request, Run, Agent and optional model, prompt, tool, collection and retrieval identifiers, status, timestamps and duration.

## Privacy and failure isolation

- Use identifiers and bounded counts/hashes, not prompt text, user messages, tool arguments, retrieved chunk text, credentials, or raw provider payloads.
- Every attribute map is recursively redacted using the shared observability redactor before it is exposed or exported. Sensitive keys and bearer/API-key patterns are masked.
- Prompt spans record content hash and variable count only. Tool spans record tool name, call ID and resource identifier but never arguments.
- Exporter/observer failures are isolated from application execution. Span scopes restore ContextVar state on success, exceptions and cancellation.
- W3C inbound context is validated; malformed trace headers are replaced rather than reflected. Tenant IDs remain explicit dimensions and are never inferred from untrusted baggage.
