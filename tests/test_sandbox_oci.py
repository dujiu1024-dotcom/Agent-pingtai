import unittest

from agent_platform.sandbox.oci import OciSandboxConfig, OciSandboxRunner

IMAGE = "registry.example/agent-sandbox@sha256:" + "a" * 64


class OciSandboxConfigTests(unittest.TestCase):
    def test_requires_digest_pinned_image(self):
        with self.assertRaisesRegex(ValueError, "sha256 digest"):
            OciSandboxConfig(image="registry.example/agent-sandbox:latest")

    def test_rejects_secret_environment_allowlist(self):
        with self.assertRaisesRegex(ValueError, "secret-bearing"):
            OciSandboxConfig(
                image=IMAGE,
                allowed_environment_keys=frozenset({"MODEL_API_KEY"}),
            )

    def test_command_has_required_isolation_and_no_host_mounts(self):
        runner = OciSandboxRunner(OciSandboxConfig(image=IMAGE))
        command = runner.build_command(("python", "-I", "-c", "print('ok')"))
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
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

    def test_rejects_untrusted_or_secret_environment_values(self):
        runner = OciSandboxRunner(OciSandboxConfig(image=IMAGE))
        with self.assertRaisesRegex(ValueError, "not explicitly allowlisted"):
            runner.build_command(("python",), environment={"HOME": "/root"})
        with self.assertRaisesRegex(ValueError, "NUL-free"):
            runner.build_command(("python", "bad\x00arg"))

    def test_only_explicit_non_secret_environment_is_passed(self):
        runner = OciSandboxRunner(
            OciSandboxConfig(
                image=IMAGE,
                allowed_environment_keys=frozenset({"LANG"}),
            )
        )
        command = runner.build_command(("python",), environment={"LANG": "C.UTF-8"})
        self.assertIn("LANG=C.UTF-8", command)


if __name__ == "__main__":
    unittest.main()
