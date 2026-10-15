"""Persistence and fan-out for favorites and price drop events."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.favorites.domain import evaluate_price_drop
from scout_api.modules.matching.models import (
    CanonicalProduct,
    EmailDelivery,
    ProductFavorite,
    StoreListing,
    UserNotification,
    UserNotificationPreference,
)

logger = logging.getLogger(__name__)


class FavoritesService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def put(
        self, *, user_id: uuid.UUID, email: str | None, product_id: uuid.UUID
    ) -> ProductFavorite:
        if self.session.get(CanonicalProduct, product_id) is None:
            raise LookupError("product_not_found")
        now = datetime.now(UTC)
        favorite = self.session.scalars(
            select(ProductFavorite).where(
                ProductFavorite.user_id == user_id,
                ProductFavorite.product_id == product_id,
            )
        ).first()
        if favorite is None:
            favorite = ProductFavorite(
                user_id=user_id,
                product_id=product_id,
                notify_price_drop=True,
                created_at=now,
                updated_at=now,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(favorite)
                    self.session.flush()
            except IntegrityError:
                favorite = self.session.scalars(
                    select(ProductFavorite).where(
                        ProductFavorite.user_id == user_id,
                        ProductFavorite.product_id == product_id,
                    )
                ).one()
        pref = self.session.get(UserNotificationPreference, user_id)
        if pref is None:
            self.session.add(
                UserNotificationPreference(
                    user_id=user_id,
                    email=email,
                    email_enabled=email is not None,
                    updated_at=now,
                )
            )
        else:
            pref.email = email
            pref.updated_at = now
        self.session.flush()
        return favorite

    def sync_email(self, *, user_id: uuid.UUID, email: str | None) -> None:
        now = datetime.now(UTC)
        preference = self.session.get(UserNotificationPreference, user_id)
        if preference is None:
            self.session.add(
                UserNotificationPreference(
                    user_id=user_id,
                    email=email,
                    email_enabled=email is not None,
                    updated_at=now,
                )
            )
        elif preference.email != email:
            previously_missing = preference.email is None
            preference.email = email
            if email and previously_missing:
                preference.email_enabled = True
            preference.updated_at = now
        self.session.flush()

    def remove(self, *, user_id: uuid.UUID, product_id: uuid.UUID) -> None:
        favorite = self.session.scalars(
            select(ProductFavorite).where(
                ProductFavorite.user_id == user_id,
                ProductFavorite.product_id == product_id,
            )
        ).first()
        if favorite is None:
            return
        self.session.query(EmailDelivery).filter(
            EmailDelivery.favorite_id == favorite.id,
            EmailDelivery.status.in_(("pending", "retry")),
        ).update(
            {
                EmailDelivery.status: "cancelled",
                EmailDelivery.updated_at: datetime.now(UTC),
            },
            synchronize_session=False,
        )
        self.session.delete(favorite)
        self.session.flush()

    def set_alerts(
        self, *, user_id: uuid.UUID, product_id: uuid.UUID, enabled: bool
    ) -> ProductFavorite | None:
        favorite = self.session.scalars(
            select(ProductFavorite).where(
                ProductFavorite.user_id == user_id,
                ProductFavorite.product_id == product_id,
            )
        ).first()
        if favorite is not None:
            favorite.notify_price_drop = enabled
            favorite.updated_at = datetime.now(UTC)
            if not enabled:
                self.session.query(EmailDelivery).filter(
                    EmailDelivery.favorite_id == favorite.id,
                    EmailDelivery.status.in_(("pending", "retry")),
                ).update(
                    {
                        EmailDelivery.status: "cancelled",
                        EmailDelivery.updated_at: datetime.now(UTC),
                    },
                    synchronize_session=False,
                )
        return favorite


def record_price_drop(
    session: Session,
    *,
    listing: StoreListing,
    event_id: uuid.UUID,
    event_type: str,
    detected_at: datetime,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> int:
    """Persist internal notification and email outbox rows in caller transaction."""
    drop = evaluate_price_drop(event_type, before, after)
    if drop is None:
        return 0
    product = session.get(CanonicalProduct, listing.canonical_product_id)
    if product is None:
        return 0
    users = session.execute(
        select(ProductFavorite, UserNotificationPreference)
        .join(
            UserNotificationPreference,
            UserNotificationPreference.user_id == ProductFavorite.user_id,
            isouter=True,
        )
        .where(
            ProductFavorite.product_id == product.id,
            ProductFavorite.notify_price_drop.is_(True),
            ProductFavorite.created_at <= detected_at,
        )
    ).all()
    created = 0
    deliveries_created = 0
    now = datetime.now(UTC)
    old, new = (
        format_money(drop.old_price, drop.currency),
        format_money(drop.new_price, drop.currency),
    )
    diff = format_money(drop.difference, drop.currency)
    percent = drop.percent.quantize(Decimal("0.01"))
    store_name = (
        STORE_CONFIGS[listing.store].label
        if listing.store in STORE_CONFIGS
        else listing.store
    )
    message = f"{product.title} caiu de {old} para {new} na {store_name}."
    metadata = {
        "listing_id": str(listing.id),
        "offer_event_id": str(event_id),
        "store": listing.store,
        "store_name": store_name,
        "old_price": str(drop.old_price),
        "new_price": str(drop.new_price),
        "currency": drop.currency,
        "difference": str(drop.difference),
        "percent_drop": str(percent),
    }
    for favorite, preference in users:
        existing = session.scalar(
            select(UserNotification.id).where(
                UserNotification.favorite_id == favorite.id,
                UserNotification.offer_event_id == event_id,
            )
        )
        if existing is not None:
            continue
        notification = UserNotification(
            user_id=favorite.user_id,
            type="price_drop",
            product_id=product.id,
            favorite_id=favorite.id,
            offer_event_id=event_id,
            title="Preço caiu",
            message=message,
            metadata_json=metadata,
            created_at=now,
        )
        try:
            with session.begin_nested():
                session.add(notification)
                session.flush([notification])
        except IntegrityError:
            continue
        created += 1
        if preference is None or not preference.email_enabled or not preference.email:
            continue
        payload = {
            "product_title": product.title,
            "product_id": str(product.id),
            "store": store_name,
            "store_url": listing.url,
            "currency": drop.currency,
            "old_price": str(drop.old_price),
            "new_price": str(drop.new_price),
            "difference": str(drop.difference),
            "percent_drop": str(percent),
            "old_price_display": old,
            "new_price_display": new,
            "difference_display": diff,
        }
        session.add(
            EmailDelivery(
                notification_id=notification.id,
                user_id=favorite.user_id,
                favorite_id=favorite.id,
                offer_event_id=event_id,
                recipient=preference.email,
                payload=payload,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        deliveries_created += 1
    session.flush()
    if created:
        logger.info(
            "price_alert_fanout_created",
            extra={
                "offer_event_id": str(event_id),
                "product_id": str(product.id),
                "price_alerts_detected": created,
                "email_delivery_created": deliveries_created,
            },
        )
    return created


def format_money(amount: Decimal, currency: str) -> str:
    value = f"{amount:,.2f}"
    if currency == "BRL":
        return "R$ " + value.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{currency} {value}"
