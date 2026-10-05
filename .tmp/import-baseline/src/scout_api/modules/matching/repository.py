"""Persistence layer for canonical products, listings, and offer history."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from scout_api.modules.crawler.models.product import ProductOffer, ProductPriceItem
from scout_api.modules.matching.identity import ProductIdentity
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferEvent,
    OfferSnapshot,
    ProductIdentifier,
    StoreListing,
)
from scout_api.modules.matching.offer_diff import (
    fingerprint_from_offer,
    snapshot_dict_from_offer,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_canonical_by_gtin(self, gtin: str) -> CanonicalProduct | None:
        stmt = (
            select(CanonicalProduct)
            .join(ProductIdentifier)
            .where(
                ProductIdentifier.type == "gtin",
                ProductIdentifier.value_normalized == gtin,
            )
            .options(
                selectinload(CanonicalProduct.identifiers),
                selectinload(CanonicalProduct.listings),
            )
        )
        return self._session.scalars(stmt).first()

    def get_canonical(self, canonical_id: uuid.UUID) -> CanonicalProduct | None:
        stmt = (
            select(CanonicalProduct)
            .where(CanonicalProduct.id == canonical_id)
            .options(
                selectinload(CanonicalProduct.identifiers),
                selectinload(CanonicalProduct.listings).selectinload(
                    StoreListing.snapshots
                ),
            )
        )
        return self._session.scalars(stmt).first()

    def get_listing(self, listing_id: uuid.UUID) -> StoreListing | None:
        stmt = (
            select(StoreListing)
            .where(StoreListing.id == listing_id)
            .options(selectinload(StoreListing.snapshots))
        )
        return self._session.scalars(stmt).first()

    def find_listing_by_url(
        self, store: str, canonical_url: str
    ) -> StoreListing | None:
        stmt = select(StoreListing).where(
            StoreListing.store == store,
            StoreListing.canonical_url == canonical_url,
        )
        return self._session.scalars(stmt).first()

    def find_listing_by_store_product_id(
        self,
        store: str,
        product_id: str,
        *,
        country: str | None = None,
    ) -> StoreListing | None:
        stmt = select(StoreListing).where(
            StoreListing.store == store,
            StoreListing.product_id == product_id,
        )
        if country is not None:
            stmt = stmt.where(StoreListing.country == country)
        return self._session.scalars(stmt).first()

    def find_listing_by_store_sku(
        self, store: str, country: str, sku: str
    ) -> StoreListing | None:
        normalized = sku.strip()
        if not normalized:
            return None
        stmt = select(StoreListing).where(
            StoreListing.store == store,
            StoreListing.country == country,
            StoreListing.sku == normalized,
        )
        return self._session.scalars(stmt).first()

    def find_listing_by_store_identity(
        self,
        *,
        store: str,
        country: str,
        product_id: str | None = None,
        sku: str | None = None,
        canonical_url: str | None = None,
    ) -> StoreListing | None:
        """Resolve listing by trusted store keys (never title).

        Priority: canonical_url → store+country+product_id → store+country+sku.
        """
        if canonical_url:
            found = self.find_listing_by_url(store, canonical_url)
            if found is not None:
                return found
        if product_id:
            found = self.find_listing_by_store_product_id(
                store, product_id, country=country
            )
            if found is not None:
                return found
        if sku and sku.strip():
            return self.find_listing_by_store_sku(store, country, sku)
        return None

    def list_listings_for_canonical(
        self, canonical_id: uuid.UUID
    ) -> list[StoreListing]:
        stmt = (
            select(StoreListing)
            .where(StoreListing.canonical_product_id == canonical_id)
            .options(selectinload(StoreListing.snapshots))
        )
        return list(self._session.scalars(stmt).all())

    def list_listings_by_ids(self, listing_ids: list[uuid.UUID]) -> list[StoreListing]:
        if not listing_ids:
            return []
        stmt = select(StoreListing).where(StoreListing.id.in_(listing_ids))
        return list(self._session.scalars(stmt).all())

    def search_canonical_products(
        self,
        *,
        viewer_id: uuid.UUID,
        is_admin: bool,
        brand: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[CanonicalProduct]:
        """Load catalog rows visible to the viewer, optionally prefiltered by brand.

        Model/variant canonical matching happens in the service (chip keys,
        cooler-line flags) so SQL does not confuse ``rtx5070`` with ``rtx5070ti``.
        """
        stmt = select(CanonicalProduct).options(
            selectinload(CanonicalProduct.identifiers),
            selectinload(CanonicalProduct.listings),
        )
        if not is_admin:
            stmt = stmt.where(
                or_(
                    CanonicalProduct.owner_user_id.is_(None),
                    CanonicalProduct.owner_user_id == viewer_id,
                )
            )
        if brand and brand.strip():
            stmt = stmt.where(
                func.lower(CanonicalProduct.brand) == brand.strip().casefold()
            )
        stmt = (
            stmt.order_by(CanonicalProduct.updated_at.desc())
            .offset(max(0, offset))
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def count_canonical_products(
        self,
        *,
        viewer_id: uuid.UUID,
        is_admin: bool,
    ) -> int:
        stmt = select(func.count()).select_from(CanonicalProduct)
        if not is_admin:
            stmt = stmt.where(
                or_(
                    CanonicalProduct.owner_user_id.is_(None),
                    CanonicalProduct.owner_user_id == viewer_id,
                )
            )
        return int(self._session.scalar(stmt) or 0)

    def delete_canonical(self, product_id: uuid.UUID) -> bool:
        product = self.get_canonical(product_id)
        if product is None:
            return False
        self._session.delete(product)
        self._session.flush()
        return True

    def update_canonical(
        self,
        product: CanonicalProduct,
        *,
        title: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        variant_key: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> CanonicalProduct:
        if title is not None:
            product.title = title[:512]
        if brand is not None:
            product.brand = brand or None
        if model is not None:
            product.model = model or None
        if variant_key is not None:
            product.variant_key = variant_key or None
        if attributes is not None:
            merged = dict(product.attributes or {})
            merged.update(attributes)
            product.attributes = merged
        self._session.flush()
        return product

    def get_or_create_canonical(
        self,
        *,
        title: str,
        brand: str | None = None,
        model: str | None = None,
        variant_key: str | None = None,
        attributes: dict[str, Any] | None = None,
        gtin: str | None = None,
        owner_user_id: uuid.UUID | None = None,
    ) -> tuple[CanonicalProduct, bool]:
        """Return existing canonical by GTIN or create a new one.

        Never silently overwrites fields of an existing product.
        Returns ``(product, created)``. Concurrent GTIN inserts reuse the row.
        """
        if gtin:
            existing = self.find_canonical_by_gtin(gtin)
            if existing is not None:
                return existing, False
        product = CanonicalProduct(
            title=title[:512],
            brand=brand,
            model=model,
            variant_key=variant_key,
            attributes=attributes or {},
            owner_user_id=owner_user_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(product)
                self._session.flush()
                if gtin:
                    self._session.add(
                        ProductIdentifier(
                            canonical_product_id=product.id,
                            type="gtin",
                            value_normalized=gtin,
                        )
                    )
                    self._session.flush()
            return product, True
        except IntegrityError:
            if gtin:
                existing = self.find_canonical_by_gtin(gtin)
                if existing is not None:
                    return existing, False
            raise

    def get_or_create_listing(
        self,
        *,
        canonical: CanonicalProduct,
        store: str,
        country: str,
        product_id: str,
        url: str,
        canonical_url: str,
        sku: str | None = None,
        gtin: str | None = None,
        title: str | None = None,
        match_decision: str = "auto_match",
        confidence: Decimal = Decimal("1.0000"),
        status: str = "active",
    ) -> tuple[StoreListing, bool]:
        """Return listing by store identity keys or create one (no overwrite)."""
        existing = self.find_listing_by_store_identity(
            store=store,
            country=country,
            product_id=product_id,
            sku=sku,
            canonical_url=canonical_url,
        )
        if existing is not None:
            return existing, False
        listing = StoreListing(
            canonical_product_id=canonical.id,
            store=store,
            country=country,
            product_id=product_id,
            sku=sku.strip() if sku and sku.strip() else None,
            gtin=gtin,
            url=url,
            canonical_url=canonical_url,
            match_decision=match_decision,
            confidence=confidence,
            status=status,
            title=title[:512] if title else None,
        )
        try:
            with self._session.begin_nested():
                self._session.add(listing)
                self._session.flush()
            return listing, True
        except IntegrityError:
            existing = self.find_listing_by_store_identity(
                store=store,
                country=country,
                product_id=product_id,
                sku=sku,
                canonical_url=canonical_url,
            )
            if existing is not None:
                return existing, False
            raise

    def upsert_canonical_from_identity(
        self,
        identity: ProductIdentity,
        *,
        title: str,
        attributes: dict[str, Any] | None = None,
    ) -> CanonicalProduct:
        product: CanonicalProduct | None = None
        if identity.gtin:
            product = self.find_canonical_by_gtin(identity.gtin)
        if product is None:
            product = CanonicalProduct(
                title=title[:512],
                brand=identity.brand,
                model=identity.model,
                variant_key=identity.variant_key,
                attributes=attributes or dict(identity.variant_attrs),
            )
            self._session.add(product)
            self._session.flush()
            if identity.gtin:
                self._session.add(
                    ProductIdentifier(
                        canonical_product_id=product.id,
                        type="gtin",
                        value_normalized=identity.gtin,
                    )
                )
        else:
            product.title = title[:512]
            product.brand = identity.brand or product.brand
            product.model = identity.model or product.model
            product.variant_key = identity.variant_key or product.variant_key
            if attributes:
                merged = dict(product.attributes or {})
                merged.update(attributes)
                product.attributes = merged
            product.updated_at = _utcnow()
        self._session.flush()
        return product

    def has_gtin(self, canonical_id: uuid.UUID, gtin: str) -> bool:
        stmt = select(ProductIdentifier).where(
            ProductIdentifier.canonical_product_id == canonical_id,
            ProductIdentifier.type == "gtin",
            ProductIdentifier.value_normalized == gtin,
        )
        return self._session.scalars(stmt).first() is not None

    def ensure_gtin_identifier(
        self,
        canonical: CanonicalProduct,
        gtin: str,
        *,
        source: str,
    ) -> bool:
        """Attach a validated GTIN to the canonical product if missing.

        Returns True when a new identifier row was created.
        """
        if self.has_gtin(canonical.id, gtin):
            return False
        # Refuse to attach a second different GTIN silently.
        existing = self._session.scalars(
            select(ProductIdentifier).where(
                ProductIdentifier.canonical_product_id == canonical.id,
                ProductIdentifier.type == "gtin",
            )
        ).first()
        if existing is not None and existing.value_normalized != gtin:
            return False
        try:
            with self._session.begin_nested():
                self._session.add(
                    ProductIdentifier(
                        canonical_product_id=canonical.id,
                        type="gtin",
                        value_normalized=gtin,
                    )
                )
                attrs = dict(canonical.attributes or {})
                attrs["gtin_source"] = source
                canonical.attributes = attrs
                canonical.updated_at = _utcnow()
                self._session.flush()
            return True
        except IntegrityError:
            # Another transaction attached the same GTIN concurrently.
            if self.has_gtin(canonical.id, gtin):
                return False
            other = self.find_canonical_by_gtin(gtin)
            if other is not None and other.id != canonical.id:
                return False
            raise

    def upsert_listing(
        self,
        *,
        canonical: CanonicalProduct,
        item: ProductPriceItem,
        decision: str,
        confidence: Decimal,
        status: str = "active",
        allow_reparent: bool = False,
    ) -> StoreListing:
        listing = self.find_listing_by_store_identity(
            store=item.store,
            country=item.country,
            product_id=item.product_id,
            sku=item.sku,
            canonical_url=item.canonical_url,
        )
        if listing is None:
            listing = StoreListing(
                canonical_product_id=canonical.id,
                store=item.store,
                country=item.country,
                product_id=item.product_id,
                sku=item.sku.strip() if item.sku and item.sku.strip() else None,
                gtin=item.gtin,
                url=item.url,
                canonical_url=item.canonical_url,
                match_decision=decision,
                confidence=confidence,
                status=status,
                title=item.title[:512] if item.title else None,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(listing)
                    self._session.flush()
            except IntegrityError:
                existing = self.find_listing_by_store_identity(
                    store=item.store,
                    country=item.country,
                    product_id=item.product_id,
                    sku=item.sku,
                    canonical_url=item.canonical_url,
                )
                if existing is None:
                    raise
                listing = existing
            else:
                from scout_api.modules.monitoring.hooks import (
                    initialize_listing_schedule,
                )

                initialize_listing_schedule(listing, checked_at=None)
                return listing

        # Existing listing path (found upfront or after IntegrityError).
        if listing.canonical_product_id != canonical.id:
            if not allow_reparent:
                logger.warning(
                    "listing_reparent_skipped",
                    extra={
                        "listing_id": str(listing.id),
                        "existing_canonical": str(listing.canonical_product_id),
                        "requested_canonical": str(canonical.id),
                        "store": listing.store,
                    },
                )
                return listing
            logger.info(
                "listing_reparented",
                extra={
                    "listing_id": str(listing.id),
                    "from_canonical": str(listing.canonical_product_id),
                    "to_canonical": str(canonical.id),
                },
            )
            listing.canonical_product_id = canonical.id

        listing.product_id = item.product_id
        listing.sku = (
            item.sku.strip() if item.sku and item.sku.strip() else listing.sku
        )
        listing.gtin = item.gtin
        listing.url = item.url
        listing.canonical_url = item.canonical_url
        listing.match_decision = decision
        listing.confidence = confidence
        listing.status = status
        listing.title = item.title[:512] if item.title else listing.title
        listing.updated_at = _utcnow()
        self._session.flush()
        return listing

    def latest_snapshot(self, listing_id: uuid.UUID) -> OfferSnapshot | None:
        stmt = (
            select(OfferSnapshot)
            .where(OfferSnapshot.listing_id == listing_id)
            .order_by(OfferSnapshot.scraped_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def append_snapshot_from_offer(
        self, listing: StoreListing, offer: ProductOffer
    ) -> OfferSnapshot:
        snapshot = OfferSnapshot(
            listing_id=listing.id,
            price=offer.price,
            currency=offer.currency,
            seller=offer.seller,
            availability=offer.availability,
            available=offer.available,
            fingerprint=fingerprint_from_offer(offer),
            payload=snapshot_dict_from_offer(offer),
            scraped_at=offer.scraped_at,
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def append_event(
        self,
        listing: StoreListing,
        event_type: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> OfferEvent:
        event = OfferEvent(
            listing_id=listing.id,
            event_type=event_type,
            before=before,
            after=after,
            detected_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def mark_listing_removed(self, listing: StoreListing) -> None:
        listing.status = "removed"
        listing.updated_at = _utcnow()
        from scout_api.modules.monitoring.hooks import mark_removed_listing

        mark_removed_listing(listing)
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def flush(self) -> None:
        self._session.flush()
