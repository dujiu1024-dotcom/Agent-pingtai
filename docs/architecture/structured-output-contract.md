# Structured output schema contract (C-12-04)

The runtime's `SchemaValidatedHarnessAdapter` is the enforcement boundary: it validates model output before returning a successful Harness result and performs only a bounded number of repair attempts through the same adapter.

## Schema registration and versions
- `StructuredOutputSchemaRegistry` registers schemas by stable ID and positive integer version. A version is immutable and resolution is explicitly pinned to the requested version.
- `PostgresStructuredOutputSchemaRegistry` provides tenant-scoped, append-only persistence. Registration stores a canonical JSON representation and SHA-256 digest; conflicting re-registration fails. Resolution verifies the stored digest.
- Schema validation rejects unsupported keywords and malformed constraints rather than silently ignoring them.

## Validation and repair
- JSON parsing rejects non-standard constants and non-finite numeric values.
- Validation covers primitive types, enums, object required/properties/additionalProperties, arrays/items and bounds for strings, arrays and numbers.
- Repair retries are bounded (0–3) and re-enter the Harness adapter with validation errors in the instructions. The result is validated again; invalid output never becomes a successful result.
- A terminal `StructuredOutputValidationError` carries diagnostics and attempt count. Schema lookup/registration failures are fail-closed.

## Failure semantics
A schema lookup failure, malformed schema, invalid JSON, schema mismatch or exhausted retry budget fails the Harness call. The adapter does not silently return raw text as a valid structured result. The caller decides whether the Run should fail or be routed for human review.

The in-memory registry is suitable for tests and single-process use. Multi-worker production deployments should use the PostgreSQL registry and tenant-scoped authorization.
