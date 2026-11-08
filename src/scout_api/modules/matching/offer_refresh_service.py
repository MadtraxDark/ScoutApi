"""Re-scrape store listings and append offer history with change events."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.exceptions import (
    FAILURE_EXTRACTION,
    FAILURE_GONE,
    FAILURE_INFRA,
    FAILURE_LOCAL_THROTTLE,
    ParseError,
    RequestError,
    classify_failure,
)
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.models.product import product_offer_from_price_item
from scout_api.modules.crawler.services.offer_scrape_service import OfferScrapeService
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.matching.models import StoreListing
from scout_api.modules.matching.offer_diff import (
    diff_offers,
    fingerprint_from_offer,
    snapshot_dict_from_offer,
)
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import (
    OfferEventView,
    OfferRefreshRequest,
    OfferRefreshResponse,
    OfferRefreshResult,
    OfferSnapshotView,
)
from scout_api.modules.monitoring.hooks import (
    mark_check_deferred,
    mark_check_failure,
    mark_check_success,
    mark_removed_listing,
)

logger = logging.getLogger(__name__)


def _snapshot_view(
    *,
    price: Decimal | None,
    currency: str | None,
    seller: str | None,
    availability: str | None,
    available: bool | None,
    scraped_at: datetime | None,
    fingerprint: str | None,
) -> OfferSnapshotView:
    return OfferSnapshotView(
        price=price,
        currency=currency,
        seller=seller,
        availability=availability,
        available=available,
        scraped_at=scraped_at,
        fingerprint=fingerprint,
    )


def _view_from_payload(
    payload: dict[str, Any], fingerprint: str | None
) -> OfferSnapshotView:
    scraped = payload.get("scraped_at")
    scraped_at: datetime | None
    if isinstance(scraped, datetime):
        scraped_at = scraped
    elif isinstance(scraped, str):
        try:
            scraped_at = datetime.fromisoformat(scraped)
        except ValueError:
            scraped_at = None
    else:
        scraped_at = None
    price = payload.get("price")
    price_dec = Decimal(price) if price is not None else None
    return _snapshot_view(
        price=price_dec,
        currency=payload.get("currency"),
        seller=payload.get("seller"),
        availability=payload.get("availability"),
        available=payload.get("available"),
        scraped_at=scraped_at,
        fingerprint=fingerprint,
    )


_EXTRACTION_STREAK = re.compile(r"^EXTRACTION#(\d+):")


def _extraction_streak(listing: StoreListing) -> int:
    """Consecutive extraction failures encoded in the last recorded error."""
    match = _EXTRACTION_STREAK.match(listing.last_check_error or "")
    return int(match.group(1)) if match else 0


class OfferRefreshService:
    def __init__(
        self,
        *,
        session: Session,
        offer_service: OfferScrapeService | None = None,
        product_service: ProductScrapeService | None = None,
    ) -> None:
        self._session = session
        self._offer_service = offer_service or OfferScrapeService()
        self._product_service = product_service or ProductScrapeService()

    def refresh(
        self,
        request: OfferRefreshRequest,
        *,
        claim_worker_id: str | None = None,
    ) -> OfferRefreshResponse:
        repo = MatchingRepository(self._session)
        listings = self._resolve_listings(repo, request)
        results: list[OfferRefreshResult] = []
        for listing in listings:
            locked = self._session.scalars(
                select(StoreListing)
                .where(StoreListing.id == listing.id)
                .with_for_update()
            ).first()
            if locked is None:
                continue
            now = datetime.now(UTC)
            claimed_until = locked.check_claim_expires_at
            if claimed_until is not None and claimed_until.tzinfo is None:
                claimed_until = claimed_until.replace(tzinfo=UTC)
            if (
                claimed_until is not None
                and claimed_until > now
                and locked.check_worker_id != claim_worker_id
            ):
                results.append(
                    OfferRefreshResult(
                        listing_id=locked.id,
                        store=locked.store,
                        url=locked.url,
                        status="deferred",
                        error="LISTING_CHECK_ALREADY_CLAIMED",
                    )
                )
                continue
            owner = claim_worker_id or f"refresh-{uuid.uuid4().hex}"
            locked.check_worker_id = owner
            locked.check_claimed_at = now
            # The row lock is held until the scrape transaction commits; this
            # lease also lets monitor workers avoid the listing after commit.
            locked.check_claim_expires_at = None
            try:
                results.append(
                    self._refresh_listing(repo, locked, request.include_details)
                )
            except Exception:
                if locked.check_worker_id == owner:
                    from scout_api.modules.monitoring.hooks import release_claim

                    release_claim(locked)
                raise
        self._session.flush()
        return OfferRefreshResponse(results=results)

    def _resolve_listings(
        self, repo: MatchingRepository, request: OfferRefreshRequest
    ) -> list[StoreListing]:
        if request.listing_ids:
            return repo.list_listings_by_ids(request.listing_ids)
        if request.canonical_product_id:
            return repo.list_listings_for_canonical(request.canonical_product_id)
        if request.urls:
            found: list[StoreListing] = []
            for url in request.urls:
                canonical = canonicalize_url(str(url))
                # Try match by canonical_url across stores
                from sqlalchemy import select

                from scout_api.modules.matching.models import StoreListing as SL

                stmt = select(SL).where(SL.canonical_url == canonical)
                row = self._session.scalars(stmt).first()
                if row is not None:
                    found.append(row)
            return found
        raise RequestError(
            "Informe canonical_product_id, listing_ids ou urls",
            code="INVALID_REQUEST",
        )

    def _refresh_listing(
        self,
        repo: MatchingRepository,
        listing: StoreListing,
        include_details: bool,
    ) -> OfferRefreshResult:
        previous = repo.latest_snapshot(listing.id)
        previous_payload: dict[str, Any] | None = None
        if previous is not None:
            previous_payload = {
                **(previous.payload or {}),
                "fingerprint": previous.fingerprint,
                "price": str(previous.price) if previous.price is not None else None,
                "currency": previous.currency,
                "seller": previous.seller,
                "availability": previous.availability,
                "available": previous.available,
            }

        try:
            if include_details:
                item = self._product_service.scrape(listing.url, include_images=False)
                offer = product_offer_from_price_item(item)
            else:
                offer = self._offer_service.scrape_offer(listing.url)
        except (RequestError, ParseError) as exc:
            kind = classify_failure(exc)
            code = getattr(exc, "code", "PARSE_ERROR")
            retry_after = getattr(exc, "retry_after", None)
            cfg = get_settings()
            prev_view = (
                _view_from_payload(previous_payload, previous.fingerprint)
                if previous_payload and previous
                else None
            )
            if kind in (FAILURE_LOCAL_THROTTLE, FAILURE_INFRA):
                # Our own spacing / browser outage: not the store's fault. Defer
                # without a failure count or a history event so valid data and
                # the listing health are untouched.
                mark_check_deferred(
                    listing,
                    reason=f"{code}: {exc}",
                    retry_after=retry_after,
                    settings=cfg,
                )
                return OfferRefreshResult(
                    listing_id=listing.id,
                    store=listing.store,
                    url=listing.url,
                    status="deferred",
                    previous=prev_view,
                    error=f"{code}: {exc}",
                )
            # Only a streak of *extraction* failures counts toward removal; blocks,
            # timeouts and throttling accumulated earlier say nothing about the page.
            streak = (
                _extraction_streak(listing) + 1 if kind == FAILURE_EXTRACTION else 0
            )
            gone = kind == FAILURE_GONE or (
                kind == FAILURE_EXTRACTION
                and streak
                >= max(1, int(cfg.offer_monitor_extraction_failures_before_removal))
            )
            if gone:
                repo.mark_listing_removed(listing)
                mark_removed_listing(listing)
                after = {"error": str(exc), "kind": kind, "observed": False}
                repo.append_event(
                    listing, "offer_removed", before=previous_payload, after=after
                )
                return OfferRefreshResult(
                    listing_id=listing.id,
                    store=listing.store,
                    url=listing.url,
                    status="removed",
                    events=[
                        OfferEventView(
                            event_type="offer_removed",
                            before=previous_payload,
                            after=after,
                            detected_at=datetime.now(UTC),
                        )
                    ],
                    previous=prev_view,
                    error=str(exc),
                )
            # blocked / timeout / extraction (below threshold) / upstream error:
            # keep the last good snapshot untouched and record an *unobserved*
            # failure so readers never mistake it for a new (empty) state.
            after = {
                "error": str(exc),
                "code": code,
                "kind": kind,
                "observed": False,
            }
            diff = diff_offers(previous_payload, None, scrape_failed=True)
            for event in diff.events:
                repo.append_event(listing, event, before=previous_payload, after=after)
            mark_check_failure(
                listing,
                error=f"EXTRACTION#{streak}: {exc}"
                if kind == FAILURE_EXTRACTION
                else f"{code}: {exc}",
                now=datetime.now(UTC),
                settings=cfg,
                retry_after=retry_after,
            )
            return OfferRefreshResult(
                listing_id=listing.id,
                store=listing.store,
                url=listing.url,
                status="scrape_failed",
                events=[
                    OfferEventView(
                        event_type="scrape_failed",
                        before=previous_payload,
                        after=after,
                        detected_at=datetime.now(UTC),
                    )
                ],
                previous=prev_view,
                error=str(exc),
            )

        diff = diff_offers(previous_payload, offer)
        current_payload = {
            **snapshot_dict_from_offer(offer),
            "fingerprint": fingerprint_from_offer(offer),
        }
        repo.append_snapshot_from_offer(listing, offer)
        event_views: list[OfferEventView] = []
        now = datetime.now(UTC)
        for event_type in diff.events:
            repo.append_event(
                listing,
                event_type,
                before=previous_payload,
                after=current_payload,
                detected_at=now,
            )
            event_views.append(
                OfferEventView(
                    event_type=event_type,
                    before=previous_payload,
                    after=current_payload,
                    detected_at=now,
                )
            )

        previous_promotion = {
            **(previous_payload or {}),
            "promotion_payload": dict(listing.promotion_payload or {}),
            "promotion_price": str(listing.promotion_price)
            if listing.promotion_price is not None
            else None,
        }
        promo_events = mark_check_success(
            listing,
            offer=offer,
            event_names=list(diff.events),
            now=now,
        )
        for promo_event in promo_events:
            repo.append_event(
                listing,
                promo_event,
                before=previous_promotion,
                after={
                    **current_payload,
                    "promotion_status": listing.promotion_status,
                    "promotion_expires_at": listing.promotion_expires_at.isoformat()
                    if listing.promotion_expires_at
                    else None,
                    "promotion_payload": listing.promotion_payload,
                },
                detected_at=now,
            )
            event_views.append(
                OfferEventView(
                    event_type=promo_event,  # type: ignore[arg-type]
                    before=previous_payload,
                    after={
                        "promotion_status": listing.promotion_status,
                        "promotion_expires_at": listing.promotion_expires_at.isoformat()
                        if listing.promotion_expires_at
                        else None,
                    },
                    detected_at=now,
                )
            )

        status: str
        if "offer_removed" in diff.events:
            status = "removed"
        elif "scrape_failed" in diff.events:
            status = "scrape_failed"
        elif "out_of_stock" in diff.events:
            status = "out_of_stock"
        elif diff.events == ("unchanged",):
            status = "unchanged"
        elif diff.events == ("new_offer",):
            status = "new_offer"
        else:
            status = "changed"

        return OfferRefreshResult(
            listing_id=listing.id,
            store=listing.store,
            url=listing.url,
            status=status,  # type: ignore[arg-type]
            events=event_views,
            previous=_view_from_payload(previous_payload, previous.fingerprint)
            if previous_payload and previous
            else None,
            current=_snapshot_view(
                price=offer.price,
                currency=offer.currency,
                seller=offer.seller,
                availability=offer.availability,
                available=offer.available,
                scraped_at=offer.scraped_at,
                fingerprint=fingerprint_from_offer(offer),
            ),
            offer=offer,
        )
