"""Versioned, explicit model pricing cards for auditable cost estimation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
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


@dataclass(frozen=True)
class PricingFeed:
    """One immutable, revisioned response from a configured trusted pricing source."""

    source: str
    revision: str
    fetched_at: datetime
    cards: tuple[PricingCard, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("pricing source is required")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("pricing source revision is required")
        if (
            not isinstance(self.fetched_at, datetime)
            or self.fetched_at.tzinfo is None
            or self.fetched_at.utcoffset() is None
        ):
            raise ValueError("pricing feed fetched_at must be timezone-aware")
        if not self.cards:
            raise ValueError("pricing feed must contain at least one card")
        if any(not isinstance(card, PricingCard) for card in self.cards):
            raise TypeError("pricing feed cards must be PricingCard instances")
        keys = [(card.model_key, card.version) for card in self.cards]
        if len(keys) != len(set(keys)) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("pricing feed must contain one unique version per model")


class ModelPricingCatalog:
    """Immutable snapshot: unknown models never receive an invented zero price.

    ``get`` first does an exact lookup; if that misses, it strips trailing
    numeric version suffixes (``@10``, ``@1``, ``-template@1``, ...) until an
    entry matches, so versioned local/model-router identifiers still resolve.
    """

    def __init__(self, cards: Mapping[str, PricingCard]) -> None:
        copied = dict(cards)
        for key, card in copied.items():
            if key != card.model_key:
                raise ValueError("catalog key must match PricingCard.model_key")
        self._cards = MappingProxyType(copied)

    def get(self, model_key: str) -> PricingCard | None:
        if model_key in self._cards:
            return self._cards[model_key]
        # Fallback: strip trailing numeric version suffixes like @10, @1, ...
        import re as _re
        stripped = model_key
        while True:
            next_stripped, n = _re.subn(r"[@._-]\d+$", "", stripped)
            if n == 0 or next_stripped == stripped or not next_stripped:
                break
            if next_stripped in self._cards:
                return self._cards[next_stripped]
            stripped = next_stripped
        return None

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


class CatalogModelCostEstimator:
    """Adapt the trusted pricing catalog to model-router/usage-recorder micros.

    Unknown model prices return None so callers can fail closed for quota
    reservations while still recording provider usage without invented cost.
    """

    def __init__(self, catalog: ModelPricingCatalog) -> None:
        if not isinstance(catalog, ModelPricingCatalog):
            raise TypeError("catalog must be a ModelPricingCatalog")
        self.catalog = catalog

    @staticmethod
    def _usage_counter(usage: Mapping[str, object], primary: str, alternate: str) -> int:
        value = usage.get(primary, usage.get(alternate))
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"provider usage requires a non-negative integer {primary}")
        other = usage.get(alternate)
        if other is not None and other != value:
            raise ValueError(f"provider usage counters {primary} and {alternate} conflict")
        return value

    async def estimate_micros(self, *, model_id: str, usage: Mapping[str, object]) -> int | None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        if not isinstance(usage, Mapping):
            raise ValueError("provider usage must be a mapping")
        input_tokens = self._usage_counter(usage, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_counter(usage, "completion_tokens", "output_tokens")
        estimate = self.catalog.estimate(
            model_id, input_tokens=input_tokens, output_tokens=output_tokens,
        )
        if estimate is None:
            return None
        # Round upward to avoid under-reserving due to sub-micro-dollar fractions.
        return int((estimate * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))

    def price_card_version(self, model_id: str) -> str | None:
        card = self.catalog.get(model_id)
        return card.version if card is not None else None
