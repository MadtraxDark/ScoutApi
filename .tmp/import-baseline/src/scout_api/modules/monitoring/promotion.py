"""Promotion domain helpers — current state on listing; history via events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.matching.models import StoreListing
from scout_api.modules.monitoring.schedule import ensure_aware, utcnow


@dataclass(frozen=True)
class PromotionObservation:
    """Structured timed promotion extracted from a scrape or HTML."""

    status: str  # none | active | expired
    promotion_type: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    promotion_price: Decimal | None = None
    original_price: Decimal | None = None
    conditions: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    timezone: str | None = None
    sku: str | None = None
    product_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "type": self.promotion_type,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "promotion_price": (
                format(self.promotion_price, "f")
                if self.promotion_price is not None
                else None
            ),
            "original_price": (
                format(self.original_price, "f")
                if self.original_price is not None
                else None
            ),
            "conditions": self.conditions,
            "source": self.source,
            "timezone": self.timezone,
            "sku": self.sku,
            "product_id": self.product_id,
            **self.payload,
        }


def parse_aware_datetime(
    value: object,
    *,
    source_timezone: str | None = None,
) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, (int, float)):
        # Unix seconds (Shopee flash_sale often uses this).
        ts = float(value)
        if ts > 1_000_000_000_000:  # ms
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Terabyte jQuery countdown: ``2026/09/28 10:00:59`` (store local).
        if "/" in text and " " in text and "T" not in text:
            try:
                naive = datetime.strptime(text, "%Y/%m/%d %H:%M:%S")
            except ValueError:
                try:
                    naive = datetime.strptime(text, "%Y/%m/%d %H:%M")
                except ValueError:
                    naive = None
            if naive is not None:
                tz_name = source_timezone or "America/Sao_Paulo"
                try:
                    return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
                except Exception:  # noqa: BLE001
                    return naive.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            tz_name = source_timezone or "UTC"
            try:
                return parsed.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
            except Exception:  # noqa: BLE001
                return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def observation_from_metadata(
    metadata: dict[str, Any] | None,
    *,
    offer: ProductOffer | None = None,
) -> PromotionObservation | None:
    """Read structured promotion from ``ProductOffer.metadata`` when present."""
    if not metadata:
        return None
    promo = metadata.get("promotion")
    if not isinstance(promo, dict):
        # AliExpress uses ``promotions`` (conditions, not always timed).
        alt = metadata.get("timed_promotion")
        promo = alt if isinstance(alt, dict) else None
    if not isinstance(promo, dict):
        return None

    tz = promo.get("timezone") or promo.get("source_timezone")
    expires = parse_aware_datetime(
        promo.get("expires_at") or promo.get("end_time") or promo.get("finish_date"),
        source_timezone=str(tz) if tz else None,
    )
    starts = parse_aware_datetime(
        promo.get("starts_at") or promo.get("start_time") or promo.get("start_date"),
        source_timezone=str(tz) if tz else None,
    )
    if expires is None and starts is None:
        return None

    now = utcnow()
    status = "active"
    if expires is not None and expires <= now:
        status = "expired"

    price = offer.price if offer is not None else None
    original = offer.original_price if offer is not None else None
    raw_price = promo.get("promotion_price") or promo.get("price")
    raw_original = promo.get("original_price")
    try:
        if raw_price is not None:
            price = Decimal(str(raw_price))
        if raw_original is not None:
            original = Decimal(str(raw_original))
    except Exception:  # noqa: BLE001
        pass

    conditions = promo.get("conditions")
    if not isinstance(conditions, dict):
        conditions = {}
    for key in (
        "pix",
        "coupon",
        "prime",
        "choice",
        "app",
        "membership",
        "seller_coupon",
    ):
        if key in promo and key not in conditions:
            conditions[key] = promo[key]

    return PromotionObservation(
        status=status,
        promotion_type=str(promo.get("type") or promo.get("promotion_type") or "timed")
        if promo.get("type") or promo.get("promotion_type")
        else "timed",
        starts_at=starts,
        expires_at=expires,
        promotion_price=price,
        original_price=original,
        conditions=conditions,
        source=str(promo.get("source") or "metadata.promotion"),
        timezone=str(tz) if tz else None,
        sku=str(promo.get("sku"))
        if promo.get("sku")
        else (offer.sku if offer else None),
        product_id=(
            str(promo.get("product_id"))
            if promo.get("product_id")
            else (offer.product_id if offer else None)
        ),
        payload={k: v for k, v in promo.items() if k not in {"conditions"}},
    )


def apply_promotion_observation(
    listing: StoreListing,
    observation: PromotionObservation | None,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Update listing current promotion columns. Returns event type names."""
    moment = ensure_aware(now or utcnow())
    events: list[str] = []
    previous_status = listing.promotion_status
    previous_expires = listing.promotion_expires_at

    if observation is None:
        # Do not invent expiry; keep prior timed promo until store says otherwise
        # or wall-clock expiry handler runs.
        if (
            listing.promotion_status == "active"
            and listing.promotion_expires_at is not None
            and ensure_aware(listing.promotion_expires_at) <= moment
        ):
            listing.promotion_status = "expired"
            events.append("promotion_expired")
        return events

    # SKU guard: ignore timer from a different variant when both sides known.
    if (
        observation.sku
        and listing.sku
        and observation.sku.strip()
        and listing.sku.strip()
        and observation.sku.strip() != listing.sku.strip()
    ):
        return events
    if (
        observation.product_id
        and listing.product_id
        and observation.product_id != listing.product_id
    ):
        return events

    listing.promotion_type = observation.promotion_type
    listing.promotion_starts_at = observation.starts_at
    listing.promotion_expires_at = observation.expires_at
    listing.promotion_price = observation.promotion_price
    listing.promotion_original_price = observation.original_price
    listing.promotion_conditions = dict(observation.conditions or {})
    listing.promotion_source = observation.source
    listing.promotion_timezone = observation.timezone
    listing.promotion_payload = observation.as_payload()

    status = observation.status
    if (
        observation.expires_at is not None
        and ensure_aware(observation.expires_at) <= moment
    ):
        status = "expired"
    listing.promotion_status = status

    if previous_status != "active" and status == "active":
        events.append("promotion_activated")
    if previous_status == "active" and status == "expired":
        events.append("promotion_expired")
    elif (
        previous_status == "active"
        and status == "active"
        and previous_expires != observation.expires_at
        and observation.expires_at is not None
    ):
        events.append("promotion_updated")
    return events


def expire_due_promotions(
    listing: StoreListing, *, now: datetime | None = None
) -> bool:
    """Mark active promotion expired when wall-clock passes ``expires_at``."""
    moment = ensure_aware(now or utcnow())
    if listing.promotion_status != "active":
        return False
    if listing.promotion_expires_at is None:
        return False
    if ensure_aware(listing.promotion_expires_at) > moment:
        return False
    listing.promotion_status = "expired"
    return True


def is_promotion_commercially_active(
    listing: StoreListing, *, now: datetime | None = None
) -> bool:
    """Backend authority for whether a promo price may be presented as current."""
    moment = ensure_aware(now or utcnow())
    if listing.promotion_status != "active":
        return False
    if listing.promotion_expires_at is None:
        return False
    return ensure_aware(listing.promotion_expires_at) > moment
