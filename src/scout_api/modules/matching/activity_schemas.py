"""Read contract for persistent catalog activity; presentation belongs to UI."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ActivityType = Literal[
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
    "other",
]


class ActivityProduct(BaseModel):
    id: UUID
    title: str
    brand: str | None = None
    model: str | None = None
    primary_image_url: str | None = None


class ActivityStore(BaseModel):
    key: str
    display_name: str


class ActivityOffer(BaseModel):
    listing_id: UUID
    old_currency: str | None = None
    currency: str | None = None
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    old_price_kind: Literal["pix", "regular", "promotion"] | None = None
    new_price_kind: Literal["pix", "regular", "promotion"] | None = None
    price_direction: Literal["decreased", "increased", "unchanged"] | None = None
    old_availability: str | None = None
    availability: str | None = None
    available: bool | None = None
    promotion_expires_at: datetime | None = None
    old_seller: str | None = None
    seller: str | None = None


class ActivityItem(BaseModel):
    id: str
    type: ActivityType
    occurred_at: datetime
    product: ActivityProduct
    store: ActivityStore | None = None
    offer: ActivityOffer | None = None


class ActivityListResponse(BaseModel):
    items: list[ActivityItem]
    next_cursor: str | None = None
