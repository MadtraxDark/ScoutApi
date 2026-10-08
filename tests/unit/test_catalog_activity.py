"""Persistent activity read-model tests. Data below is isolated test data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from scout_api.core.config import get_settings
from scout_api.main import app
from scout_api.modules.auth.deps import require_authenticated_user
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.matching.activity_service import ActivityService
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.models import OfferEvent
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.router import get_activity_service

NOW = datetime(2026, 10, 7, 21, 0, tzinfo=UTC)
OWNER = AuthenticatedPrincipal(id=uuid4())
ADMIN = AuthenticatedPrincipal(id=uuid4(), role=UserRole.ADMIN)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def product_listing(session, *, owner=OWNER.id, store="kabum"):
    repo = MatchingRepository(session)
    product, _ = repo.get_or_create_canonical(
        title="Produto de teste isolado",
        brand="Marca de teste",
        owner_user_id=owner,
    )
    product.created_at = NOW - timedelta(days=1)
    listing, _ = repo.get_or_create_listing(
        canonical=product,
        store=store,
        country="BR",
        product_id=str(uuid4()),
        url=f"https://example.test/{uuid4()}",
        canonical_url=f"https://example.test/{uuid4()}",
    )
    session.flush()
    return product, listing


def add_event(session, listing, kind, *, before=None, after=None, at=NOW):
    return MatchingRepository(session).append_event(
        listing,
        kind,
        before=before,
        after=after,
        detected_at=at,
    )


def feed(session, **kwargs):
    return ActivityService(session).list_recent(
        viewer=kwargs.pop("viewer", OWNER), **kwargs
    )


def test_product_added_persisted_and_no_external_image(session):
    product, _ = product_listing(session)
    page = feed(session)
    assert len(page.items) == 1
    item = page.items[0]
    assert item.type == "product_added"
    assert item.occurred_at == NOW - timedelta(days=1)
    assert item.product.id == product.id
    assert item.product.primary_image_url is None
    assert item.offer is None and item.store is None


@pytest.mark.parametrize("kind", ["new_offer", "offer_created"])
def test_new_offer(session, kind):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        kind,
        after={"price": "100", "pix_price": "90", "currency": "BRL"},
    )
    item = feed(session).items[0]
    assert item.type == "new_offer"
    assert item.offer.new_price == Decimal("90")
    assert item.offer.new_price_kind == "pix"
    assert item.store.display_name == "KaBuM!"


@pytest.mark.parametrize(
    "old,new,direction",
    [
        ("2499.90", "2199.90", "decreased"),
        ("100", "120", "increased"),
    ],
)
def test_price_changed_uses_history(session, old, new, direction):
    _, listing = product_listing(session)
    listing.promotion_price = Decimal("9999")
    add_event(
        session,
        listing,
        "price_changed",
        before={"price": old, "currency": "BRL"},
        after={"price": new, "currency": "BRL"},
    )
    offer = feed(session).items[0].offer
    assert offer.old_price == Decimal(old)
    assert offer.new_price == Decimal(new)
    assert offer.price_direction == direction


def test_pix_and_currency_changes_are_not_converted(session):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        "price_changed",
        before={"price": "499", "pix_price": "489", "currency": "USD"},
        after={"price": "2500", "currency": "BRL"},
    )
    offer = feed(session).items[0].offer
    assert offer.old_price == Decimal("489") and offer.old_currency == "USD"
    assert offer.new_price == Decimal("2500") and offer.currency == "BRL"
    assert offer.price_direction is None


@pytest.mark.parametrize("kind", ["offer_removed", "out_of_stock", "promotion_expired"])
def test_terminal_events_keep_last_price_and_distinct_type(session, kind):
    _, listing = product_listing(session)
    before = {"price": "4599", "currency": "BRL"}
    if kind == "promotion_expired":
        before["promotion_price"] = "1899"
    add_event(session, listing, kind, before=before, after={"status": "expired"})
    item = feed(session).items[0]
    assert item.type == kind
    assert item.offer.old_price == Decimal(
        "1899" if kind == "promotion_expired" else "4599"
    )
    assert item.offer.new_price is None


@pytest.mark.parametrize("kind", ["promotion_activated", "promotion_updated"])
def test_promotion_price_not_regular_price(session, kind):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        kind,
        before={"currency": "BRL", "promotion_price": "170"},
        after={
            "price": "200",
            "pix_price": "190",
            "currency": "BRL",
            "promotion_payload": {"promotion_price": "150"},
        },
    )
    offer = feed(session).items[0].offer
    assert offer.old_price == Decimal("170") and offer.new_price == Decimal("150")
    assert offer.new_price_kind == "promotion"


def test_technical_events_excluded_without_removal(session):
    _, listing = product_listing(session)
    for kind in ("scrape_failed", "unchanged", "seller_changed", "gtin_learned"):
        add_event(session, listing, kind, after={"error": "isolated test"})
    assert [item.type for item in feed(session).items] == ["product_added"]


@pytest.mark.parametrize(
    "primary,secondary",
    [
        ("out_of_stock", "availability_changed"),
        ("promotion_expired", "price_changed"),
        ("promotion_activated", "price_changed"),
        ("promotion_updated", "price_changed"),
    ],
)
def test_dedup_before_pagination_preserves_raw_history(session, primary, secondary):
    _, listing = product_listing(session)
    add_event(session, listing, secondary, after={"price": "100"})
    add_event(session, listing, primary, after={"promotion_price": "90"})
    page = feed(session, limit=1)
    assert page.items[0].type == primary
    assert page.next_cursor
    assert (
        feed(session, limit=1, cursor=page.next_cursor).items[0].type == "product_added"
    )
    assert len(list(session.scalars(select(OfferEvent)))) == 2


def test_legacy_observation_dedup_even_with_different_detection_times(session):
    _, listing = product_listing(session)
    after = {"scraped_at": NOW.isoformat(), "availability": "out_of_stock"}
    add_event(session, listing, "availability_changed", after=after)
    add_event(
        session,
        listing,
        "out_of_stock",
        after=after,
        at=NOW + timedelta(milliseconds=5),
    )
    assert [item.type for item in feed(session).items] == [
        "out_of_stock",
        "product_added",
    ]


def test_separate_observations_not_deduplicated(session):
    _, listing = product_listing(session)
    add_event(session, listing, "price_changed", at=NOW)
    add_event(session, listing, "promotion_expired", at=NOW + timedelta(seconds=1))
    assert [item.type for item in feed(session).items] == [
        "promotion_expired",
        "price_changed",
        "product_added",
    ]


def test_descending_cursor_ties_and_new_insert(session):
    for _ in range(4):
        product, listing = product_listing(session)
        product.created_at = NOW
        add_event(session, listing, "new_offer", at=NOW)
    session.flush()
    all_ids = [item.id for item in feed(session).items]
    first = feed(session, limit=3)
    add_event(session, listing, "out_of_stock", at=NOW + timedelta(seconds=1))
    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor:
        page = feed(session, limit=3, cursor=cursor)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
    assert seen == all_ids
    assert len(set(seen)) == 8
    assert all_ids == sorted(all_ids, reverse=True)


@pytest.mark.parametrize("cursor", ["bad", "", "W10", "bnVsbA", "MTIz"])
def test_invalid_cursor(session, cursor):
    with pytest.raises(ValueError, match="INVALID_ACTIVITY_CURSOR"):
        feed(session, cursor=cursor)


def test_owner_shared_admin_visibility(session):
    own, _ = product_listing(session)
    shared, _ = product_listing(session, owner=None)
    private, private_listing = product_listing(session, owner=uuid4())
    add_event(session, private_listing, "offer_removed", before={"price": "100"})
    assert {item.product.id for item in feed(session).items} == {own.id, shared.id}
    assert {item.product.id for item in feed(session, viewer=ADMIN).items} == {
        own.id,
        shared.id,
        private.id,
    }


@pytest.mark.parametrize("optimized", [True, False])
def test_primary_catalog_image_and_constant_queries(session, optimized):
    product, listing = product_listing(session)
    image = ProductImageRepository(session).create(
        product_id=product.id,
        source_url="https://example.test/source.jpg",
        position=0,
        is_main=True,
        original_status="ready",
        original_drive_file_id="test-original",
        optimized_status="ready" if optimized else "processing",
        optimized_drive_file_id="test-avif" if optimized else None,
    )
    for i in range(20):
        add_event(session, listing, "price_changed", at=NOW + timedelta(seconds=i))
    session.commit()
    session.expire_all()
    queries = []

    def count(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        page = feed(session, limit=30)
    finally:
        event.remove(bind, "before_cursor_execute", count)
    assert len(queries) == 2
    assert len(page.items) == 21
    expected = "optimized" if optimized else "original"
    assert all(
        item.product.primary_image_url
        == f"/products/{product.id}/images/{image.id}/content?variant={expected}"
        for item in page.items
    )


def test_store_metadata_overrides_registered_label(session):
    from scout_api.modules.matching.models import StoreMetadata

    _, listing = product_listing(session)
    session.add(StoreMetadata(store_key="kabum", display_name="Nome administrado"))
    add_event(session, listing, "new_offer")
    session.flush()
    assert feed(session).items[0].store.display_name == "Nome administrado"


def test_endpoint_private_cursor_and_read_permission(session, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()
    app.dependency_overrides[get_activity_service] = lambda: ActivityService(session)
    try:
        with TestClient(app) as client:
            assert client.get("/products/activity").status_code == 401
            app.dependency_overrides[require_authenticated_user] = lambda: OWNER
            response = client.get("/products/activity")
            assert response.status_code == 200
            assert response.json() == {"items": [], "next_cursor": None}
            assert client.get("/products/activity?cursor=bad").status_code == 422
            assert client.get("/products/activity?limit=51").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_http_database_error_is_sanitized(monkeypatch):
    from sqlalchemy.exc import OperationalError

    class FailingService:
        def list_recent(self, **_kwargs):
            raise OperationalError("SECRET SQL", {}, Exception("SECRET PASSWORD"))

    app.dependency_overrides[get_activity_service] = FailingService
    try:
        with TestClient(app) as client:
            result = client.get("/products/activity")
            assert result.status_code == 503
            assert "SECRET" not in result.text
    finally:
        app.dependency_overrides.clear()


def test_regular_price_changed_when_pix_stayed_the_same(session):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        "price_changed",
        before={"price": "100", "pix_price": "90", "currency": "BRL"},
        after={"price": "120", "pix_price": "90", "currency": "BRL"},
    )
    offer = feed(session).items[0].offer
    assert offer.old_price == Decimal("100") and offer.new_price == Decimal("120")
    assert offer.old_price_kind == offer.new_price_kind == "regular"
    assert offer.price_direction == "increased"


def test_sparse_legacy_event_uses_historical_snapshot_not_latest(session):
    from scout_api.modules.crawler.models.product import ProductOffer

    _, listing = product_listing(session)
    repo = MatchingRepository(session)

    def snapshot(price, at):
        repo.append_snapshot_from_offer(
            listing,
            ProductOffer(
                store="kabum",
                country="BR",
                product_id=listing.product_id,
                url=listing.url,
                canonical_url=listing.canonical_url,
                price=Decimal(price),
                currency="BRL",
                scraped_at=at,
            ),
        )

    snapshot("99", NOW - timedelta(seconds=1))
    add_event(session, listing, "offer_created", after={"url": listing.url}, at=NOW)
    snapshot("999", NOW + timedelta(seconds=1))
    assert feed(session).items[0].offer.new_price == Decimal("99")


def test_legacy_refresh_promotion_normalized_without_wall_clock_conflation(session):
    _, listing = product_listing(session)
    before = {
        "scraped_at": (NOW - timedelta(hours=1)).isoformat(),
        "price": "100",
        "currency": "BRL",
    }
    add_event(
        session,
        listing,
        "price_changed",
        before=before,
        after={"scraped_at": NOW.isoformat(), "price": "90", "currency": "BRL"},
    )
    add_event(
        session,
        listing,
        "promotion_activated",
        before=before,
        after={
            "promotion_status": "active",
            "promotion_payload": {"promotion_price": "90"},
        },
        at=NOW + timedelta(milliseconds=20),
    )
    assert [item.type for item in feed(session).items] == [
        "promotion_activated",
        "product_added",
    ]
    # A clock event is separate even if it observes the same previous snapshot.
    _, other = product_listing(session)
    add_event(
        session,
        other,
        "price_changed",
        before=before,
        after={"scraped_at": NOW.isoformat(), "price": "90", "currency": "BRL"},
    )
    add_event(
        session,
        other,
        "promotion_expired",
        before=before,
        after={"source": "wall_clock", "status": "expired"},
        at=NOW + timedelta(seconds=1),
    )
    assert any(
        item.type == "price_changed" and item.offer.listing_id == other.id
        for item in feed(session).items
    )


@pytest.mark.parametrize("country,key", [("BR", "amazon_br"), ("US", "amazon_us")])
def test_regional_store_alias_uses_registered_metadata(session, country, key):
    from scout_api.modules.crawler.stores import STORE_CONFIGS
    from scout_api.modules.matching.models import StoreMetadata

    _, listing = product_listing(session, store="amazon")
    listing.country = country
    add_event(session, listing, "new_offer")
    session.flush()
    item = feed(session).items[0]
    assert item.store.key == key
    assert item.store.display_name == STORE_CONFIGS[key].label
    session.add(StoreMetadata(store_key=key, display_name="Nome regional administrado"))
    session.flush()
    assert feed(session).items[0].store.display_name == "Nome regional administrado"


def test_equal_commercial_price_omitted_before_pagination(session):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        "price_changed",
        before={"price": "171", "currency": "USD"},
        after={"price": "171.00", "currency": "USD"},
    )
    page = feed(session, limit=1)
    assert page.items[0].type == "product_added"
    assert page.next_cursor is None
    assert len(list(session.scalars(select(OfferEvent)))) == 1


def test_malformed_price_does_not_break_activity_query(session):
    _, listing = product_listing(session)
    add_event(
        session,
        listing,
        "price_changed",
        before={"price": "unknown", "currency": "BRL"},
        after={"price": "100", "currency": "BRL"},
    )
    item = feed(session).items[0]
    assert item.type == "price_changed"
    assert item.offer.old_price is None
