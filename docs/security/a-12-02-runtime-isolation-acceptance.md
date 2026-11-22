# A-12-02 security acceptance evidence

## Existing implementation under test

- Agent graph boundary: `agent_platform/runtime/langgraph_adapter.py` requires an explicit `tool_using_nodes` inventory and requires each declared tool node to be a `GatewayToolNode`; arbitrary callables are rejected.
- Runtime tool path: `agent_platform/runtime/tool_gateway.py` authenticates the principal, binds tenant/agent/project/run, checks the Run allowlist and scopes, applies timeout/cancellation, and audits invocations.
- OCI boundary: `agent_platform/sandbox/oci.py` builds a digest-pinned Podman/Docker invocation with no network, no host mounts, read-only root, non-root UID, private namespaces, dropped capabilities, no-new-privileges, resource limits, and an explicit environment allowlist.

## Automated acceptance

Run the deterministic and OCI contract suite:

```bash
python -m unittest tests.test_issue_12_a_12_02_security_acceptance tests.test_langgraph_gateway_contract tests.test_sandbox_oci -v
```

The Gateway tests attempt the relevant bypasses: omitting the tool-node inventory, registering a plain callable as a tool node, spoofing the tenant identity, and calling a tool outside the node allowlist. Each denied attempt asserts that the Gateway was never called.

The production OCI probe is opt-in and is intentionally not run by ordinary hosted CI. It requires all of the following on the **actual production execution host**:

- `AGENT_PLATFORM_OCI_INTEGRATION_IMAGE` set to the exact immutable production image reference, `registry/repository@sha256:<64-hex>`;
- the referenced image already present locally (the probe uses inspect and the runner uses `--pull=never`; it does not pull or build images);
- Podman available and usable by the production execution identity;
- operator approval to execute the short isolation probe on that host.

Run:

```bash
AGENT_PLATFORM_OCI_INTEGRATION_IMAGE='registry.example/agent-sandbox@sha256:<64-hex>' \
  python -m unittest tests.test_issue_12_a_12_02_security_acceptance.ProductionOciIsolationAcceptanceTests -v
```

The probe creates two temporary host-only files and a host-only environment secret, passes only their paths as stdin, then asserts from inside the container:

1. neither host-only file is visible;
2. the PID namespace differs from the host and the probe is PID 1;
3. outbound network access is blocked;
4. writes to the image root fail and the root mount is read-only;
5. the host-only environment secret is absent;
6. Docker and Podman runtime sockets are not visible;
7. UID is 65532, effective capabilities are zero, and `NoNewPrivs` is enabled.

It also checks Podman's local image metadata against the requested digest before execution and emits one `OCI_ISOLATION_EVIDENCE=` JSON record containing runtime, image digest, host PID namespace, and observed results. Preserve the complete test log with the host identity, UTC timestamp, Podman version, kernel version, and deployment/environment identifier. Redact all credentials and do not attach secret values.

A successful hosted-CI run of deterministic tests is not production-host evidence. A skipped opt-in test is not evidence of isolation.

## Current acceptance status

The repository contains enforceable Gateway and OCI launch configuration plus deterministic negative tests and a production-host probe. This connector session does not expose the production execution host, its Podman daemon, or the production sandbox image digest. Per the execution instruction, no production server or external resource is requested or started, so the real file/network/process/credential isolation probe is **not run here**. Keep A-12-02 open until the probe is executed on the target host and its evidence log is attached to the #267 ledger.
