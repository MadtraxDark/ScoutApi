"""Deterministic tests for persistent offer monitoring schedule (ADR 0030)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.core.database import Base
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.models.product import ProductOffer, ProductPriceItem
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.identity import ProductIdentity
from scout_api.modules.matching.models import OfferEvent, StoreListing
from scout_api.modules.matching.offer_diff import diff_offers
from scout_api.modules.matching.offer_refresh_service import OfferRefreshService
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import OfferRefreshRequest
from scout_api.modules.monitoring.claim import claim_due_listings, release_claim
from scout_api.modules.monitoring.extractors import extract_terabyte_countdown
from scout_api.modules.monitoring.hooks import mark_check_failure, mark_check_success
from scout_api.modules.monitoring.promotion import (
    PromotionObservation,
    apply_promotion_observation,
    expire_due_promotions,
    is_promotion_commercially_active,
)
from scout_api.modules.monitoring.schedule import (
    compute_next_check_at,
    compute_next_regular_check_at,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _settings(**overrides: object) -> MagicMock:
    settings = MagicMock()
    settings.offer_refresh_interval_hours = 12
    settings.offer_monitor_jitter_seconds = 0
    settings.offer_promotion_grace_seconds = 0
    settings.offer_monitor_retry_base_seconds = 300
    settings.offer_monitor_retry_max_seconds = 3600
    settings.offer_monitor_batch_size = 10
    settings.offer_monitor_lease_seconds = 300
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _listing(session: Session) -> StoreListing:
    repo = MatchingRepository(session)
    identity = ProductIdentity(
        gtin="7891991010863",
        brand="Acme",
        model="X1",
        title="Produto Teste",
        title_normalized="produto teste",
        variant_attrs={},
    )
    canonical = repo.upsert_canonical_from_identity(identity, title="Produto Teste")
    item = ProductPriceItem.model_validate(
        {
            "store": "kabum",
            "country": "BR",
            "product_id": "123",
            "sku": "SKU-1",
            "url": "https://www.kabum.com.br/produto/123",
            "canonical_url": "https://www.kabum.com.br/produto/123",
            "title": "Produto Teste",
            "price": Decimal("100.00"),
            "currency": "BRL",
            "seller": "KaBuM!",
            "availability": "available",
            "available": True,
        }
    )
    return repo.upsert_listing(
        canonical=canonical,
        item=item,
        decision="auto_match",
        confidence=Decimal("0.9500"),
    )


def _offer(price: str = "100.00", **overrides: object) -> ProductOffer:
    base: dict[str, object] = {
        "store": "kabum",
        "country": "BR",
        "product_id": "123",
        "url": "https://www.kabum.com.br/produto/123",
        "canonical_url": "https://www.kabum.com.br/produto/123",
        "currency": "BRL",
        "price": Decimal(price),
        "seller": "KaBuM!",
        "availability": "available",
        "available": True,
        "scraped_at": datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return ProductOffer.model_validate(base)


def test_schedule_not_due_before_interval() -> None:
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    settings = _settings()
    regular = compute_next_regular_check_at(now=now, settings=settings)
    assert regular == now + timedelta(hours=12)
    next_at = compute_next_check_at(now=now, next_regular=regular, settings=settings)
    assert next_at == now + timedelta(hours=12)
    almost = now + timedelta(hours=11, minutes=59)
    assert almost < next_at


def test_schedule_due_at_exact_interval() -> None:
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    settings = _settings()
    next_at = compute_next_check_at(
        now=now,
        next_regular=compute_next_regular_check_at(now=now, settings=settings),
        settings=settings,
    )
    assert now + timedelta(hours=12) == next_at


def test_promo_expires_before_regular_pulls_next_check() -> None:
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    settings = _settings()
    regular = compute_next_regular_check_at(now=now, settings=settings)
    promo_end = now + timedelta(hours=4)  # 14:00
    next_at = compute_next_check_at(
        now=now,
        next_regular=regular,
        promotion_expires_at=promo_end,
        settings=settings,
    )
    assert next_at == promo_end


def test_restart_recovery_due_immediately(session: Session) -> None:
    listing = _listing(session)
    listing.monitoring_enabled = True
    listing.status = "active"
    listing.next_check_at = datetime(2026, 9, 21, 2, 0, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    claimed = claim_due_listings(session, worker_id="w1", now=now, settings=_settings())
    assert len(claimed) == 1
    assert claimed[0].id == listing.id
    assert claimed[0].last_check_delay_seconds == 6 * 3600


def test_multiple_missed_intervals_single_claim(session: Session) -> None:
    listing = _listing(session)
    listing.monitoring_enabled = True
    listing.next_check_at = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    claimed = claim_due_listings(session, worker_id="w1", now=now, settings=_settings())
    assert len(claimed) == 1
    # One claim only — no fabricated historical windows.
    release_claim(listing)
    listing.next_check_at = now + timedelta(hours=12)
    session.flush()
    claimed_again = claim_due_listings(
        session, worker_id="w2", now=now, settings=_settings()
    )
    assert claimed_again == []


def test_promo_expires_during_downtime(session: Session) -> None:
    listing = _listing(session)
    listing.promotion_status = "active"
    listing.promotion_expires_at = datetime(2026, 9, 21, 3, 0, tzinfo=UTC)
    listing.next_check_at = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    assert expire_due_promotions(listing, now=now) is True
    assert listing.promotion_status == "expired"
    assert is_promotion_commercially_active(listing, now=now) is False
    listing.next_check_at = now
    claimed = claim_due_listings(session, worker_id="w1", now=now, settings=_settings())
    assert len(claimed) == 1


def test_price_unchanged_updates_last_checked(session: Session) -> None:
    listing = _listing(session)
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    offer = _offer("100.00")
    mark_check_success(
        listing, offer=offer, event_names=["unchanged"], now=now, settings=_settings()
    )
    assert listing.last_checked_at == now
    assert listing.last_successful_check_at == now
    assert listing.next_check_at == now + timedelta(hours=12)


def test_price_changed_event_taxonomy() -> None:
    previous = {
        "price": "100.00",
        "currency": "BRL",
        "seller": "kabum!",
        "availability": "available",
        "fingerprint": "x",
    }
    current = _offer("90.00")
    # Force fingerprint path via price change.
    previous["fingerprint"] = "old"
    diff = diff_offers(previous, current)
    assert "price_changed" in diff.events


def test_availability_out_of_stock_not_from_blocked() -> None:
    previous = {
        "price": "100.00",
        "currency": "BRL",
        "seller": "kabum!",
        "availability": "available",
        "fingerprint": "old",
    }
    blocked = diff_offers(previous, None, scrape_failed=True)
    assert blocked.events == ("scrape_failed",)
    current = _offer("100.00", availability="out_of_stock", available=False)
    diff = diff_offers(previous, current)
    assert "out_of_stock" in diff.events or "availability_changed" in diff.events


def test_concurrency_only_one_worker_claims(session: Session) -> None:
    listing = _listing(session)
    listing.monitoring_enabled = True
    listing.next_check_at = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    first = claim_due_listings(session, worker_id="w1", now=now, settings=_settings())
    second = claim_due_listings(session, worker_id="w2", now=now, settings=_settings())
    assert len(first) == 1
    assert second == []


def test_worker_crash_lease_recovery(session: Session) -> None:
    listing = _listing(session)
    listing.monitoring_enabled = True
    listing.next_check_at = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    listing.check_worker_id = "dead"
    listing.check_claimed_at = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    listing.check_claim_expires_at = datetime(2026, 9, 21, 9, 5, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    claimed = claim_due_listings(session, worker_id="w2", now=now, settings=_settings())
    assert len(claimed) == 1
    assert claimed[0].check_worker_id == "w2"


def test_manual_refresh_reschedules_not_immediately_due(session: Session) -> None:
    listing = _listing(session)
    listing.monitoring_enabled = True
    listing.next_check_at = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    session.flush()
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    mark_check_success(
        listing,
        offer=_offer(),
        event_names=["unchanged"],
        now=now,
        settings=_settings(),
    )
    session.flush()
    assert listing.next_check_at == now + timedelta(hours=12)
    claimed = claim_due_listings(session, worker_id="w1", now=now, settings=_settings())
    assert claimed == []


def test_transient_failure_uses_retry_not_12h(session: Session) -> None:
    listing = _listing(session)
    now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    mark_check_failure(listing, error="timeout", now=now, settings=_settings())
    assert listing.next_check_at == now + timedelta(seconds=300)
    assert listing.consecutive_failures == 1


def test_refresh_known_listing_uses_persisted_pdp_and_records_price_change(
    session: Session,
) -> None:
    listing = _listing(session)
    repo = MatchingRepository(session)
    repo.append_snapshot_from_offer(listing, _offer("300.00"))
    scraper = MagicMock()
    scraper.scrape_offer.return_value = _offer("350.00", seller="Loja Parceira")

    response = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )

    assert scraper.scrape_offer.call_args.args == (listing.url,)
    assert response.results[0].status == "changed"
    assert "price_changed" in [event.event_type for event in response.results[0].events]
    latest = repo.latest_snapshot(listing.id)
    assert latest is not None
    assert latest.price == Decimal("350.00")
    assert latest.seller == "Loja Parceira"


def test_refresh_unchanged_is_successful_and_out_of_stock_stays_known(
    session: Session,
) -> None:
    listing = _listing(session)
    repo = MatchingRepository(session)
    repo.append_snapshot_from_offer(listing, _offer("100.00"))
    scraper = MagicMock()
    scraper.scrape_offer.return_value = _offer("100.00")

    unchanged = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )
    assert unchanged.results[0].status == "unchanged"

    scraper.scrape_offer.return_value = _offer(
        "100.00", availability="out_of_stock", available=False
    )
    out_of_stock = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )
    assert out_of_stock.results[0].status == "out_of_stock"
    assert listing.status == "active"


def test_refresh_confirmed_missing_pdp_marks_removed_not_no_match(
    session: Session,
) -> None:
    listing = _listing(session)
    scraper = MagicMock()
    scraper.scrape_offer.side_effect = ParseError("PDP removida")

    response = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )

    assert response.results[0].status == "removed"
    assert listing.status == "removed"
    events = session.scalars(
        select(OfferEvent.event_type).where(OfferEvent.listing_id == listing.id)
    ).all()
    assert events == ["offer_removed"]


def test_refresh_processes_all_active_listings_for_one_store(
    session: Session,
) -> None:
    listing = _listing(session)
    canonical = listing.canonical_product
    second = MatchingRepository(session).upsert_listing(
        canonical=canonical,
        item=ProductPriceItem.model_validate(
            {
                "store": "kabum",
                "country": "BR",
                "product_id": "456",
                "url": "https://www.kabum.com.br/produto/456",
                "canonical_url": "https://www.kabum.com.br/produto/456",
                "title": "Produto Teste",
                "price": Decimal("120.00"),
                "currency": "BRL",
            }
        ),
        decision="auto_match",
        confidence=Decimal("0.9500"),
    )
    scraper = MagicMock()
    scraper.scrape_offer.side_effect = [
        _offer(),
        _offer(
            "120.00",
            product_id="456",
            url=second.url,
            canonical_url=second.canonical_url,
        ),
    ]

    response = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(canonical_product_id=canonical.id)
    )

    assert {result.listing_id for result in response.results} == {
        listing.id,
        second.id,
    }
    assert scraper.scrape_offer.call_count == 2


def test_refresh_timeout_preserves_known_listing_and_records_scrape_failed(
    session: Session,
) -> None:
    listing = _listing(session)
    scraper = MagicMock()
    scraper.scrape_offer.side_effect = RequestError(
        "timeout", code="NETWORK_TIMEOUT", retryable=True
    )

    response = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )

    assert response.results[0].status == "scrape_failed"
    assert listing.status == "active"
    events = session.scalars(
        select(OfferEvent.event_type).where(OfferEvent.listing_id == listing.id)
    ).all()
    assert events == ["scrape_failed"]


def test_refresh_defers_when_monitor_already_claimed_listing(session: Session) -> None:
    from datetime import timedelta

    listing = _listing(session)
    listing.check_worker_id = "monitor-worker"
    listing.check_claimed_at = datetime.now(UTC)
    listing.check_claim_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    scraper = MagicMock()

    response = OfferRefreshService(session=session, offer_service=scraper).refresh(
        OfferRefreshRequest(listing_ids=[listing.id])
    )

    assert response.results[0].status == "deferred"
    scraper.scrape_offer.assert_not_called()


def test_terabyte_countdown_extractor() -> None:
    html = (
        "<script>$('#ctd41251').countdown('2026/09/28 10:00:59')"
        ".on('update.countdown', function(event) {});</script>"
    )
    obs = extract_terabyte_countdown(html, product_id="41251")
    assert obs is not None
    assert obs.expires_at is not None
    assert obs.expires_at.tzinfo is not None
    assert obs.product_id == "41251"
    assert obs.source == "terabyte.jquery.countdown"


def test_promotion_sku_guard(session: Session) -> None:
    listing = _listing(session)
    listing.sku = "SKU-A"
    obs = PromotionObservation(
        status="active",
        expires_at=datetime(2026, 9, 21, 18, 0, tzinfo=UTC),
        sku="SKU-B",
        source="test",
    )
    events = apply_promotion_observation(listing, obs)
    assert events == []
    assert listing.promotion_status == "none"


def test_create_all_includes_monitor_columns(session: Session) -> None:
    listing = _listing(session)
    assert hasattr(listing, "next_check_at")
    assert hasattr(listing, "promotion_expires_at")
    assert Base.metadata.tables["monitor_scheduler_state"] is not None
