from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_platform.runtime.langgraph_adapter import GatewayToolNode, build_state_graph
from agent_platform.sandbox.oci import OciSandboxConfig, OciSandboxRunner


class _RecordingGateway:
    def __init__(self):
        self.calls = []

    async def call_resolved(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


class GatewayBypassAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_graph_requires_explicit_tool_node_inventory(self):
        with self.assertRaises(TypeError):
            build_state_graph(state_schema=dict, nodes={})

    def test_declared_tool_node_cannot_be_an_arbitrary_callable(self):
        async def direct_network_or_file_access(state, _config):
            return state

        with self.assertRaisesRegex(ValueError, "must use GatewayToolNode"):
            build_state_graph(
                state_schema=dict,
                nodes={"external_tool": direct_network_or_file_access},
                tool_using_nodes=("external_tool",),
            )

    async def test_gateway_node_rejects_spoofed_tenant_before_gateway_call(self):
        gateway = _RecordingGateway()
        node = GatewayToolNode(
            handler=lambda state, tools: tools.call("search", {"q": state["q"]}),
            gateway=gateway,
            allowed_tools=("search",),
            identity_resolver=lambda _config: {
                "tenant_id": "tenant-b",
                "actor_id": "agent-a",
                "actor_roles": ("agent",),
                "agent_id": "agent-a",
            },
        )
        with self.assertRaisesRegex(PermissionError, "tenant"):
            await node(
                {"q": "x"},
                {"configurable": {
                    "platform_tenant_id": "tenant-a",
                    "platform_run_id": "run-a",
                }},
            )
        self.assertEqual(gateway.calls, [])

    async def test_gateway_node_rejects_unlisted_tool_before_gateway_call(self):
        gateway = _RecordingGateway()
        node = GatewayToolNode(
            handler=lambda state, tools: tools.call("shell", {"cmd": "id"}),
            gateway=gateway,
            allowed_tools=("search",),
            identity_resolver=lambda _config: {
                "tenant_id": "tenant-a",
                "actor_id": "agent-a",
                "actor_roles": ("agent",),
                "agent_id": "agent-a",
            },
        )
        with self.assertRaisesRegex(PermissionError, "not allowed"):
            await node(
                {},
                {"configurable": {
                    "platform_tenant_id": "tenant-a",
                    "platform_run_id": "run-a",
                }},
            )
        self.assertEqual(gateway.calls, [])


class ProductionOciIsolationAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """Opt-in production-host probe; a skipped run is never acceptance evidence.

    Required: immutable image@sha256 digest, Podman daemon on the target
    production execution host, and an operator-approved maintenance window.
    This test intentionally does not provision infrastructure or pull images.
    """

    @unittest.skipUnless(
        os.environ.get("AGENT_PLATFORM_OCI_INTEGRATION_IMAGE"),
        "requires production OCI image digest and runtime host",
    )
    async def test_real_container_file_network_process_and_credential_isolation(self):
        image = os.environ["AGENT_PLATFORM_OCI_INTEGRATION_IMAGE"]
        match = re.fullmatch(r"[^\s@]+@sha256:([a-fA-F0-9]{64})", image)
        self.assertIsNotNone(match, "production image must be pinned by sha256 digest")
        if shutil.which("podman") is None:
            self.fail("Podman is required for the real OCI isolation acceptance probe")

        # Refuse to silently fetch a different image or accept a tag-only local image.
        inspect = subprocess.run(
            ["podman", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        image_metadata = json.loads(inspect.stdout)
        self.assertTrue(image_metadata, "Podman returned no image metadata")
        repo_digests = image_metadata[0].get("RepoDigests") or []
        self.assertTrue(
            any(value.endswith("@sha256:" + match.group(1)) for value in repo_digests),
            f"local image RepoDigests do not prove requested digest: {repo_digests!r}",
        )

        runner = OciSandboxRunner(OciSandboxConfig(image=image, runtime="podman"))
        secret_name = "AGENT_PLATFORM_HOST_SECRET_PROBE"
        previous_secret = os.environ.get(secret_name)
        os.environ[secret_name] = "must-never-enter-container"
        host_file = None
        host_credential_file = None
        try:
            with tempfile.NamedTemporaryFile(prefix="host-only-data-", delete=False) as stream:
                stream.write(b"host-private-data")
                host_file = stream.name
            with tempfile.NamedTemporaryFile(prefix="host-only-credential-", delete=False) as stream:
                stream.write(b"host-private-credential")
                host_credential_file = stream.name

            probe = r"""
import json
import os
import socket

request = json.load(__import__("sys").stdin)
network_blocked = False
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1.0).close()
except OSError:
    network_blocked = True

root_write_blocked = False
try:
    with open("/etc/agent-platform-host-write-probe", "w", encoding="utf-8") as stream:
        stream.write("unexpected")
except OSError:
    root_write_blocked = True

status = {}
with open("/proc/self/status", encoding="ascii") as stream:
    for line in stream:
        if line.startswith(("CapEff:", "NoNewPrivs:")):
            key, value = line.split(":", 1)
            status[key] = value.strip()

root_mount_options = []
with open("/proc/self/mountinfo", encoding="ascii") as stream:
    for line in stream:
        left, _right = line.split(" - ", 1)
        fields = left.split()
        if len(fields) > 5 and fields[4] == "/":
            root_mount_options = fields[5].split(",")
            break

print(json.dumps({
    "host_data_file_visible": os.path.exists(request["host_file"]),
    "host_credential_file_visible": os.path.exists(request["host_credential_file"]),
    "pid_namespace": os.readlink("/proc/self/ns/pid"),
    "network_blocked": network_blocked,
    "root_write_blocked": root_write_blocked,
    "root_mount_read_only": "ro" in root_mount_options,
    "host_secret_present": "AGENT_PLATFORM_HOST_SECRET_PROBE" in os.environ,
    "docker_socket_visible": os.path.exists("/var/run/docker.sock"),
    "podman_socket_visible": os.path.exists("/run/podman/podman.sock"),
    "uid": os.getuid(),
    "pid": os.getpid(),
    "cap_eff": status.get("CapEff"),
    "no_new_privs": status.get("NoNewPrivs"),
}))
"""
            host_pid_namespace = os.readlink("/proc/self/ns/pid")
            result = await runner.run(
                ("python", "-I", "-c", probe),
                stdin=json.dumps({
                    "host_file": host_file,
                    "host_credential_file": host_credential_file,
                }).encode(),
                timeout_seconds=15,
            )
            self.assertFalse(result.timed_out, result.stderr.decode(errors="replace"))
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            observed = json.loads(result.stdout)
            evidence = {
                "runtime": "podman",
                "image": image,
                "image_digest": match.group(1),
                "host_pid_namespace": host_pid_namespace,
                "observed": observed,
            }
            print("OCI_ISOLATION_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
            self.assertFalse(observed["host_data_file_visible"], observed)
            self.assertFalse(observed["host_credential_file_visible"], observed)
            self.assertNotEqual(observed["pid_namespace"], host_pid_namespace, observed)
            self.assertTrue(observed["network_blocked"], observed)
            self.assertTrue(observed["root_write_blocked"], observed)
            self.assertTrue(observed["root_mount_read_only"], observed)
            self.assertFalse(observed["host_secret_present"], observed)
            self.assertFalse(observed["docker_socket_visible"], observed)
            self.assertFalse(observed["podman_socket_visible"], observed)
            self.assertEqual(observed["uid"], 65532, observed)
            self.assertEqual(observed["pid"], 1, observed)
            self.assertEqual(observed["cap_eff"], "0000000000000000", observed)
            self.assertEqual(observed["no_new_privs"], "1", observed)
        finally:
            if previous_secret is None:
                os.environ.pop(secret_name, None)
            else:
                os.environ[secret_name] = previous_secret
            if host_file is not None:
                Path(host_file).unlink(missing_ok=True)
            if host_credential_file is not None:
                Path(host_credential_file).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
