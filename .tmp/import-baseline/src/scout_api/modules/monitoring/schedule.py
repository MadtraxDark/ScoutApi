"""Persistent offer monitoring — schedule helpers (clock in PostgreSQL)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Protocol

from scout_api.core.config import Settings, get_settings


class _HasPromotionExpiry(Protocol):
    promotion_expires_at: datetime | None
    promotion_status: str


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def regular_interval(settings: Settings | None = None) -> timedelta:
    cfg = settings or get_settings()
    hours = max(1, int(cfg.offer_refresh_interval_hours))
    return timedelta(hours=hours)


def jitter_delta(settings: Settings | None = None) -> timedelta:
    cfg = settings or get_settings()
    max_seconds = max(0, int(cfg.offer_monitor_jitter_seconds))
    if max_seconds <= 0:
        return timedelta(0)
    return timedelta(seconds=random.randint(0, max_seconds))


def promotion_grace(settings: Settings | None = None) -> timedelta:
    cfg = settings or get_settings()
    return timedelta(seconds=max(0, int(cfg.offer_promotion_grace_seconds)))


def compute_next_regular_check_at(
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> datetime:
    base = ensure_aware(now or utcnow())
    return base + regular_interval(settings) + jitter_delta(settings)


def compute_next_check_at(
    *,
    now: datetime | None = None,
    next_regular: datetime | None = None,
    promotion_expires_at: datetime | None = None,
    settings: Settings | None = None,
) -> datetime:
    """Earliest actionable time: regular cadence vs promo expiry (+ grace).

    Cadence semantics: after a successful check, the next regular due is
    ``now + OFFER_REFRESH_INTERVAL_HOURS`` (+ jitter). If an active promotion
    expires sooner, ``next_check_at`` is pulled forward to that instant
    (plus grace) so expired promos are revalidated without waiting 12h.
    """
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    regular = ensure_aware(
        next_regular or compute_next_regular_check_at(now=moment, settings=cfg)
    )
    if promotion_expires_at is None:
        return regular
    promo = ensure_aware(promotion_expires_at)
    if promo <= moment:
        # Already expired → due immediately (caller may still claim now).
        return moment
    promo_due = promo + promotion_grace(cfg)
    return min(regular, promo_due)


def compute_retry_at(
    *,
    consecutive_failures: int,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> datetime:
    """Transient failure backoff — does not advance the regular cadence."""
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    failures = max(1, int(consecutive_failures))
    base = max(30, int(cfg.offer_monitor_retry_base_seconds))
    cap = max(base, int(cfg.offer_monitor_retry_max_seconds))
    delay = min(cap, base * (2 ** (failures - 1)))
    return moment + timedelta(seconds=delay)


def effective_due_at(
    listing: _HasPromotionExpiry, *, now: datetime | None = None
) -> datetime | None:
    """When a listing should be considered due, including promo expiry."""
    moment = ensure_aware(now or utcnow())
    candidates: list[datetime] = []
    next_check = getattr(listing, "next_check_at", None)
    if isinstance(next_check, datetime):
        candidates.append(ensure_aware(next_check))
    expires = listing.promotion_expires_at
    if (
        listing.promotion_status == "active"
        and isinstance(expires, datetime)
        and ensure_aware(expires) <= moment
    ):
        candidates.append(moment)
    if not candidates:
        return None
    return min(candidates)
