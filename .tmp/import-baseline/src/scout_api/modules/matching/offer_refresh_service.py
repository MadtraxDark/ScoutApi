"""Re-scrape store listings and append offer history with change events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
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

    def refresh(self, request: OfferRefreshRequest) -> OfferRefreshResponse:
        repo = MatchingRepository(self._session)
        listings = self._resolve_listings(repo, request)
        results: list[OfferRefreshResult] = []
        for listing in listings:
            results.append(
                self._refresh_listing(repo, listing, request.include_details)
            )
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
        except RequestError as exc:
            if exc.code in {"UPSTREAM_BLOCKED", "AUTH_REQUIRED"}:
                diff = diff_offers(previous_payload, None, scrape_failed=True)
                events = [
                    OfferEventView(
                        event_type="scrape_failed",
                        before=previous_payload,
                        after={"error": str(exc), "code": exc.code},
                        detected_at=datetime.now(UTC),
                    )
                ]
                for event in diff.events:
                    repo.append_event(
                        listing,
                        event,
                        before=previous_payload,
                        after={"error": str(exc), "code": exc.code},
                    )
                mark_check_failure(
                    listing, error=f"{exc.code}: {exc}", now=datetime.now(UTC)
                )
                return OfferRefreshResult(
                    listing_id=listing.id,
                    store=listing.store,
                    url=listing.url,
                    status="scrape_failed",
                    events=events,
                    previous=_view_from_payload(previous_payload, previous.fingerprint)
                    if previous_payload and previous
                    else None,
                    error=str(exc),
                )
            raise
        except ParseError as exc:
            # Product page gone / unparseable as product → treat as removed.
            diff = diff_offers(previous_payload, None, removed=True)
            repo.mark_listing_removed(listing)
            mark_removed_listing(listing)
            repo.append_event(
                listing,
                "offer_removed",
                before=previous_payload,
                after={"error": str(exc)},
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
                        after={"error": str(exc)},
                        detected_at=datetime.now(UTC),
                    )
                ],
                previous=_view_from_payload(previous_payload, previous.fingerprint)
                if previous_payload and previous
                else None,
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
            )
            event_views.append(
                OfferEventView(
                    event_type=event_type,
                    before=previous_payload,
                    after=current_payload,
                    detected_at=now,
                )
            )

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
                before=previous_payload,
                after={
                    "promotion_status": listing.promotion_status,
                    "promotion_expires_at": listing.promotion_expires_at.isoformat()
                    if listing.promotion_expires_at
                    else None,
                    "promotion_payload": listing.promotion_payload,
                },
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
        elif "out_of_stock" in diff.events and len(diff.events) == 1:
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
