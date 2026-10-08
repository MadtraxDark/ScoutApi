"""Isolated persistent PostgreSQL scenario; storefront responses are mocked."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.matching.activity_service import ActivityService
from scout_api.modules.matching.models import CanonicalProduct, OfferEvent, StoreListing
from scout_api.modules.matching.offer_refresh_service import OfferRefreshService
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.schemas import (
    OfferRefreshRequest,
    ProductRegisterRequest,
)
from scout_api.modules.monitoring.service import OfferMonitorService

pytestmark = pytest.mark.integration


def run_activity_scenario(engine):
    owner = AuthenticatedPrincipal(id=uuid4())
    suffix = uuid4().hex
    now = datetime.now(UTC)
    with Session(engine) as db:
        registered = ProductRegistrationService(db).register_saved(
            ProductRegisterRequest(
                title=f"Produto de teste integrado {suffix[:8]}",
                brand="Marca de teste",
                store="kabum",
                country="BR",
                product_id=suffix,
                url=f"https://www.kabum.com.br/produto/{suffix}",
                price=Decimal("2499.90"),
                currency="BRL",
            ),
            owner=owner,
        )
        product_id = registered.product.id
        listing = db.scalar(
            select(StoreListing).where(StoreListing.canonical_product_id == product_id)
        )
        listing_id = listing.id
        first = ActivityService(db).list_recent(viewer=owner)
        assert {item.type for item in first.items} == {"product_added", "new_offer"}
        assert next(
            item for item in first.items if item.type == "new_offer"
        ).offer.new_price == Decimal("2499.90")
        # Catalog image references are test-only; no Drive upload or external fetch.
        image = ProductImageRepository(db).create(
            product_id=product_id,
            source_url="https://example.test/external.jpg",
            position=0,
            is_main=True,
            original_status="ready",
            original_drive_file_id="isolated-test-original",
            optimized_status="ready",
            optimized_drive_file_id="isolated-test-avif",
        )
        image_id = image.id
        scraper = MagicMock()
        refresh = OfferRefreshService(session=db, offer_service=scraper)

        def offer(price, **extra):
            return ProductOffer(
                store="kabum",
                country="BR",
                product_id=suffix,
                url=listing.url,
                canonical_url=listing.canonical_url,
                price=Decimal(price),
                currency="BRL",
                availability="available",
                available=True,
                scraped_at=datetime.now(UTC),
                **extra,
            )

        scraper.scrape_offer.return_value = offer("2199.90")
        refresh.refresh(OfferRefreshRequest(listing_ids=[listing_id]))
        changed = next(
            item
            for item in ActivityService(db).list_recent(viewer=owner).items
            if item.type == "price_changed"
        )
        assert changed.offer.old_price == Decimal("2499.90")
        assert changed.offer.new_price == Decimal("2199.90")
        scraper.scrape_offer.side_effect = RequestError(
            "Isolated failed scrape", code="UPSTREAM_BLOCKED"
        )
        refresh.refresh(OfferRefreshRequest(listing_ids=[listing_id]))
        assert not any(
            item.type == "offer_removed"
            for item in ActivityService(db).list_recent(viewer=owner).items
        )
        scraper.scrape_offer.side_effect = None
        scraper.scrape_offer.return_value = offer(
            "1899.90",
            metadata={
                "promotion": {
                    "status": "active",
                    "promotion_price": "1899.90",
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                }
            },
        )
        refresh.refresh(OfferRefreshRequest(listing_ids=[listing_id]))
        assert listing.promotion_status == "active"
        OfferMonitorService(session=db).apply_wall_clock_promo_expiry(
            listing, now=now + timedelta(hours=2)
        )
        assert listing.status == "active"  # Promotion expiry did not remove the offer.
        expired = next(
            item
            for item in ActivityService(db).list_recent(viewer=owner).items
            if item.type == "promotion_expired"
        )
        assert expired.offer.old_price == Decimal("1899.90")
        scraper.scrape_offer.side_effect = ParseError("Isolated removed product")
        refresh.refresh(OfferRefreshRequest(listing_ids=[listing_id]))
        removed = next(
            item
            for item in ActivityService(db).list_recent(viewer=owner).items
            if item.type == "offer_removed"
        )
        assert removed.offer.old_price == Decimal("1899.90")
        db.commit()
        ids = [item.id for item in ActivityService(db).list_recent(viewer=owner).items]
    # New DB connection represents a reload; activity comes from persisted rows.
    with Session(engine) as reloaded:
        page = ActivityService(reloaded).list_recent(viewer=owner)
        assert [item.id for item in page.items] == ids
        assert all(
            item.product.primary_image_url
            == f"/products/{product_id}/images/{image_id}/content?variant=optimized"
            for item in page.items
        )
        kinds = [item.type for item in page.items]
        assert (
            kinds.count("price_changed") == 1
        )  # Promotion activation absorbed redundant price event.
        assert "scrape_failed" not in kinds
        cursor = None
        paginated = []
        while True:
            batch = ActivityService(reloaded).list_recent(
                viewer=owner, limit=2, cursor=cursor
            )
            paginated.extend(item.id for item in batch.items)
            cursor = batch.next_cursor
            if not cursor:
                break
        assert paginated == ids
        assert (
            len(
                list(
                    reloaded.scalars(
                        select(OfferEvent).where(OfferEvent.listing_id == listing_id)
                    )
                )
            )
            > len(page.items) - 1
        )
    return product_id, image_id, owner


def test_persistent_activity_on_isolated_postgres():
    url = os.environ.get("TEST_ACTIVITY_DATABASE_URL")
    if not url:
        pytest.skip(
            "Configure TEST_ACTIVITY_DATABASE_URL para PostgreSQL local isolado"
        )
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (
        parsed.database or ""
    ).startswith("scout_activity_test"):
        pytest.fail("Este cenário exige um banco local separado scout_activity_test*")
    engine = create_engine(url)
    product_id = None
    try:
        product_id, _, _ = run_activity_scenario(engine)
    finally:
        if product_id:
            with engine.begin() as conn:
                conn.execute(
                    delete(CanonicalProduct).where(CanonicalProduct.id == product_id)
                )
        engine.dispose()
