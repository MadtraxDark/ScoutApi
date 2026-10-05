"""Offer monitoring service — DB-driven sweep over StoreListing schedules."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.matching.models import MonitorSchedulerState, StoreListing
from scout_api.modules.matching.offer_refresh_service import OfferRefreshService
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import OfferRefreshRequest
from scout_api.modules.monitoring.claim import (
    claim_due_listings,
    count_claimed,
    count_due,
    new_worker_id,
    oldest_due_at,
    release_claim,
    touch_scheduler_heartbeat,
)
from scout_api.modules.monitoring.hooks import (
    initialize_listing_schedule,
    mark_check_failure,
)
from scout_api.modules.monitoring.promotion import expire_due_promotions
from scout_api.modules.monitoring.schedule import ensure_aware, utcnow

logger = logging.getLogger(__name__)


class OfferMonitorService:
    def __init__(
        self,
        *,
        session: Session,
        refresh_service: OfferRefreshService | None = None,
        settings: Settings | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._refresh = refresh_service or OfferRefreshService(session=session)
        self.worker_id = worker_id or new_worker_id()

    def initialize_listing_schedule(
        self,
        listing: StoreListing,
        *,
        checked_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        initialize_listing_schedule(
            listing,
            checked_at=checked_at,
            now=now,
            settings=self._settings,
        )

    def apply_wall_clock_promo_expiry(
        self, listing: StoreListing, *, now: datetime | None = None
    ) -> bool:
        moment = ensure_aware(now or utcnow())
        expired = expire_due_promotions(listing, now=moment)
        if expired:
            repo = MatchingRepository(self._session)
            expires_iso = (
                listing.promotion_expires_at.isoformat()
                if listing.promotion_expires_at
                else None
            )
            repo.append_event(
                listing,
                "promotion_expired",
                before={"expires_at": expires_iso},
                after={"status": "expired", "source": "wall_clock"},
            )
            listing.next_check_at = moment
        return expired

    def sweep_once(
        self,
        *,
        limit: int | None = None,
        now: datetime | None = None,
        include_details: bool = False,
    ) -> dict[str, Any]:
        moment = ensure_aware(now or utcnow())
        started = time.perf_counter()
        claimed = claim_due_listings(
            self._session,
            worker_id=self.worker_id,
            limit=limit,
            now=moment,
            settings=self._settings,
        )
        results: list[dict[str, Any]] = []
        for listing in claimed:
            self.apply_wall_clock_promo_expiry(listing, now=moment)
            item_started = time.perf_counter()
            scheduled_for = listing.last_check_scheduled_for
            delay_seconds = listing.last_check_delay_seconds
            try:
                response = self._refresh.refresh(
                    OfferRefreshRequest(
                        listing_ids=[listing.id],
                        include_details=include_details,
                    )
                )
                if listing.check_worker_id == self.worker_id:
                    release_claim(listing)
                result = response.results[0] if response.results else None
                status = result.status if result else "scrape_failed"
                duration_ms = int((time.perf_counter() - item_started) * 1000)
                logger.info(
                    "offer_monitor_check listing_id=%s store=%s status=%s "
                    "delay_seconds=%s duration_ms=%s next_check_at=%s",
                    listing.id,
                    listing.store,
                    status,
                    delay_seconds,
                    duration_ms,
                    listing.next_check_at.isoformat()
                    if listing.next_check_at
                    else None,
                )
                results.append(
                    {
                        "listing_id": str(listing.id),
                        "store": listing.store,
                        "status": status,
                        "delay_seconds": delay_seconds,
                        "duration_ms": duration_ms,
                        "scheduled_for": scheduled_for.isoformat()
                        if scheduled_for
                        else None,
                        "next_check_at": listing.next_check_at.isoformat()
                        if listing.next_check_at
                        else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                mark_check_failure(
                    listing,
                    error=str(exc),
                    now=utcnow(),
                    settings=self._settings,
                )
                logger.exception(
                    "offer_monitor_check_failed listing_id=%s store=%s",
                    listing.id,
                    listing.store,
                )
                results.append(
                    {
                        "listing_id": str(listing.id),
                        "store": listing.store,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        touch_scheduler_heartbeat(
            self._session,
            worker_id=self.worker_id,
            claimed_count=len(claimed),
            processed_count=len(results),
            now=utcnow(),
        )
        self._session.commit()
        return {
            "worker_id": self.worker_id,
            "claimed": len(claimed),
            "processed": len(results),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "results": results,
            "due_remaining": count_due(self._session, now=utcnow()),
        }

    def diagnostics(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = ensure_aware(now or utcnow())
        monitored = int(
            self._session.scalar(
                select(func.count())
                .select_from(StoreListing)
                .where(
                    StoreListing.monitoring_enabled.is_(True),
                    StoreListing.status == "active",
                )
            )
            or 0
        )
        failed = int(
            self._session.scalar(
                select(func.count())
                .select_from(StoreListing)
                .where(
                    StoreListing.monitoring_enabled.is_(True),
                    StoreListing.consecutive_failures > 0,
                )
            )
            or 0
        )
        promo_expiring = int(
            self._session.scalar(
                select(func.count())
                .select_from(StoreListing)
                .where(
                    StoreListing.promotion_status == "active",
                    StoreListing.promotion_expires_at.is_not(None),
                    StoreListing.promotion_expires_at <= moment,
                )
            )
            or 0
        )
        promo_expired_pending = int(
            self._session.scalar(
                select(func.count())
                .select_from(StoreListing)
                .where(
                    StoreListing.promotion_status == "expired",
                    StoreListing.monitoring_enabled.is_(True),
                    StoreListing.status == "active",
                    StoreListing.next_check_at.is_not(None),
                    StoreListing.next_check_at <= moment,
                )
            )
            or 0
        )
        next_run = self._session.scalar(
            select(func.min(StoreListing.next_check_at)).where(
                StoreListing.monitoring_enabled.is_(True),
                StoreListing.status == "active",
                StoreListing.next_check_at.is_not(None),
                StoreListing.next_check_at > moment,
            )
        )
        state = self._session.get(MonitorSchedulerState, 1)
        oldest = oldest_due_at(self._session, now=moment)
        max_delay = None
        if oldest is not None:
            max_delay = int((moment - ensure_aware(oldest)).total_seconds())
        return {
            "monitored_active": monitored,
            "due_count": count_due(self._session, now=moment),
            "claimed_count": count_claimed(self._session, now=moment),
            "failed_count": failed,
            "oldest_due_check_at": oldest.isoformat() if oldest else None,
            "max_delay_seconds": max_delay,
            "next_check_at": next_run.isoformat() if next_run else None,
            "promotions_expiring_now": promo_expiring,
            "promotions_expired_awaiting_refresh": promo_expired_pending,
            "scheduler_last_heartbeat_at": (
                state.last_heartbeat_at.isoformat()
                if state and state.last_heartbeat_at
                else None
            ),
            "scheduler_worker_id": state.worker_id if state else None,
            "scheduler_last_claimed_count": (state.last_claimed_count if state else 0),
        }
