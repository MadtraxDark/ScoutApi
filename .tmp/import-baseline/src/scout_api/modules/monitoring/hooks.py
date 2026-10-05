"""Hooks that keep StoreListing monitoring schedule in sync after refresh."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from scout_api.core.config import Settings, get_settings
from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.matching.models import StoreListing
from scout_api.modules.monitoring.extractors import extract_promotion
from scout_api.modules.monitoring.promotion import (
    apply_promotion_observation,
    is_promotion_commercially_active,
    observation_from_metadata,
)
from scout_api.modules.monitoring.schedule import (
    compute_next_check_at,
    compute_next_regular_check_at,
    compute_retry_at,
    ensure_aware,
    utcnow,
)


def release_claim(listing: StoreListing) -> None:
    listing.check_worker_id = None
    listing.check_claimed_at = None
    listing.check_claim_expires_at = None


def initialize_listing_schedule(
    listing: StoreListing,
    *,
    checked_at: datetime | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> None:
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    listing.monitoring_enabled = listing.status == "active"
    if listing.status != "active":
        listing.next_check_at = None
        return
    if checked_at is not None:
        checked = ensure_aware(checked_at)
        listing.last_checked_at = checked
        listing.last_successful_check_at = checked
        listing.consecutive_failures = 0
        listing.last_check_error = None
        regular = compute_next_regular_check_at(now=checked, settings=cfg)
    else:
        # New listing without a check yet → due ASAP (startup recovery will pick it).
        regular = moment
        listing.next_regular_check_at = regular
        listing.next_check_at = moment
        return
    listing.next_regular_check_at = regular
    listing.next_check_at = compute_next_check_at(
        now=moment,
        next_regular=regular,
        promotion_expires_at=(
            listing.promotion_expires_at
            if listing.promotion_status == "active"
            else None
        ),
        settings=cfg,
    )


def mark_removed_listing(listing: StoreListing) -> None:
    listing.monitoring_enabled = False
    listing.next_check_at = None
    release_claim(listing)


def mark_check_success(
    listing: StoreListing,
    *,
    offer: ProductOffer | None = None,
    event_names: list[str] | None = None,
    html: str | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[str]:
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    listing.last_checked_at = moment
    listing.last_successful_check_at = moment
    listing.consecutive_failures = 0
    listing.last_check_error = None
    if listing.status == "removed":
        mark_removed_listing(listing)
        return []

    listing.monitoring_enabled = True
    names = list(event_names or [])
    observation = None
    if offer is not None:
        observation = extract_promotion(
            store=listing.store,
            html=html,
            offer=offer,
            metadata=offer.metadata,
        )
        if observation is None:
            observation = observation_from_metadata(offer.metadata, offer=offer)
    promo_events = apply_promotion_observation(listing, observation, now=moment)
    names.extend(promo_events)

    if "price_changed" in names:
        listing.last_price_changed_at = moment
    if "availability_changed" in names or "out_of_stock" in names:
        listing.last_availability_changed_at = moment

    regular = compute_next_regular_check_at(now=moment, settings=cfg)
    listing.next_regular_check_at = regular
    active_expires = (
        listing.promotion_expires_at
        if is_promotion_commercially_active(listing, now=moment)
        else None
    )
    listing.next_check_at = compute_next_check_at(
        now=moment,
        next_regular=regular,
        promotion_expires_at=active_expires,
        settings=cfg,
    )
    release_claim(listing)
    return promo_events


def mark_check_failure(
    listing: StoreListing,
    *,
    error: str,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> None:
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    listing.last_checked_at = moment
    listing.consecutive_failures = int(listing.consecutive_failures or 0) + 1
    listing.last_check_error = (error or "")[:2000]
    listing.next_check_at = compute_retry_at(
        consecutive_failures=listing.consecutive_failures,
        now=moment,
        settings=cfg,
    )
    release_claim(listing)


def apply_offer_metadata_promotion(
    listing: StoreListing,
    metadata: dict[str, Any] | None,
    *,
    offer: ProductOffer | None = None,
    now: datetime | None = None,
) -> list[str]:
    observation = observation_from_metadata(metadata, offer=offer)
    return apply_promotion_observation(listing, observation, now=now)
