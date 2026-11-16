from __future__ import annotations

import unittest
from decimal import Decimal

from agent_platform.quotas.pricing import ModelPricingCatalog, PricingCard


class ModelPricingCatalogTests(unittest.TestCase):
    def test_estimates_from_explicit_versioned_card(self):
        catalog = ModelPricingCatalog({
            "model-a": PricingCard(
                "model-a", "2026-11", Decimal("2"), Decimal("8")
            )
        })
        self.assertEqual(
            catalog.estimate("model-a", input_tokens=500_000, output_tokens=250_000),
            Decimal("3"),
        )

    def test_unknown_price_is_not_assumed_zero(self):
        self.assertIsNone(ModelPricingCatalog({}).estimate(
            "unknown", input_tokens=1, output_tokens=1
        ))

    def test_rejects_bool_nonfinite_and_negative_values(self):
        catalog = ModelPricingCatalog({
            "model-a": PricingCard("model-a", "v1", Decimal("1"), Decimal("1"))
        })
        for value in (True, -1, 1.5):
            with self.assertRaises(ValueError):
                catalog.estimate("model-a", input_tokens=value, output_tokens=0)
        with self.assertRaises(ValueError):
            PricingCard("model-b", "v1", Decimal("NaN"), Decimal("1"))
