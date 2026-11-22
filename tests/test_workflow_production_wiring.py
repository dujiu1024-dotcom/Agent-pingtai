"""Production Workflow compiler resolver loading and fail-closed behavior tests."""
import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from agent_platform.production import (
    _load_trusted_workflow_compiler_resolver,
    _workflow_compiler_unavailable,
)


class WorkflowProductionResolverTests(unittest.TestCase):
    def test_missing_trusted_entry_point_is_not_replaced_with_a_fake_compiler(self):
        with patch("importlib.metadata.entry_points", return_value=()):
            self.assertIsNone(_load_trusted_workflow_compiler_resolver())

    def test_exactly_one_trusted_resolver_entry_point_is_loaded(self):
        resolver = object()
        entry = Mock(name="workflow-compiler-entry")
        entry.name = "resolver"
        entry.load.return_value = resolver
        with patch("importlib.metadata.entry_points", return_value=(entry,)):
            self.assertIs(_load_trusted_workflow_compiler_resolver(), resolver)

    def test_multiple_resolvers_fail_closed_instead_of_choosing_implicitly(self):
        first = Mock(name="first")
        second = unittest.mock.Mock(name="second")
        first.name = second.name = "resolver"
        with patch("importlib.metadata.entry_points", return_value=(first, second)):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                _load_trusted_workflow_compiler_resolver()

    def test_non_callable_entry_point_is_rejected(self):
        entry = unittest.mock.Mock(name="invalid")
        entry.name = "resolver"
        entry.load.return_value = object()
        with patch("importlib.metadata.entry_points", return_value=(entry,)):
            with self.assertRaisesRegex(TypeError, "callable resolver"):
                _load_trusted_workflow_compiler_resolver()

    def test_unconfigured_compiler_routes_return_503(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(_workflow_compiler_unavailable(object()))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("compile and publish are disabled", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
