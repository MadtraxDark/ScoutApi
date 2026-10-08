"""Semantic activity projection; no localized phrases or raw history exposed."""

import base64
import binascii
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.images.service import primary_display_url, to_image_view
from scout_api.modules.matching.activity_repository import (
    ActivityRepository,
    ActivityRow,
)
from scout_api.modules.matching.activity_schemas import (
    ActivityItem,
    ActivityListResponse,
    ActivityOffer,
    ActivityProduct,
    ActivityStore,
    ActivityType,
)
from scout_api.modules.matching.activity_search import ActivityFilters, search_terms

PriceKind = Literal["pix", "regular", "promotion"]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    try:
        data = json.loads(
            base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
        )
        if not isinstance(data, list) or len(data) != 3 or data[0] != 1:
            raise ValueError
        timestamp = datetime.fromisoformat(data[1])
        key = data[2]
        source, identifier = key.split(":", 1)
        if timestamp.tzinfo is None or source not in {"product", "offer"}:
            raise ValueError
        UUID(identifier)
        return _aware(timestamp), key
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error) as exc:
        raise ValueError("INVALID_ACTIVITY_CURSOR") from exc


def _encode_cursor(row: ActivityRow) -> str:
    data = json.dumps([1, _aware(row.occurred_at).isoformat(), row.id])
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _amount(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _promotion(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("promotion_payload")
    if not isinstance(value, dict):
        metadata = payload.get("metadata")
        value = metadata.get("promotion") if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) else {}


def _price(
    payload: dict[str, Any],
    *,
    promotional: bool = False,
) -> tuple[Decimal | None, PriceKind | None]:
    if promotional:
        promo = _promotion(payload)
        for value in (
            payload.get("promotion_price"),
            promo.get("promotion_price"),
            promo.get("price"),
        ):
            amount = _amount(value)
            if amount is not None:
                return amount, "promotion"
    for key, kind in (
        ("pix_price", "pix"),
        ("price", "regular"),
        ("original_price", "regular"),
    ):
        amount = _amount(payload.get(key))
        if amount is not None:
            return amount, cast(PriceKind, kind)
    return None, None


def _currency(payload: dict[str, Any]) -> str | None:
    value = payload.get("currency")
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _offer(row: ActivityRow) -> ActivityOffer:
    assert row.event is not None and row.listing_id is not None
    kind = row.event.event_type
    before = row.event.before or {}
    after = row.event.after or {}
    snapshot = row.snapshot or {}
    promotional = kind.startswith("promotion_")
    # before/after are authoritative. Snapshot fallback only for legacy sparse
    # creation/promotion events, not an old-price reconstruction from current state.
    if kind in {"offer_created", "new_offer"}:
        after = {**snapshot, **after}
    elif promotional:
        if kind == "promotion_expired":
            before = {**snapshot, **before}
        else:
            after = {**snapshot, **after}
    old_price, old_kind = _price(before, promotional=promotional)
    new_price, new_kind = _price(after, promotional=promotional)
    # If Pix did not change but the regular/card price did, describe the
    # commercial field that actually changed instead of "90 -> 90".
    if kind == "price_changed" and old_price == new_price:
        old_regular = _amount(before.get("price"))
        new_regular = _amount(after.get("price"))
        if (
            old_regular is not None
            and new_regular is not None
            and old_regular != new_regular
        ):
            old_price, new_price = old_regular, new_regular
            old_kind, new_kind = "regular", "regular"
    old_currency = _currency(before)
    currency = _currency(after)
    if promotional:
        old_currency = old_currency or _currency(snapshot)
        currency = currency or _currency(snapshot)
    if kind in {"offer_removed", "out_of_stock", "promotion_expired"}:
        new_price, new_kind = None, None
        currency = currency or old_currency
    direction: Literal["decreased", "increased", "unchanged"] | None = None
    if (
        kind == "price_changed"
        and old_price is not None
        and new_price is not None
        and old_currency == currency
        and currency is not None
    ):
        direction = (
            "decreased"
            if new_price < old_price
            else "increased"
            if new_price > old_price
            else "unchanged"
        )
    expiry = after.get("promotion_expires_at") or _promotion(after).get("expires_at")
    try:
        expires_at = _aware(datetime.fromisoformat(expiry)) if expiry else None
    except (ValueError, TypeError):
        expires_at = None
    return ActivityOffer(
        listing_id=row.listing_id,
        old_currency=old_currency,
        currency=currency,
        old_price=old_price,
        new_price=new_price,
        old_price_kind=old_kind,
        new_price_kind=new_kind,
        price_direction=direction,
        old_availability=before.get("availability"),
        availability=after.get("availability"),
        available=after.get("available"),
        promotion_expires_at=expires_at,
        old_seller=before.get("seller")
        if isinstance(before.get("seller"), str)
        else None,
        seller=after.get("seller") if isinstance(after.get("seller"), str) else None,
    )


class ActivityService:
    def __init__(self, session: Session) -> None:
        self._repository = ActivityRepository(session)
        self._images = ProductImageRepository(session)

    def list_changes(
        self,
        *,
        viewer: AuthenticatedPrincipal,
        limit: int = 25,
        cursor: str | None = None,
        filters: ActivityFilters | None = None,
    ) -> ActivityListResponse:
        filters = filters or ActivityFilters()
        search_terms(filters.q)
        if any(
            value and value.tzinfo is None
            for value in (filters.started_at, filters.ended_at)
        ) or (
            filters.started_at
            and filters.ended_at
            and filters.started_at >= filters.ended_at
        ):
            raise ValueError("INVALID_CHANGE_FILTERS")
        return self.list_recent(
            viewer=viewer, limit=limit, cursor=cursor, history=True, filters=filters
        )

    def list_recent(
        self,
        *,
        viewer: AuthenticatedPrincipal,
        limit: int = 15,
        cursor: str | None = None,
        history: bool = False,
        filters: ActivityFilters | None = None,
    ) -> ActivityListResponse:
        if not 1 <= limit <= 50:
            raise ValueError("INVALID_ACTIVITY_LIMIT")
        started = time.perf_counter()
        boundary = decode_cursor(cursor)
        rows = self._repository.list_recent(
            viewer_id=viewer.id,
            is_admin=viewer.role == UserRole.ADMIN,
            limit=limit,
            boundary=boundary,
            history=history,
            filters=filters,
        )
        queried = time.perf_counter()
        page = rows[:limit]
        images = self._images.list_for_products(list({row.product.id for row in page}))
        image_urls = {
            product_id: primary_display_url([to_image_view(image) for image in gallery])
            for product_id, gallery in images.items()
        }
        items: list[ActivityItem] = []
        for row in page:
            event_type = row.event.event_type if row.event else "product_added"
            event_type = "new_offer" if event_type == "offer_created" else event_type
            if event_type not in {
                "product_added",
                "new_offer",
                "price_changed",
                "availability_changed",
                "offer_removed",
                "out_of_stock",
                "promotion_activated",
                "promotion_expired",
                "promotion_updated",
                "seller_changed",
                "gtin_learned",
                "scrape_failed",
                "unchanged",
            }:
                event_type = "other"
            config = STORE_CONFIGS.get(row.store or "")
            items.append(
                ActivityItem(
                    id=row.id,
                    type=cast(ActivityType, event_type),
                    occurred_at=_aware(row.occurred_at),
                    product=ActivityProduct(
                        id=row.product.id,
                        title=row.product.title,
                        brand=row.product.brand,
                        model=row.product.model,
                        primary_image_url=image_urls.get(row.product.id),
                    ),
                    store=ActivityStore(
                        key=row.store,
                        display_name=row.store_display_name
                        or (config.label if config else "Loja"),
                    )
                    if row.store
                    else None,
                    offer=_offer(row) if row.event else None,
                )
            )
        logging.getLogger(__name__).info(
            "catalog_activity query_ms=%.1f projection_ms=%.1f items=%d",
            (queried - started) * 1000,
            (time.perf_counter() - queried) * 1000,
            len(items),
        )
        return ActivityListResponse(
            items=items,
            next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
        )
