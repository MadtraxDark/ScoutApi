from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.core.config import Settings
from scout_api.core.database import Base
from scout_api.modules.favorites.domain import (
    effective_offer_price,
    evaluate_price_drop,
)
from scout_api.modules.favorites.service import FavoritesService, record_price_drop
from scout_api.modules.favorites.worker import process_delivery
from scout_api.modules.matching import models as matching_models  # noqa: F401
from scout_api.modules.matching.models import (
    CanonicalProduct,
    EmailDelivery,
    OfferEvent,
    ProductFavorite,
    StoreListing,
    UserNotification,
    UserNotificationPreference,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def _offer(
    price: str | None,
    *,
    pix: str | None = None,
    available: bool = True,
    currency: str = "BRL",
) -> dict[str, object]:
    return {
        "price": price,
        "pix_price": pix,
        "available": available,
        "currency": currency,
    }


def test_effective_price_prefers_pix_and_falls_back_to_price() -> None:
    assert effective_offer_price(_offer("3899", pix="3599")) == Decimal("3599")
    assert effective_offer_price(_offer("3899")) == Decimal("3899")
    assert effective_offer_price(_offer("3899", pix="0")) == Decimal("3899")
    assert effective_offer_price({"price": None, "original_price": "4099"}) == Decimal(
        "4099"
    )


@pytest.mark.parametrize(
    ("event_type", "before", "after", "expected"),
    [
        ("price_changed", _offer("100"), _offer("90"), Decimal("100")),
        ("price_changed", _offer("100"), _offer("110"), None),
        ("price_changed", _offer("100"), _offer("100"), None),
        ("price_changed", _offer("100"), _offer(None), None),
        ("offer_removed", _offer("100"), _offer(None), None),
        ("out_of_stock", _offer("100"), _offer(None, available=False), None),
        ("scrape_failed", _offer("100"), _offer(None), None),
        (
            "price_changed",
            _offer("1000", currency="USD"),
            _offer("950", currency="USD"),
            Decimal("1000"),
        ),
        (
            "price_changed",
            _offer("1000", currency="USD"),
            _offer("950", currency="BRL"),
            None,
        ),
    ],
)
def test_evaluate_price_drop_requires_real_same_currency_decrease(
    event_type, before, after, expected
) -> None:
    result = evaluate_price_drop(event_type, before, after)
    assert (result.old_price if result else None) == expected


def test_favorite_put_is_idempotent_and_scoped_by_user(session: Session) -> None:
    product = CanonicalProduct(title="Produto")
    session.add(product)
    session.flush()
    service = FavoritesService(session)
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    first = service.put(user_id=user_a, email="a@example.com", product_id=product.id)
    again = service.put(user_id=user_a, email="a@example.com", product_id=product.id)
    other = service.put(user_id=user_b, email="b@example.com", product_id=product.id)
    assert first.id == again.id
    assert other.id != first.id
    assert (
        session.scalar(
            select(UserNotificationPreference).where(
                UserNotificationPreference.user_id == user_a
            )
        ).email
        == "a@example.com"
    )
    service.remove(user_id=user_a, product_id=product.id)
    service.remove(user_id=user_a, product_id=product.id)
    assert (
        session.scalar(select(ProductFavorite).where(ProductFavorite.user_id == user_a))
        is None
    )
    assert (
        session.scalar(select(ProductFavorite).where(ProductFavorite.user_id == user_b))
        is not None
    )


def test_price_drop_creates_one_notification_and_email_for_each_enabled_favorite(
    session: Session,
) -> None:
    product = CanonicalProduct(title="Produto canônico")
    session.add(product)
    session.flush()
    listing = StoreListing(
        canonical_product_id=product.id,
        store="nis sei",
        country="BR",
        product_id="sku",
        url="https://store.example/p",
        canonical_url="https://store.example/p",
        match_decision="auto_match",
        confidence=Decimal("0.99"),
        status="active",
    )
    session.add(listing)
    session.flush()
    user_a, user_b, user_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    favorite_a = ProductFavorite(
        user_id=user_a, product_id=product.id, notify_price_drop=True
    )
    favorite_b = ProductFavorite(
        user_id=user_b, product_id=product.id, notify_price_drop=True
    )
    favorite_c = ProductFavorite(
        user_id=user_c, product_id=product.id, notify_price_drop=False
    )
    session.add_all([favorite_a, favorite_b, favorite_c])
    session.flush()
    session.add_all(
        [
            UserNotificationPreference(
                user_id=user_a, email="a@example.com", email_enabled=True
            ),
            UserNotificationPreference(
                user_id=user_b, email="b@example.com", email_enabled=True
            ),
            UserNotificationPreference(
                user_id=user_c, email="c@example.com", email_enabled=True
            ),
        ]
    )
    event = OfferEvent(
        listing_id=listing.id,
        event_type="price_changed",
        before=_offer("100"),
        after=_offer("90"),
    )
    session.add(event)
    session.flush()
    detected_at = datetime.now(UTC)
    before = _offer("100")
    after = {**_offer("90"), "scraped_at": detected_at.isoformat()}
    assert (
        record_price_drop(
            session,
            listing=listing,
            event_id=event.id,
            event_type="price_changed",
            detected_at=detected_at,
            before=before,
            after=after,
        )
        == 2
    )
    assert (
        record_price_drop(
            session,
            listing=listing,
            event_id=event.id,
            event_type="price_changed",
            detected_at=detected_at,
            before=before,
            after=after,
        )
        == 0
    )
    assert (
        len(
            session.scalars(
                select(UserNotification).where(UserNotification.type == "price_drop")
            ).all()
        )
        == 2
    )
    assert len(session.scalars(select(EmailDelivery)).all()) == 2


class FakeTransport:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.sent: list[dict[str, str]] = []

    def send(self, **message: str) -> None:
        if self.failure:
            raise self.failure
        self.sent.append(message)


def _delivery() -> EmailDelivery:
    return EmailDelivery(
        id=uuid.uuid4(),
        notification_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        favorite_id=uuid.uuid4(),
        offer_event_id=uuid.uuid4(),
        recipient="buyer@example.com",
        payload={
            "product_title": "Produto",
            "product_id": str(uuid.uuid4()),
            "store": "Loja",
            "store_url": "https://store.example/p",
            "currency": "BRL",
            "old_price": "100",
            "new_price": "90",
            "difference": "10",
            "percent_drop": "10.00",
            "old_price_display": "R$ 100,00",
            "new_price_display": "R$ 90,00",
            "difference_display": "R$ 10,00",
        },
        status="sending",
        attempts=1,
    )


def test_worker_sends_multipart_email_with_stable_message_id() -> None:
    delivery = _delivery()
    fake = FakeTransport()
    cfg = Settings(
        _env_file=None,
        email_enabled=True,
        email_from_address="alerts@example.com",
        public_app_url="https://pricescout.example",
    )
    assert process_delivery(Session(), delivery, settings=cfg, transport=fake) == "sent"
    assert fake.sent[0]["message_id"] == delivery.message_id
    assert "https://pricescout.example/admin/produtos/" in fake.sent[0]["text_body"]
    assert "<a href=" in fake.sent[0]["html_body"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError(), "retry"),
        (smtplib.SMTPDataError(421, b"temporary"), "retry"),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "failed"),
        (smtplib.SMTPDataError(550, b"rejected"), "failed"),
    ],
)
def test_worker_classifies_transient_and_permanent_failures(
    failure: Exception, expected: str
) -> None:
    delivery = _delivery()
    cfg = Settings(
        _env_file=None, email_enabled=True, email_from_address="alerts@example.com"
    )
    result = process_delivery(
        Session(), delivery, settings=cfg, transport=FakeTransport(failure)
    )
    assert result == expected
    assert delivery.last_error != str(failure)
