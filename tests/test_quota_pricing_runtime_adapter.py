from __future__ import annotations

import unittest
from decimal import Decimal

from agent_platform.quotas.pricing import (
    CatalogModelCostEstimator, ModelPricingCatalog, PricingCard,
)


class CatalogModelCostEstimatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = ModelPricingCatalog({
            "model-a": PricingCard(
                "model-a", "2026-11-r2", Decimal("1.25"), Decimal("4.50")
            )
        })
        self.estimator = CatalogModelCostEstimator(self.catalog)

    async def test_adapts_provider_usage_to_micros_and_exposes_card_version(self):
        micros = await self.estimator.estimate_micros(
            model_id="model-a",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
        self.assertEqual(micros, 6)
        self.assertEqual(self.estimator.price_card_version("model-a"), "2026-11-r2")

    async def test_unknown_model_does_not_invent_a_zero_price(self):
        self.assertIsNone(await self.estimator.estimate_micros(
            model_id="unpriced-model",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        ))
        self.assertIsNone(self.estimator.price_card_version("unpriced-model"))

    async def test_rejects_missing_boolean_negative_and_conflicting_usage(self):
        invalid = (
            {},
            {"prompt_tokens": True, "completion_tokens": 1},
            {"prompt_tokens": -1, "completion_tokens": 1},
            {"prompt_tokens": 1, "input_tokens": 2, "completion_tokens": 1},
        )
        for usage in invalid:
            with self.subTest(usage=usage):
                with self.assertRaises(ValueError):
                    await self.estimator.estimate_micros(model_id="model-a", usage=usage)

    async def test_rounds_up_fractional_micro_dollars(self):
        catalog = ModelPricingCatalog({
            "tiny": PricingCard("tiny", "v1", Decimal("0.000001"), Decimal("0"))
        })
        estimator = CatalogModelCostEstimator(catalog)
        self.assertEqual(await estimator.estimate_micros(
            model_id="tiny", usage={"prompt_tokens": 1, "completion_tokens": 0},
        ), 1)


if __name__ == "__main__":
    unittest.main()
