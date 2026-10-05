"""Offer snapshot fingerprinting and change detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from scout_api.modules.crawler.models.product import ProductOffer

OfferEventType = Literal[
    "unchanged",
    "price_changed",
    "seller_changed",
    "availability_changed",
    "offer_removed",
    "out_of_stock",
    "new_offer",
    "scrape_failed",
    "offer_created",
    "gtin_learned",
    "promotion_activated",
    "promotion_expired",
    "promotion_updated",
]


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def offer_fingerprint(
    *,
    price: Decimal | None,
    currency: str | None,
    seller: str | None,
    availability: str | None,
    pix_price: Decimal | None = None,
) -> str:
    payload = {
        "price": _decimal_str(price),
        "currency": currency,
        "seller": (seller or "").strip().casefold() or None,
        "availability": availability,
        "pix_price": _decimal_str(pix_price),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_from_offer(offer: ProductOffer) -> str:
    return offer_fingerprint(
        price=offer.price,
        currency=offer.currency,
        seller=offer.seller,
        availability=offer.availability,
        pix_price=offer.pix_price,
    )


def snapshot_dict_from_offer(offer: ProductOffer) -> dict[str, Any]:
    return {
        "price": _decimal_str(offer.price),
        "currency": offer.currency,
        "seller": offer.seller,
        "availability": offer.availability,
        "available": offer.available,
        "pix_price": _decimal_str(offer.pix_price),
        "original_price": _decimal_str(offer.original_price),
        "scraped_at": offer.scraped_at.isoformat(),
        "product_id": offer.product_id,
        "sku": offer.sku,
        "url": offer.url,
        "canonical_url": offer.canonical_url,
        "store": offer.store,
        "country": offer.country,
        "metadata": offer.metadata,
    }


@dataclass(frozen=True)
class OfferDiff:
    events: tuple[OfferEventType, ...]
    previous_fingerprint: str | None
    current_fingerprint: str | None


def diff_offers(
    previous: dict[str, Any] | None,
    current: ProductOffer | None,
    *,
    removed: bool = False,
    scrape_failed: bool = False,
) -> OfferDiff:
    if scrape_failed:
        return OfferDiff(
            events=("scrape_failed",),
            previous_fingerprint=previous.get("fingerprint") if previous else None,
            current_fingerprint=None,
        )
    if removed:
        return OfferDiff(
            events=("offer_removed",),
            previous_fingerprint=previous.get("fingerprint") if previous else None,
            current_fingerprint=None,
        )
    if current is None:
        return OfferDiff(
            events=("scrape_failed",),
            previous_fingerprint=None,
            current_fingerprint=None,
        )

    current_fp = fingerprint_from_offer(current)
    if previous is None:
        return OfferDiff(
            events=("new_offer",),
            previous_fingerprint=None,
            current_fingerprint=current_fp,
        )

    prev_fp = previous.get("fingerprint")
    if prev_fp == current_fp:
        return OfferDiff(
            events=("unchanged",),
            previous_fingerprint=prev_fp,
            current_fingerprint=current_fp,
        )

    events: list[OfferEventType] = []
    prev_price = previous.get("price")
    curr_price = _decimal_str(current.price)
    if prev_price != curr_price:
        events.append("price_changed")

    prev_seller = (previous.get("seller") or "").strip().casefold() or None
    curr_seller = (current.seller or "").strip().casefold() or None
    if prev_seller != curr_seller:
        events.append("seller_changed")

    prev_avail = previous.get("availability")
    if prev_avail != current.availability:
        events.append("availability_changed")
        if current.availability == "out_of_stock" or current.available is False:
            events.append("out_of_stock")

    if not events:
        # Fingerprint changed via pix/currency etc.
        events.append("price_changed")

    return OfferDiff(
        events=tuple(events),
        previous_fingerprint=prev_fp,
        current_fingerprint=current_fp,
    )


def parse_scraped_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
