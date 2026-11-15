"""Bridge OCI sandbox execution into the supervised Tool Gateway lifecycle.

Register the returned handler as a ContextualToolHandler on a tool whose risk
level is chosen by trusted server configuration. Approval and write-ahead audit
remain enforced by SupervisedToolGateway before this handler is invoked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_platform.runtime.lifecycle import ToolCallRegistry
from agent_platform.sandbox.oci import OciSandboxRunner
from agent_platform.tools.gateway import ToolExecutionContext


def make_sandbox_tool_handler(
    runner: OciSandboxRunner,
    calls: ToolCallRegistry,
):
    """Create a Gateway contextual handler with Run-scoped cancellation.

    Expected arguments: command (argv list), optional stdin (text),
    timeout_seconds (number), environment (allowlisted mapping), call_id.
    The runner enforces the actual environment allowlist and hard limits.
    """

    async def execute(
        arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        run_id = context.run_id
        if not run_id:
            raise ValueError("sandbox execution requires a supervised Run context")
        call_id = arguments.get("call_id")
        command = arguments.get("command")
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 128:
            raise ValueError("call_id must be a non-empty string up to 128 characters")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError("command must be a non-empty argv list")
        stdin = arguments.get("stdin", "")
        timeout = arguments.get("timeout_seconds", 30.0)
        environment = arguments.get("environment", {})
        if not isinstance(stdin, str):
            raise ValueError("stdin must be text")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if not isinstance(environment, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise ValueError("environment must be a string mapping")

        result = await calls.run(
            run_id,
            call_id,
            runner.run(
                command,
                stdin=stdin.encode("utf-8"),
                environment=environment,
                timeout_seconds=float(timeout),
            ),
            timeout=float(timeout) + 1.0,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "timed_out": result.timed_out,
            "output_truncated": result.output_truncated,
        }

    return execute
