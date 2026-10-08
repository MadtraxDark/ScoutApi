"""Bounded activity read queries over existing catalog/history tables."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Numeric,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import Uuid

from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.activity_search import (
    ACCENTED,
    PLAIN,
    ActivityFilters,
    search_terms,
)
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferEvent,
    OfferSnapshot,
    StoreListing,
    StoreMetadata,
)

FEED_EVENT_TYPES = (
    "offer_created",
    "new_offer",
    "price_changed",
    "availability_changed",
    "offer_removed",
    "out_of_stock",
    "promotion_activated",
    "promotion_expired",
    "promotion_updated",
)


@dataclass(frozen=True)
class ActivityRow:
    id: str
    occurred_at: datetime
    product: CanonicalProduct
    event: OfferEvent | None
    listing_id: UUID | None
    store: str | None
    store_display_name: str | None
    snapshot: dict[str, Any] | None


class ActivityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_recent(
        self,
        *,
        viewer_id: UUID,
        is_admin: bool,
        limit: int,
        boundary: tuple[datetime, str] | None,
        history: bool = False,
        filters: ActivityFilters | None = None,
    ) -> list[ActivityRow]:
        product_key = literal("product:") + cast(CanonicalProduct.id, String)
        event_key = literal("offer:") + cast(OfferEvent.id, String)
        products = select(
            product_key.label("id"),
            CanonicalProduct.created_at.label("occurred_at"),
            CanonicalProduct.id.label("product_id"),
            cast(null(), Uuid(as_uuid=True)).label("event_id"),
        )
        events = (
            select(
                event_key.label("id"),
                OfferEvent.detected_at.label("occurred_at"),
                StoreListing.canonical_product_id.label("product_id"),
                OfferEvent.id.label("event_id"),
            )
            .join(StoreListing, StoreListing.id == OfferEvent.listing_id)
            .join(
                CanonicalProduct,
                CanonicalProduct.id == StoreListing.canonical_product_id,
            )
        )
        if not history:
            events = events.where(OfferEvent.event_type.in_(FEED_EVENT_TYPES))

        # Historical fingerprints compared strings, so "171" -> "171.00"
        # could persist price_changed. Normalize validated amounts before paging.
        def money(payload: Any, key: str) -> Any:
            value = payload[key].as_string()
            parsed = case(
                (
                    value.regexp_match(r"^[0-9]+(\.[0-9]+)?$"),
                    cast(value, Numeric(18, 4)),
                ),
                else_=None,
            )
            return case((parsed > 0, parsed), else_=None)

        old_regular = money(OfferEvent.before, "price")
        new_regular = money(OfferEvent.after, "price")
        old_commercial = func.coalesce(
            money(OfferEvent.before, "pix_price"),
            old_regular,
            money(OfferEvent.before, "original_price"),
        )
        new_commercial = func.coalesce(
            money(OfferEvent.after, "pix_price"),
            new_regular,
            money(OfferEvent.after, "original_price"),
        )
        old_currency = func.upper(func.trim(OfferEvent.before["currency"].as_string()))
        new_currency = func.upper(func.trim(OfferEvent.after["currency"].as_string()))
        unchanged_commercial_price = and_(
            old_commercial.is_not(None),
            new_commercial.is_not(None),
            old_commercial == new_commercial,
            old_currency.is_not(None),
            old_currency != "",
            old_currency == new_currency,
            old_regular.is_not_distinct_from(new_regular),
        )
        summarized_events = events.where(
            or_(
                OfferEvent.event_type != "price_changed",
                ~unchanged_commercial_price,
            )
        )
        # Suppress redundant events only with evidence of the same observation.
        peer = aliased(OfferEvent)
        observed_at = OfferEvent.after["scraped_at"].as_string()
        peer_observed_at = peer.after["scraped_at"].as_string()
        # Legacy refresh promotion events omitted after.scraped_at but retained
        # the same previous snapshot as the price event. Wall-clock expiry is
        # a distinct observation and has no promotion_payload in its after.
        previous_observed_at = OfferEvent.before["scraped_at"].as_string()
        peer_previous_observed_at = peer.before["scraped_at"].as_string()
        legacy_promotion_observation = and_(
            peer_observed_at.is_(None),
            previous_observed_at.is_not(None),
            previous_observed_at == peer_previous_observed_at,
            peer.after["promotion_status"].as_string().is_not(None),
            peer.after["source"].as_string().is_(None),
        )
        same_observation = or_(
            peer.detected_at == OfferEvent.detected_at,
            legacy_promotion_observation,
            and_(observed_at.is_not(None), observed_at == peer_observed_at),
        )
        priority = or_(
            and_(
                OfferEvent.event_type == "availability_changed",
                peer.event_type.in_(("out_of_stock", "offer_removed")),
            ),
            and_(
                OfferEvent.event_type == "price_changed",
                peer.event_type.in_(
                    ("promotion_activated", "promotion_expired", "promotion_updated")
                ),
            ),
            and_(
                OfferEvent.event_type.in_(("new_offer", "offer_created")),
                peer.event_type.in_(("new_offer", "offer_created")),
                peer.id > OfferEvent.id,
            ),
        )
        summarized_events = summarized_events.where(
            ~select(peer.id)
            .where(peer.listing_id == OfferEvent.listing_id, same_observation, priority)
            .exists()
        )
        if not history:
            events = summarized_events
        store_key = case(
            *[
                (
                    and_(
                        StoreListing.store == config.key,
                        StoreListing.country == config.country,
                    ),
                    key,
                )
                for key, config in STORE_CONFIGS.items()
                if key != config.key
            ],
            else_=StoreListing.store,
        )
        if filters is not None:
            events = events.outerjoin(
                StoreMetadata, StoreMetadata.store_key == store_key
            )
            store_label = func.coalesce(
                StoreMetadata.display_name,
                case(
                    *[
                        (store_key == key, config.label)
                        for key, config in STORE_CONFIGS.items()
                    ],
                    else_="Loja",
                ),
            )

            def normalized(value: Any) -> Any:
                value = func.coalesce(value, "")
                if self._session.get_bind().dialect.name == "postgresql":
                    return func.lower(
                        func.translate(
                            value, ACCENTED + ACCENTED.upper(), PLAIN + PLAIN
                        )
                    )
                # SQLite has no translate and lower only folds ASCII by default.
                for accented, plain in zip(
                    ACCENTED + ACCENTED.upper(), PLAIN * 2, strict=True
                ):
                    value = func.replace(value, accented, plain)
                return func.lower(value)

            product_fields = [
                normalized(field)
                for field in (
                    CanonicalProduct.title,
                    CanonicalProduct.brand,
                    CanonicalProduct.model,
                )
            ]
            offer_fields = [
                normalized(field)
                for field in (
                    store_label,
                    OfferEvent.before["seller"].as_string(),
                    OfferEvent.after["seller"].as_string(),
                )
            ]
            for term in search_terms(filters.q):
                products = products.where(
                    or_(
                        *[
                            field.contains(term, autoescape=True)
                            for field in product_fields
                        ]
                    )
                )
                events = events.where(
                    or_(
                        *[
                            field.contains(term, autoescape=True)
                            for field in product_fields + offer_fields
                        ]
                    )
                )
            if filters.event_type:
                if filters.event_type != "product_added":
                    products = products.where(literal(False))
                if filters.event_type == "product_added":
                    events = events.where(literal(False))
                elif filters.event_type == "new_offer":
                    events = events.where(
                        OfferEvent.event_type.in_(("new_offer", "offer_created"))
                    )
                elif filters.event_type == "other":
                    events = events.where(
                        ~OfferEvent.event_type.in_(
                            (
                                *FEED_EVENT_TYPES,
                                "seller_changed",
                                "gtin_learned",
                                "scrape_failed",
                                "unchanged",
                            )
                        )
                    )
                else:
                    events = events.where(OfferEvent.event_type == filters.event_type)
            if filters.store:
                products = products.where(literal(False))
                events = events.where(store_key == filters.store)
            if filters.product_id:
                products = products.where(CanonicalProduct.id == filters.product_id)
                events = events.where(CanonicalProduct.id == filters.product_id)
            if filters.started_at:
                products = products.where(
                    CanonicalProduct.created_at >= filters.started_at
                )
                events = events.where(OfferEvent.detected_at >= filters.started_at)
            if filters.ended_at:
                products = products.where(
                    CanonicalProduct.created_at < filters.ended_at
                )
                events = events.where(OfferEvent.detected_at < filters.ended_at)
        if not is_admin:
            scope = or_(
                CanonicalProduct.owner_user_id.is_(None),
                CanonicalProduct.owner_user_id == viewer_id,
            )
            products = products.where(scope)
            events = events.where(scope)
        if boundary:
            timestamp, key = boundary
            products = products.where(
                or_(
                    CanonicalProduct.created_at < timestamp,
                    and_(CanonicalProduct.created_at == timestamp, product_key < key),
                )
            )
            events = events.where(
                or_(
                    OfferEvent.detected_at < timestamp,
                    and_(OfferEvent.detected_at == timestamp, event_key < key),
                )
            )
        # Both sources are limited before the merge; the DB never returns history.
        products_page = (
            products.order_by(CanonicalProduct.created_at.desc(), product_key.desc())
            .limit(limit + 1)
            .subquery()
        )
        events_page = (
            events.order_by(OfferEvent.detected_at.desc(), event_key.desc())
            .limit(limit + 1)
            .subquery()
        )
        feed = union_all(select(products_page), select(events_page)).subquery()
        page = (
            select(feed)
            .order_by(feed.c.occurred_at.desc(), feed.c.id.desc())
            .limit(limit + 1)
            .subquery()
        )
        # Historical fallback, never today's listing price. One SQL statement.
        historical_snapshot = (
            select(OfferSnapshot.payload)
            .where(
                OfferSnapshot.listing_id == OfferEvent.listing_id,
                OfferSnapshot.scraped_at <= OfferEvent.detected_at,
            )
            .order_by(OfferSnapshot.scraped_at.desc(), OfferSnapshot.id.desc())
            .limit(1)
            .correlate(OfferEvent)
            .scalar_subquery()
        )
        stmt = (
            select(
                page.c.id,
                page.c.occurred_at,
                CanonicalProduct,
                OfferEvent,
                StoreListing.id,
                store_key,
                StoreMetadata.display_name,
                historical_snapshot,
            )
            .join(CanonicalProduct, CanonicalProduct.id == page.c.product_id)
            .outerjoin(OfferEvent, OfferEvent.id == page.c.event_id)
            .outerjoin(StoreListing, StoreListing.id == OfferEvent.listing_id)
            .outerjoin(StoreMetadata, StoreMetadata.store_key == store_key)
            .order_by(page.c.occurred_at.desc(), page.c.id.desc())
        )
        return [ActivityRow(*row) for row in self._session.execute(stmt).all()]
