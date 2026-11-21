"""Governed ecosystem marketplace lifecycle for reusable AI assets.

This layer owns marketplace metadata and release decisions only. Package trust,
dependency resolution, package scanning and OCI execution are delegated to the
existing extension/supply-chain/sandbox adapters.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9]*)\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_ASSET_KINDS = {"digital_employee", "team_template", "solution", "extension", "skill"}
_VISIBILITIES = {"private", "team", "enterprise", "partner", "public"}


class MarketplaceGateError(ValueError):
    """An asset did not satisfy the marketplace release policy."""


@dataclass(frozen=True)
class MarketplaceAsset:
    tenant_id: str
    asset_id: str
    asset_kind: str
    version: str
    publisher_id: str
    name: str
    description: str
    digest: str
    visibility: str
    license_id: str
    status: str
    signature_verified: bool
    provenance_verified: bool
    scan_passed: bool
    evaluation_passed: bool
    review_status: str = "pending"
    reviewed_by: str | None = None
    trial_status: str = "not_run"
    trial_evidence: Mapping[str, Any] | None = None
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.asset_kind not in _ASSET_KINDS:
            raise ValueError("unsupported marketplace asset kind")
        if self.visibility not in _VISIBILITIES:
            raise ValueError("unsupported marketplace visibility")
        if not _SEMVER.fullmatch(self.version):
            raise ValueError("asset version must be semantic x.y.z")
        if not _DIGEST.fullmatch(self.digest):
            raise ValueError("asset digest must be lowercase SHA-256")
        if self.status not in {"review_pending", "rejected", "approved", "published", "withdrawn"}:
            raise ValueError("invalid marketplace lifecycle status")


class MarketplaceStore(Protocol):
    async def create(self, asset: MarketplaceAsset) -> None: ...
    async def get(self, tenant_id: str, asset_id: str, version: str) -> MarketplaceAsset | None: ...
    async def update(self, asset: MarketplaceAsset) -> None: ...
    async def list_published(self, tenant_id: str, visibility: str | None = None) -> list[MarketplaceAsset]: ...
    async def list_versions(self, tenant_id: str, asset_id: str) -> list[MarketplaceAsset]: ...
    async def add_rating(self, tenant_id: str, asset_id: str, version: str,
                         installation_id: str, user_id: str, score: int, comment: str) -> None: ...
    async def ratings(self, tenant_id: str, asset_id: str, version: str) -> list[Mapping[str, Any]]: ...


class InMemoryMarketplaceStore:
    """Test adapter only; production must supply a durable tenant-bound store."""
    def __init__(self) -> None:
        self.assets: dict[tuple[str, str, str], MarketplaceAsset] = {}
        self._ratings: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}

    async def create(self, asset: MarketplaceAsset) -> None:
        key = (asset.tenant_id, asset.asset_id, asset.version)
        if key in self.assets:
            raise ValueError("asset version already exists and is immutable")
        self.assets[key] = asset

    async def get(self, tenant_id: str, asset_id: str, version: str) -> MarketplaceAsset | None:
        return self.assets.get((tenant_id, asset_id, version))

    async def update(self, asset: MarketplaceAsset) -> None:
        key = (asset.tenant_id, asset.asset_id, asset.version)
        if key not in self.assets:
            raise LookupError("marketplace asset version not found")
        self.assets[key] = asset

    async def list_published(self, tenant_id: str, visibility: str | None = None) -> list[MarketplaceAsset]:
        return sorted((a for a in self.assets.values()
                       if a.tenant_id == tenant_id and a.status == "published"
                       and (visibility is None or a.visibility == visibility)),
                      key=lambda a: (a.asset_id, a.version))

    async def list_versions(self, tenant_id: str, asset_id: str) -> list[MarketplaceAsset]:
        return sorted((a for a in self.assets.values()
                       if a.tenant_id == tenant_id and a.asset_id == asset_id),
                      key=lambda a: tuple(int(x) for x in a.version.split(".")))

    async def add_rating(self, tenant_id: str, asset_id: str, version: str,
                         installation_id: str, user_id: str, score: int, comment: str) -> None:
        key = (tenant_id, asset_id, version)
        rows = self._ratings.setdefault(key, [])
        if any(row["installation_id"] == installation_id for row in rows):
            raise ValueError("installation has already rated this asset version")
        rows.append({"installation_id": installation_id, "user_id": user_id,
                     "score": score, "comment": comment})

    async def ratings(self, tenant_id: str, asset_id: str, version: str) -> list[Mapping[str, Any]]:
        return list(self._ratings.get((tenant_id, asset_id, version), []))


async def _call(callback: Callable[..., Any], **kwargs: Any) -> Any:
    result = callback(**kwargs)
    return await result if inspect.isawaitable(result) else result


class MarketplaceGovernance:
    """Submission, independent review, isolated trial and release-gate policy."""

    def __init__(self, *, store: MarketplaceStore, verify_signature: Callable[..., Any],
                 verify_provenance: Callable[..., Any], scan_package: Callable[..., Any],
                 evaluate_asset: Callable[..., Any], sandbox_trial: Callable[..., Any],
                 verify_installation: Callable[..., Any], audit: Callable[..., Any]) -> None:
        self.store = store
        self.verify_signature = verify_signature
        self.verify_provenance = verify_provenance
        self.scan_package = scan_package
        self.evaluate_asset = evaluate_asset
        self.sandbox_trial = sandbox_trial
        self.verify_installation = verify_installation
        self.audit = audit

    async def submit(self, *, tenant_id: str, publisher_id: str, actor_id: str,
                     asset_id: str, asset_kind: str, version: str, name: str,
                     description: str, digest: str, signature: str,
                     visibility: str, license_id: str) -> MarketplaceAsset:
        if not all(isinstance(v, str) and v.strip() for v in
                   (tenant_id, publisher_id, actor_id, asset_id, name, digest, signature, license_id)):
            raise ValueError("required marketplace submission fields are missing")
        if visibility not in _VISIBILITIES:
            raise ValueError("invalid asset visibility")
        # Verify all external evidence before creating a reviewable version.
        signature_ok = await _call(self.verify_signature, tenant_id=tenant_id,
            publisher_id=publisher_id, asset_id=asset_id, version=version,
            digest=digest, signature=signature)
        provenance_ok = await _call(self.verify_provenance, tenant_id=tenant_id,
            publisher_id=publisher_id, asset_id=asset_id, version=version, digest=digest)
        scan_ok = await _call(self.scan_package, tenant_id=tenant_id, asset_id=asset_id,
            version=version, digest=digest)
        eval_ok = await _call(self.evaluate_asset, tenant_id=tenant_id, asset_id=asset_id,
            asset_kind=asset_kind, version=version, digest=digest)
        if signature_ok is not True or provenance_ok is not True or scan_ok is not True or eval_ok is not True:
            raise MarketplaceGateError("signature, provenance, security scan and evaluation must all pass")
        asset = MarketplaceAsset(
            tenant_id=tenant_id, asset_id=asset_id, asset_kind=asset_kind, version=version,
            publisher_id=publisher_id, name=name.strip(), description=description.strip(),
            digest=digest, visibility=visibility, license_id=license_id,
            status="review_pending", signature_verified=True, provenance_verified=True,
            scan_passed=True, evaluation_passed=True, created_at=datetime.now(timezone.utc),
        )
        await self.store.create(asset)
        await _call(self.audit, tenant_id=tenant_id, actor_id=actor_id,
                    action="marketplace.submit", asset_id=asset_id, version=version, outcome="succeeded")
        return asset

    async def review(self, *, tenant_id: str, asset_id: str, version: str,
                     reviewer_id: str, approved: bool, reason: str) -> MarketplaceAsset:
        asset = await self._required(tenant_id, asset_id, version)
        if asset.status != "review_pending" or reviewer_id == asset.publisher_id:
            raise MarketplaceGateError("review requires a pending submission and an independent reviewer")
        if not reason.strip():
            raise ValueError("review reason is required")
        updated = replace(asset, status="approved" if approved else "rejected",
                          review_status="approved" if approved else "rejected",
                          reviewed_by=reviewer_id)
        await self.store.update(updated)
        await _call(self.audit, tenant_id=tenant_id, actor_id=reviewer_id,
                    action="marketplace.review", asset_id=asset_id, version=version,
                    outcome=updated.review_status)
        return updated

    async def trial(self, *, tenant_id: str, asset_id: str, version: str,
                    actor_id: str, trial_config: Mapping[str, Any]) -> MarketplaceAsset:
        asset = await self._required(tenant_id, asset_id, version)
        if asset.status != "approved":
            raise MarketplaceGateError("only independently approved assets may enter sandbox trial")
        # The adapter must use the shared OCI sandbox with network disabled and
        # no host mounts/credentials; these are mandatory invocation parameters.
        evidence = await _call(self.sandbox_trial, tenant_id=tenant_id, asset_id=asset_id,
            version=version, digest=asset.digest, config=dict(trial_config),
            network_enabled=False, host_mounts=False, credentials_injected=False)
        if not isinstance(evidence, Mapping) or evidence.get("passed") is not True:
            raise MarketplaceGateError("isolated sandbox trial did not pass")
        if (evidence.get("network_disabled") is not True\n                or evidence.get("host_mounts") is not False\n                or evidence.get("credentials_injected") is not False):
            raise MarketplaceGateError("sandbox isolation evidence is incomplete")
        updated = replace(asset, trial_status="passed", trial_evidence=dict(evidence))
        await self.store.update(updated)
        await _call(self.audit, tenant_id=tenant_id, actor_id=actor_id,
                    action="marketplace.sandbox_trial", asset_id=asset_id, version=version,
                    outcome="passed")
        return updated

    async def publish(self, *, tenant_id: str, asset_id: str, version: str,
                      actor_id: str) -> MarketplaceAsset:
        asset = await self._required(tenant_id, asset_id, version)
        gates = {
            "independent_review": asset.review_status == "approved",
            "signature": asset.signature_verified,
            "provenance": asset.provenance_verified,
            "security_scan": asset.scan_passed,
            "evaluation": asset.evaluation_passed,
            "isolated_trial": asset.trial_status == "passed",
        }
        failed = [name for name, passed in gates.items() if not passed]
        if failed or asset.status != "approved":
            raise MarketplaceGateError("release gate blocked: " + ", ".join(failed or ["invalid lifecycle state"]))
        updated = replace(asset, status="published", published_at=datetime.now(timezone.utc))
        await self.store.update(updated)
        await _call(self.audit, tenant_id=tenant_id, actor_id=actor_id,
                    action="marketplace.publish", asset_id=asset_id, version=version, outcome="published")
        return updated

    async def withdraw(self, *, tenant_id: str, asset_id: str, version: str,
                       actor_id: str, reason: str) -> MarketplaceAsset:
        asset = await self._required(tenant_id, asset_id, version)
        if asset.status != "published" or not reason.strip():
            raise MarketplaceGateError("only published assets can be withdrawn with a reason")
        updated = replace(asset, status="withdrawn")
        await self.store.update(updated)
        await _call(self.audit, tenant_id=tenant_id, actor_id=actor_id,
                    action="marketplace.withdraw", asset_id=asset_id, version=version, outcome="withdrawn")
        return updated

    async def rate(self, *, tenant_id: str, asset_id: str, version: str,
                   installation_id: str, user_id: str, score: int, comment: str = "") -> Mapping[str, Any]:
        asset = await self._required(tenant_id, asset_id, version)
        if asset.status != "published" or isinstance(score, bool) or not 1 <= score <= 5:
            raise ValueError("rating requires a published version and score from 1 to 5")
        if user_id == asset.publisher_id:
            raise MarketplaceGateError("publishers cannot rate their own assets")
        verified = await _call(self.verify_installation, tenant_id=tenant_id,
            installation_id=installation_id, asset_id=asset_id, version=version, user_id=user_id)
        if verified is not True:
            raise PermissionError("rating requires a verified installation")
        await self.store.add_rating(tenant_id, asset_id, version, installation_id, user_id, score, comment[:2000])
        rows = await self.store.ratings(tenant_id, asset_id, version)
        average = sum(int(row["score"]) for row in rows) / len(rows)
        await _call(self.audit, tenant_id=tenant_id, actor_id=user_id,
                    action="marketplace.rate", asset_id=asset_id, version=version, outcome="succeeded")
        return {"asset_id": asset_id, "version": version, "rating_count": len(rows),
                "rating_average": round(average, 2)}

    async def catalog(self, *, tenant_id: str, visibility: str | None = None) -> list[MarketplaceAsset]:
        if visibility is not None and visibility not in _VISIBILITIES:
            raise ValueError("invalid asset visibility")
        return await self.store.list_published(tenant_id, visibility)

    async def versions(self, *, tenant_id: str, asset_id: str) -> list[MarketplaceAsset]:
        return await self.store.list_versions(tenant_id, asset_id)

    async def _required(self, tenant_id: str, asset_id: str, version: str) -> MarketplaceAsset:
        item = await self.store.get(tenant_id, asset_id, version)
        if item is None:
            raise LookupError("marketplace asset version not found")
        return item
