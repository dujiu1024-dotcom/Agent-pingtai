"""Versioned, explicit model pricing cards for auditable cost estimation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class PricingCard:
    model_key: str
    version: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    currency: str = "USD"
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_key, str) or not self.model_key.strip():
            raise ValueError("model_key is required")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("pricing version is required")
        if self.currency != "USD":
            raise ValueError("only USD pricing is supported by this catalog")
        for value in (self.input_usd_per_million, self.output_usd_per_million):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("prices must be finite non-negative Decimal values")
        if self.effective_at is not None and (
            self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None
        ):
            raise ValueError("effective_at must be timezone-aware")


class ModelPricingCatalog:
    """Immutable snapshot: unknown models never receive an invented zero price."""

    def __init__(self, cards: Mapping[str, PricingCard]) -> None:
        copied = dict(cards)
        for key, card in copied.items():
            if key != card.model_key:
                raise ValueError("catalog key must match PricingCard.model_key")
        self._cards = MappingProxyType(copied)

    def get(self, model_key: str) -> PricingCard | None:
        return self._cards.get(model_key)

    def estimate(
        self, model_key: str, *, input_tokens: int, output_tokens: int
    ) -> Decimal | None:
        for name, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        card = self.get(model_key)
        if card is None:
            return None
        million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * card.input_usd_per_million / million
            + Decimal(output_tokens) * card.output_usd_per_million / million
        )
