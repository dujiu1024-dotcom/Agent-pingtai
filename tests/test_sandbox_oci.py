import unittest

from agent_platform.sandbox.oci import OciSandboxConfig, OciSandboxRunner

IMAGE = "registry.example/agent-sandbox@sha256:" + "a" * 64


class OciSandboxConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_requires_digest_pinned_image(self):
        with self.assertRaisesRegex(ValueError, "sha256 digest"):
            OciSandboxConfig(image="registry.example/agent-sandbox:latest")

    def test_rejects_secret_and_host_credential_environment_allowlists(self):
        for key in (
            "MODEL_API_KEY", "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN",
            "AUTHORIZATION", "SSH_AUTH_SOCK", "DATABASE_PASSWORD",
            "TLS_PRIVATE_KEY", "SESSION_COOKIE", "DOCKER_HOST",
            "DATABASE_URL", "PLATFORM_DB_URL", "PGHOST", "PGPASSWORD",
            "REDIS_URL", "MYSQL_DSN", "DB_CONNECTION_STRING",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "credential-bearing"):
                    OciSandboxConfig(
                        image=IMAGE, runtime="podman",
                        allowed_environment_keys=frozenset({key}),
                    )

    def test_command_has_required_isolation_and_no_host_mounts(self):
        runner = OciSandboxRunner(OciSandboxConfig(image=IMAGE, runtime="podman"))
        command = runner.build_command(("python", "-I", "-c", "print('ok')"))
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--userns=private", command)
        self.assertIn("--pid=private", command)
        self.assertIn("--ipc=private", command)
        self.assertIn("--memory-swap", command)
        self.assertIn("512m", command)
        self.assertIn("nofile=1024:1024", command)
        self.assertIn("fsize=16777216:16777216", command)
        self.assertIn("core=0:0", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges:true", command)
        self.assertIn("--pids-limit", command)
        self.assertIn("--memory", command)
        self.assertIn("--cpus", command)
        self.assertIn("65532:65532", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)
        self.assertEqual(command[-5:], (IMAGE, "python", "-I", "-c", "print('ok')"))

    def test_command_cannot_inherit_host_database_or_runtime_access(self):
        runner = OciSandboxRunner(
            OciSandboxConfig(
                image=IMAGE, runtime="podman",
                allowed_environment_keys=frozenset({"LANG"}),
            )
        )
        command = runner.build_command(
            ("python", "-c", "pass"), environment={"LANG": "C.UTF-8"},
        )
        forbidden_options = {
            "--network=host", "--privileged", "--pid=host", "--ipc=host",
            "--uts=host", "--userns=host", "--env-file", "--mount",
            "--volume", "--device", "--add-host",
        }
        self.assertTrue(forbidden_options.isdisjoint(command))
        self.assertNotIn("/var/run/docker.sock", command)
        self.assertNotIn("/run/podman/podman.sock", command)
        env_values = [
            command[i + 1] for i, value in enumerate(command[:-1])
            if value == "--env"
        ]
        self.assertEqual(env_values, ["LANG=C.UTF-8"])
        self.assertIn("--network=none", command)

    def test_rejects_untrusted_or_secret_environment_values(self):
        runner = OciSandboxRunner(OciSandboxConfig(image=IMAGE, runtime="podman"))
        with self.assertRaisesRegex(ValueError, "not explicitly allowlisted"):
            runner.build_command(("python",), environment={"HOME": "/root"})
        with self.assertRaisesRegex(ValueError, "NUL-free"):
            runner.build_command(("python", "bad\x00arg"))



    def test_docker_requires_verified_user_namespace_isolation_and_uses_valid_flags(self):
        with self.assertRaisesRegex(ValueError, "verified rootless mode or daemon userns-remap"):
            OciSandboxConfig(image=IMAGE, runtime="docker")
        config = OciSandboxConfig(
            image=IMAGE, runtime="docker", docker_userns_remap_verified=True,
        )
        command = OciSandboxRunner(config).build_command(("python",))
        self.assertNotIn("--userns=private", command)
        self.assertNotIn("--pid=private", command)
        self.assertIn("--ipc=private", command)

    def test_rejects_boolean_and_unbounded_resource_configuration(self):
        for kwargs in (
            {"pids_limit": True},
            {"max_timeout_seconds": True},
            {"max_output_bytes": True},
            {"max_stdin_bytes": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OciSandboxConfig(image=IMAGE, **kwargs)

    async def test_rejects_oversized_stdin_and_boolean_timeout(self):
        runner = OciSandboxRunner(OciSandboxConfig(
            image=IMAGE, runtime="podman", max_stdin_bytes=4,
        ))
        with self.assertRaisesRegex(ValueError, "input limit"):
            await runner.run(("python", "-I", "-c", "pass"), stdin=b"12345")
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            await runner.run(("python", "-I", "-c", "pass"), timeout_seconds=True)

    def test_rejects_string_instead_of_argv_sequence(self):
        runner = OciSandboxRunner(OciSandboxConfig(image=IMAGE, runtime="podman"))
        with self.assertRaisesRegex(ValueError, "argv sequence"):
            runner.build_command("python")

    def test_only_explicit_non_secret_environment_is_passed(self):
        runner = OciSandboxRunner(
            OciSandboxConfig(
                image=IMAGE, runtime="podman",
                allowed_environment_keys=frozenset({"LANG"}),
            )
        )
        command = runner.build_command(("python",), environment={"LANG": "C.UTF-8"})
        self.assertIn("LANG=C.UTF-8", command)


if __name__ == "__main__":
    unittest.main()
