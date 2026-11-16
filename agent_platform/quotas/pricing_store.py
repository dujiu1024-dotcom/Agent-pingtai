"""Immutable PostgreSQL persistence and synchronization for trusted model price feeds."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Protocol

from agent_platform.quotas.pricing import (
    ModelPricingCatalog,
    PricingCard,
    PricingFeed,
)


class PricingCardConflict(RuntimeError):
    """A source attempted to mutate an already-published model/version price card."""


class PricingFeedSource(Protocol):
    async def fetch(self) -> PricingFeed: ...


class PricingCardRepository(Protocol):
    async def save_feed(self, feed: PricingFeed, *, created_by: str) -> int: ...
    async def list_current(
        self, *, as_of: datetime, source: str | None = None,
    ) -> list[PricingCard]: ...


@dataclass(frozen=True)
class PricingCatalogSyncResult:
    source: str
    revision: str
    inserted_or_confirmed: int
    catalog: ModelPricingCatalog


class PricingCatalogSyncService:
    """Persist a revisioned source feed and build an immutable effective catalog."""

    def __init__(
        self, *, source: PricingFeedSource, repository: PricingCardRepository,
        created_by: str,
    ) -> None:
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        self.source = source
        self.repository = repository
        self.created_by = created_by

    async def sync(self) -> PricingCatalogSyncResult:
        feed = await self.source.fetch()
        if not isinstance(feed, PricingFeed):
            raise TypeError("pricing source must return a PricingFeed")
        count = await self.repository.save_feed(feed, created_by=self.created_by)
        cards = await self.repository.list_current(
            as_of=feed.fetched_at, source=feed.source,
        )
        catalog = ModelPricingCatalog({card.model_key: card for card in cards})
        return PricingCatalogSyncResult(
            source=feed.source, revision=feed.revision,
            inserted_or_confirmed=count, catalog=catalog,
        )


class PostgresPricingCardRepository:
    """Store immutable pricing versions and resolve effective cards by source/time."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    @staticmethod
    def _content_hash(
        card: PricingCard, *, source: str, revision: str, effective_at: datetime,
    ) -> str:
        payload = {
            "model_key": card.model_key,
            "version": card.version,
            "input_usd_per_million": str(card.input_usd_per_million),
            "output_usd_per_million": str(card.output_usd_per_million),
            "currency": card.currency,
            "effective_at": effective_at.isoformat(),
            "source": source,
            "source_revision": revision,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def save_feed(self, feed: PricingFeed, *, created_by: str) -> int:
        if not isinstance(feed, PricingFeed):
            raise TypeError("feed must be a PricingFeed")
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        connection = await self._connect()
        confirmed = 0
        async with connection:
            async with connection.transaction():
                for card in sorted(feed.cards, key=lambda item: item.model_key):
                    effective_at = card.effective_at or feed.fetched_at
                    digest = self._content_hash(
                        card, source=feed.source, revision=feed.revision,
                        effective_at=effective_at,
                    )
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (card.model_key,),
                    )
                    await connection.execute(
                        """INSERT INTO platform_model_pricing_cards
                           (model_key,version,input_usd_per_million,output_usd_per_million,
                            currency,effective_at,source,source_revision,content_hash,created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (
                            card.model_key, card.version,
                            card.input_usd_per_million, card.output_usd_per_million,
                            card.currency, effective_at, feed.source, feed.revision,
                            digest, created_by,
                        ),
                    )
                    cursor = await connection.execute(
                        """SELECT content_hash,source,source_revision
                           FROM platform_model_pricing_cards
                           WHERE model_key=%s AND version=%s""",
                        (card.model_key, card.version),
                    )
                    row = await cursor.fetchone()
                    if row is None or (
                        row["content_hash"] != digest
                        or row["source"] != feed.source
                        or row["source_revision"] != feed.revision
                    ):
                        raise PricingCardConflict(
                            f"pricing card {card.model_key}@{card.version} is immutable"
                        )
                    confirmed += 1
        return confirmed

    async def list_current(
        self, *, as_of: datetime, source: str | None = None,
    ) -> list[PricingCard]:
        if (
            not isinstance(as_of, datetime)
            or as_of.tzinfo is None
            or as_of.utcoffset() is None
        ):
            raise ValueError("as_of must be timezone-aware")
        if source is not None and (not isinstance(source, str) or not source.strip()):
            raise ValueError("source must be a non-empty string")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT DISTINCT ON (model_key)
                          model_key,version,input_usd_per_million,output_usd_per_million,
                          currency,effective_at
                   FROM platform_model_pricing_cards
                   WHERE effective_at <= %s AND (%s IS NULL OR source=%s)
                   ORDER BY model_key,effective_at DESC,created_at DESC,version DESC""",
                (as_of, source, source),
            )
            rows = await cursor.fetchall()
        return [
            PricingCard(
                model_key=row["model_key"], version=row["version"],
                input_usd_per_million=Decimal(row["input_usd_per_million"]),
                output_usd_per_million=Decimal(row["output_usd_per_million"]),
                currency=row["currency"], effective_at=row["effective_at"],
            )
            for row in rows
        ]
