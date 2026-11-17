import unittest

from agent_platform.memory.boundaries import (
    MEMORY_BOUNDARIES,
    MemoryLayer,
    memory_boundary,
)
from agent_platform.memory.service import MemoryScope


class MemoryBoundaryTests(unittest.TestCase):
    def test_all_six_memory_layers_have_one_canonical_boundary(self):
        self.assertEqual(
            {boundary.layer for boundary in MEMORY_BOUNDARIES},
            set(MemoryLayer),
        )
        self.assertEqual(len(MEMORY_BOUNDARIES), len(MemoryLayer))

    def test_run_and_session_are_not_long_term_memory_scopes(self):
        for layer in (MemoryLayer.RUN, MemoryLayer.SESSION):
            boundary = memory_boundary(layer)
            self.assertIn("not durable user memory" if layer is MemoryLayer.RUN else "not the long-term MemoryService", boundary.persistence)
            self.assertNotIn("MemoryScope.", boundary.persistence)

    def test_durable_layers_map_to_existing_scopes(self):
        expected = {
            MemoryLayer.USER: MemoryScope.USER,
            MemoryLayer.AGENT: MemoryScope.AGENT,
            MemoryLayer.PROJECT: MemoryScope.PROJECT,
            MemoryLayer.ORGANIZATION: MemoryScope.TENANT,
        }
        for layer, scope in expected.items():
            self.assertIn(scope.value, memory_boundary(layer).persistence)

    def test_organization_scope_is_tenant_bounded_not_cross_tenant(self):
        boundary = memory_boundary(MemoryLayer.ORGANIZATION)
        self.assertIn("represented by a tenant", boundary.owner)
        self.assertTrue(any("cross-tenant" in item for item in boundary.exclusions))

    def test_layer_lookup_rejects_untyped_values(self):
        with self.assertRaises(TypeError):
            memory_boundary("user")


if __name__ == "__main__":
    unittest.main()
