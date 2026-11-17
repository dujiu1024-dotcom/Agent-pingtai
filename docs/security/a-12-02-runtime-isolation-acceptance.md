# A-12-02 security acceptance evidence

## Existing implementation under test

- Agent graph boundary: `agent_platform/runtime/langgraph_adapter.py` requires an explicit `tool_using_nodes` inventory and requires each declared tool node to be a `GatewayToolNode`; arbitrary callables are rejected.
- Runtime tool path: `agent_platform/runtime/tool_gateway.py` authenticates the principal, binds tenant/agent/project/run, checks the Run allowlist and scopes, applies timeout/cancellation, and audits invocations.
- OCI boundary: `agent_platform/sandbox/oci.py` builds a digest-pinned Podman/Docker invocation with no network, no host mounts, read-only root, non-root UID, private namespaces, dropped capabilities, no-new-privileges, resource limits, and an explicit environment allowlist.

## Automated acceptance tests

Run:

```bash
python -m unittest tests.test_issue_12_a_12_02_security_acceptance tests.test_langgraph_gateway_contract tests.test_sandbox_oci -v
```

The gateway tests attempt the relevant bypasses: omitting the tool-node inventory, registering a plain callable as a tool node, spoofing the tenant identity, and calling a tool outside the node allowlist. Each denied attempt asserts that the Gateway was never called.

The OCI integration probe is deliberately opt-in. It requires `AGENT_PLATFORM_OCI_INTEGRATION_IMAGE` to contain an immutable `image@sha256:digest` for the production sandbox image and must be executed on the actual production OCI runtime host with Podman. It then probes host-file visibility, host PID namespace visibility, outbound network, root-filesystem writes, inherited host credentials, non-root UID, dropped capabilities and no-new-privileges. A skipped test is not evidence of isolation.

## Current acceptance status

The repository contains enforceable Gateway and OCI launch configuration plus deterministic negative tests. This repository/connector session does not expose the production execution host, its Podman daemon, or the production sandbox image digest. Therefore the real file/network/process/credential isolation probe cannot be run here. CI/unit success alone cannot satisfy the production-environment clause of A-12-02; keep the acceptance checkbox open until the opt-in probe is run on the production host and its output/host/image/runtime evidence is attached.
