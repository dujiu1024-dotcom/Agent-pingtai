"""Explicit model pricing cards; never infer or hard-code provider prices."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class ModelPricing:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def __post_init__(self) -> None:
        if self.input_usd_per_million < 0 or self.output_usd_per_million < 0:
            raise ValueError("model token prices must be non-negative")

    def estimate(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * self.input_usd_per_million / million
            + Decimal(output_tokens) * self.output_usd_per_million / million
        )


class ModelCostEstimator:
    def __init__(self, pricing: Mapping[str, ModelPricing]) -> None:
        self._pricing = dict(pricing)

    def estimate(self, template_key: str, *, input_tokens: int, output_tokens: int) -> Decimal | None:
        card = self._pricing.get(template_key)
        if card is None:
            return None
        return card.estimate(input_tokens=input_tokens, output_tokens=output_tokens)
