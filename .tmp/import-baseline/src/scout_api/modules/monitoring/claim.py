"""Claim due monitored listings with lease + SKIP LOCKED (PostgreSQL)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from scout_api.core.config import Settings, get_settings
from scout_api.modules.matching.models import MonitorSchedulerState, StoreListing
from scout_api.modules.monitoring.schedule import ensure_aware, utcnow

logger = logging.getLogger(__name__)


def _priority_order(*, now: datetime) -> ColumnElement[Any]:
    """1=expired promo, 2=transient retry, 3=regular overdue."""
    return case(
        (
            and_(
                StoreListing.promotion_status == "active",
                StoreListing.promotion_expires_at.is_not(None),
                StoreListing.promotion_expires_at <= now,
            ),
            1,
        ),
        (
            and_(
                StoreListing.promotion_status == "expired",
                StoreListing.promotion_expires_at.is_not(None),
            ),
            1,
        ),
        (StoreListing.consecutive_failures > 0, 2),
        else_=3,
    )


def _due_filter(*, now: datetime) -> ColumnElement[bool]:
    lease_free = or_(
        StoreListing.check_claim_expires_at.is_(None),
        StoreListing.check_claim_expires_at <= now,
    )
    promo_due = and_(
        StoreListing.promotion_status == "active",
        StoreListing.promotion_expires_at.is_not(None),
        StoreListing.promotion_expires_at <= now,
    )
    schedule_due = and_(
        StoreListing.next_check_at.is_not(None),
        StoreListing.next_check_at <= now,
    )
    return and_(
        StoreListing.monitoring_enabled.is_(True),
        StoreListing.status == "active",
        lease_free,
        or_(schedule_due, promo_due),
    )


def claim_due_listings(
    session: Session,
    *,
    worker_id: str,
    limit: int | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[StoreListing]:
    """Atomically claim up to ``limit`` due listings for this worker."""
    cfg = settings or get_settings()
    moment = ensure_aware(now or utcnow())
    batch = max(1, int(limit or cfg.offer_monitor_batch_size))
    lease = timedelta(seconds=max(30, int(cfg.offer_monitor_lease_seconds)))
    claim_expires = moment + lease

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return _claim_postgres(
            session,
            worker_id=worker_id,
            batch=batch,
            now=moment,
            claim_expires=claim_expires,
        )
    return _claim_portable(
        session,
        worker_id=worker_id,
        batch=batch,
        now=moment,
        claim_expires=claim_expires,
    )


def _claim_postgres(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[StoreListing]:
    # SELECT … FOR UPDATE SKIP LOCKED then UPDATE claimed rows.
    stmt: Select[tuple[StoreListing]] = (
        select(StoreListing)
        .where(_due_filter(now=now))
        .order_by(
            _priority_order(now=now),
            StoreListing.next_check_at.asc().nulls_last(),
        )
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    rows = list(session.scalars(stmt).all())
    claimed: list[StoreListing] = []
    for listing in rows:
        listing.check_worker_id = worker_id
        listing.check_claimed_at = now
        listing.check_claim_expires_at = claim_expires
        listing.last_check_scheduled_for = listing.next_check_at
        if listing.next_check_at is not None:
            delay = int((now - ensure_aware(listing.next_check_at)).total_seconds())
            listing.last_check_delay_seconds = max(0, delay)
        else:
            listing.last_check_delay_seconds = 0
        claimed.append(listing)
    session.flush()
    return claimed


def _claim_portable(
    session: Session,
    *,
    worker_id: str,
    batch: int,
    now: datetime,
    claim_expires: datetime,
) -> list[StoreListing]:
    """Optimistic claim for SQLite tests (no SKIP LOCKED)."""
    candidates = list(
        session.scalars(
            select(StoreListing)
            .where(_due_filter(now=now))
            .order_by(_priority_order(now=now), StoreListing.next_check_at.asc())
            .limit(batch * 3)
        ).all()
    )
    claimed: list[StoreListing] = []
    for listing in candidates:
        if len(claimed) >= batch:
            break
        result = session.execute(
            update(StoreListing)
            .where(
                StoreListing.id == listing.id,
                or_(
                    StoreListing.check_claim_expires_at.is_(None),
                    StoreListing.check_claim_expires_at <= now,
                ),
            )
            .values(
                check_worker_id=worker_id,
                check_claimed_at=now,
                check_claim_expires_at=claim_expires,
                last_check_scheduled_for=listing.next_check_at,
                last_check_delay_seconds=(
                    max(
                        0,
                        int(
                            (now - ensure_aware(listing.next_check_at)).total_seconds()
                        ),
                    )
                    if listing.next_check_at is not None
                    else 0
                ),
            )
        )
        if int(result.rowcount or 0) == 1:  # type: ignore[attr-defined]
            session.refresh(listing)
            claimed.append(listing)
    session.flush()
    return claimed


def release_claim(listing: StoreListing) -> None:
    listing.check_worker_id = None
    listing.check_claimed_at = None
    listing.check_claim_expires_at = None


def touch_scheduler_heartbeat(
    session: Session,
    *,
    worker_id: str,
    claimed_count: int = 0,
    processed_count: int = 0,
    now: datetime | None = None,
) -> MonitorSchedulerState:
    moment = ensure_aware(now or utcnow())
    state = session.get(MonitorSchedulerState, 1)
    if state is None:
        state = MonitorSchedulerState(id=1)
        session.add(state)
    state.worker_id = worker_id
    state.last_heartbeat_at = moment
    state.last_sweep_at = moment
    state.last_claimed_count = claimed_count
    state.last_processed_count = processed_count
    state.updated_at = moment
    session.flush()
    return state


def new_worker_id() -> str:
    return f"mon-{uuid.uuid4().hex[:12]}"


def count_due(session: Session, *, now: datetime | None = None) -> int:
    moment = ensure_aware(now or utcnow())
    return int(
        session.scalar(
            select(func.count())
            .select_from(StoreListing)
            .where(_due_filter(now=moment))
        )
        or 0
    )


def count_claimed(session: Session, *, now: datetime | None = None) -> int:
    moment = ensure_aware(now or utcnow())
    return int(
        session.scalar(
            select(func.count())
            .select_from(StoreListing)
            .where(
                StoreListing.check_claim_expires_at.is_not(None),
                StoreListing.check_claim_expires_at > moment,
            )
        )
        or 0
    )


def oldest_due_at(session: Session, *, now: datetime | None = None) -> datetime | None:
    moment = ensure_aware(now or utcnow())
    return session.scalar(
        select(func.min(StoreListing.next_check_at)).where(
            StoreListing.monitoring_enabled.is_(True),
            StoreListing.status == "active",
            StoreListing.next_check_at.is_not(None),
            StoreListing.next_check_at <= moment,
        )
    )
