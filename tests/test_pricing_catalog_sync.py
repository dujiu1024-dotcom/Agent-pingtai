from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.quotas.pricing import PricingCard, PricingFeed
from agent_platform.quotas.pricing_store import (
    PostgresPricingCardRepository,
    PricingCardConflict,
    PricingCatalogSyncService,
)


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class FakePricingConnection:
    def __init__(self, database):
        self.database = database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return self

    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if "pg_advisory_xact_lock" in sql:
            return Cursor()
        if sql.startswith("INSERT INTO platform_model_pricing_cards"):
            (
                model_key, version, input_price, output_price, currency,
                effective_at, source, revision, digest, created_by,
            ) = params
            key = (model_key, version)
            if key not in self.database.cards and not any(
                row["source"] == source and row["source_revision"] == revision
                and row["model_key"] == model_key
                for row in self.database.cards.values()
            ):
                self.database.cards[key] = {
                    "model_key": model_key, "version": version,
                    "input_usd_per_million": input_price,
                    "output_usd_per_million": output_price,
                    "currency": currency, "effective_at": effective_at,
                    "source": source, "source_revision": revision,
                    "content_hash": digest, "created_by": created_by,
                    "created_at": effective_at,
                }
            return Cursor()
        if sql.startswith("SELECT content_hash,source,source_revision"):
            return Cursor(self.database.cards.get((params[0], params[1])))
        if sql.startswith("SELECT DISTINCT ON (model_key)"):
            as_of, source, _ = params
            candidates = [
                row for row in self.database.cards.values()
                if row["effective_at"] <= as_of
                and (source is None or row["source"] == source)
            ]
            latest = {}
            for row in candidates:
                prior = latest.get(row["model_key"])
                if prior is None or (
                    row["effective_at"], row["created_at"], row["version"]
                ) > (
                    prior["effective_at"], prior["created_at"], prior["version"]
                ):
                    latest[row["model_key"]] = row
            return Cursor(rows=list(latest.values()))
        raise AssertionError(f"unhandled SQL: {sql}")


class FakePricingDatabase:
    def __init__(self):
        self.cards = {}

    async def connect(self):
        return FakePricingConnection(self)


class FeedSource:
    def __init__(self, feed):
        self.feed = feed

    async def fetch(self):
        return self.feed


class PricingCatalogSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.feed = PricingFeed(
            source="trusted-provider-feed",
            revision="2026-11-16-r1",
            fetched_at=self.now,
            cards=(
                PricingCard(
                    "model-a", "v1", Decimal("2.00"), Decimal("8.00"),
                    effective_at=self.now,
                ),
                PricingCard(
                    "model-b", "v3", Decimal("1.00"), Decimal("4.00"),
                    effective_at=self.now,
                ),
            ),
        )

    async def test_sync_persists_immutable_cards_and_builds_catalog(self):
        db = FakePricingDatabase()
        repository = PostgresPricingCardRepository(db.connect)
        service = PricingCatalogSyncService(
            source=FeedSource(self.feed), repository=repository, created_by="pricing-sync",
        )
        result = await service.sync()
        self.assertEqual(result.inserted_or_confirmed, 2)
        self.assertEqual(result.source, self.feed.source)
        self.assertEqual(result.catalog.estimate(
            "model-a", input_tokens=1_000_000, output_tokens=500_000,
        ), Decimal("6.00"))

        repeated = await service.sync()
        self.assertEqual(repeated.inserted_or_confirmed, 2)
        self.assertEqual(len(db.cards), 2)

    async def test_same_model_version_cannot_be_mutated(self):
        db = FakePricingDatabase()
        repository = PostgresPricingCardRepository(db.connect)
        await repository.save_feed(self.feed, created_by="pricing-sync")
        changed = PricingFeed(
            source=self.feed.source, revision="2026-11-16-r2",
            fetched_at=self.now + timedelta(minutes=1),
            cards=(
                PricingCard(
                    "model-a", "v1", Decimal("99.00"), Decimal("99.00"),
                    effective_at=self.now,
                ),
            ),
        )
        with self.assertRaises(PricingCardConflict):
            await repository.save_feed(changed, created_by="pricing-sync")

    async def test_future_price_cards_are_not_effective_early(self):
        db = FakePricingDatabase()
        repository = PostgresPricingCardRepository(db.connect)
        future = PricingFeed(
            source="trusted-provider-feed", revision="r2",
            fetched_at=self.now,
            cards=(PricingCard(
                "model-a", "v2", Decimal("3.00"), Decimal("9.00"),
                effective_at=self.now + timedelta(days=1),
            ),),
        )
        await repository.save_feed(future, created_by="pricing-sync")
        current = await repository.list_current(as_of=self.now, source=future.source)
        self.assertEqual(current, [])


if __name__ == "__main__":
    unittest.main()
