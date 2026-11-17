# Structured output schema contract

Schemas are registered by stable ID and positive integer version. A registered version is immutable; incompatible revisions require a new version. Omitting a requested version selects the latest registered version. Registration rejects unsupported JSON Schema keywords rather than silently ignoring them.

The built-in validator supports primitive types, object properties/required/additionalProperties, array items, enum/const, numeric bounds, string length/pattern, and object/array size bounds. Deployments needing the full JSON Schema specification should inject a conforming validator in a later extension rather than assume unsupported keywords work.

Repair is opt-in and bounded to 0–5 retries. A repair callback receives the raw output, schema version, validation issues and attempt number; synchronous and asynchronous callbacks are accepted. Every repaired response is parsed and validated again. Unvalidated output is never returned.

Stable failure codes: schema_registration_error, schema_not_found, schema_version_conflict, output_parse_error, output_validation_error, output_repair_exhausted. Errors expose attempt count and issue details. Exhaustion fails explicitly; callers choose whether to fail the Run, request human review or use an explicitly configured fallback.

The included registry is in-memory. Production should persist schemas with tenant/project authorization and immutable version constraints. Repair input/output is untrusted model content.
