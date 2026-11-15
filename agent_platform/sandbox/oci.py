"""Fail-closed OCI sandbox for untrusted Agent workloads.

The platform process talks to Docker/Podman; untrusted code runs in a pinned
image with no network, no host mounts, a read-only root filesystem, dropped
capabilities and bounded resources. The runtime daemon/socket must never be
exposed to the Agent itself.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence


class SandboxExecutionError(RuntimeError):
    """Raised when the sandbox cannot be started or safely configured."""


@dataclass(frozen=True)
class OciSandboxConfig:
    image: str
    runtime: str = "docker"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = 64
    tmpfs_size: str = "64m"
    max_timeout_seconds: float = 120.0
    max_output_bytes: int = 256_000
    allowed_environment_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[a-fA-F0-9]{64}", self.image):
            raise ValueError("sandbox image must be pinned by sha256 digest")
        if self.runtime not in {"docker", "podman"}:
            raise ValueError("runtime must be docker or podman")
        if not re.fullmatch(r"[1-9][0-9]*(?:m|g)", self.memory_limit.lower()):
            raise ValueError("memory_limit must be a positive value such as 512m or 1g")
        if not re.fullmatch(r"(?:[0-9]+)(?:\\.[0-9]+)?", self.cpu_limit):
            raise ValueError("cpu_limit must be a positive CPU quantity")
        if float(self.cpu_limit) <= 0:
            raise ValueError("cpu_limit must be positive")
        if self.pids_limit < 1 or self.pids_limit > 4096:
            raise ValueError("pids_limit must be between 1 and 4096")
        if not re.fullmatch(r"[1-9][0-9]*(?:m|g)", self.tmpfs_size.lower()):
            raise ValueError("tmpfs_size must be a positive value such as 64m")
        if not 0 < self.max_timeout_seconds <= 3600:
            raise ValueError("max_timeout_seconds must be in (0, 3600]")
        if not 1024 <= self.max_output_bytes <= 10_000_000:
            raise ValueError("max_output_bytes must be between 1 KiB and 10 MB")
        for key in self.allowed_environment_keys:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key):
                raise ValueError("invalid environment key in allowlist")
            if any(part in key for part in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "CREDENTIAL")):
                raise ValueError("secret-bearing environment keys are forbidden in the sandbox")


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_truncated: bool = False


class OciSandboxRunner:
    """Run a command in a fresh, network-isolated OCI container.

    No bind mounts, devices, host namespaces, host environment, or Docker
    socket are passed. Inputs are delivered only through stdin. The image must
    contain the required executable and is pinned by immutable digest.
    """

    def __init__(self, config: OciSandboxConfig) -> None:
        self.config = config

    def build_command(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        if not command or any(not isinstance(arg, str) or not arg or "\\x00" in arg for arg in command):
            raise ValueError("command must contain non-empty NUL-free arguments")
        env = dict(environment or {})
        unknown = set(env) - self.config.allowed_environment_keys
        if unknown:
            raise ValueError("environment contains keys not explicitly allowlisted")
        args: list[str] = [
            self.config.runtime, "run", "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--pids-limit", str(self.config.pids_limit), "--memory", self.config.memory_limit,
            "--cpus", self.config.cpu_limit, "--user", "65532:65532",
            "--workdir", "/workspace", "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self.config.tmpfs_size}",
            "--tmpfs", f"/workspace:rw,noexec,nosuid,nodev,size={self.config.tmpfs_size}",
        ]
        for key, value in sorted(env.items()):
            if not isinstance(value, str) or "\\x00" in value or len(value) > 4096:
                raise ValueError("environment values must be short NUL-free strings")
            args.extend(("--env", f"{key}={value}"))
        args.append(self.config.image)
        args.extend(command)
        return tuple(args)

    async def run(
        self,
        command: Sequence[str],
        *,
        stdin: bytes = b"",
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> SandboxResult:
        if timeout_seconds <= 0 or timeout_seconds > self.config.max_timeout_seconds:
            raise ValueError("timeout_seconds exceeds the configured sandbox limit")
        args = self.build_command(command, environment=environment)
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=64 * 1024,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise SandboxExecutionError("OCI runtime is unavailable or inaccessible") from exc

        async def collect(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
            captured = bytearray()
            truncated = False
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                remaining = self.config.max_output_bytes - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    truncated = True
            return bytes(captured), truncated

        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise SandboxExecutionError("OCI runtime did not provide output pipes")
        stdout_task = asyncio.create_task(collect(process.stdout))
        stderr_task = asyncio.create_task(collect(process.stderr))

        async def feed_stdin() -> None:
            if not stdin or process.stdin is None:
                return
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        feeder = asyncio.create_task(feed_stdin())
        async def stop_process() -> None:
            if process.returncode is not None:
                return
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(process.wait(), feeder),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            await stop_process()
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
        except asyncio.CancelledError:
            await stop_process()
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout, out_truncated = await stdout_task
        stderr, err_truncated = await stderr_task
        return SandboxResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            output_truncated=out_truncated or err_truncated,
        )
