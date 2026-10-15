"""Authenticated API for product favorites."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scout_api.core.config import get_settings
from scout_api.core.database import get_db_session
from scout_api.modules.auth.deps import enforce_rate_limit, require_authenticated_user
from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.favorites.domain import effective_offer_price
from scout_api.modules.favorites.service import FavoritesService
from scout_api.modules.images.models import ProductImage
from scout_api.modules.images.service import to_image_view
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferSnapshot,
    ProductFavorite,
    StoreListing,
)

router = APIRouter(tags=["Favoritos"])
_RL = Depends(enforce_rate_limit("default"))


class FavoriteOfferView(BaseModel):
    listing_id: uuid.UUID
    store: str
    listing_url: str
    price: Decimal
    currency: str
    last_updated_at: datetime


class FavoriteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    title: str
    brand: str | None
    primary_image_url: str | None
    offers: list[FavoriteOfferView]
    offers_count: int
    last_updated_at: datetime | None
    notify_price_drop: bool
    email_available: bool
    created_at: datetime


class FavoriteMutationResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    notify_price_drop: bool
    email_available: bool


class FavoriteListResponse(BaseModel):
    items: list[FavoriteView]
    count: int
    limit: int
    offset: int


class FavoriteAlertPatch(BaseModel):
    notify_price_drop: bool


def _require_db() -> Generator[Session, None, None]:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    yield from get_db_session()


@router.get("/favorites", response_model=FavoriteListResponse, dependencies=[_RL])
def list_favorites(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    session: Annotated[Session, Depends(_require_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FavoriteListResponse:
    base = (
        select(ProductFavorite, CanonicalProduct)
        .join(CanonicalProduct, CanonicalProduct.id == ProductFavorite.product_id)
        .where(ProductFavorite.user_id == principal.id)
        .order_by(ProductFavorite.created_at.desc())
    )
    total = int(
        session.scalar(
            select(func.count())
            .select_from(ProductFavorite)
            .where(ProductFavorite.user_id == principal.id)
        )
        or 0
    )
    FavoritesService(session).sync_email(user_id=principal.id, email=principal.email)
    rows = session.execute(base.offset(offset).limit(limit)).all()
    product_ids = [product.id for _, product in rows]
    image_rows = (
        session.execute(
            select(ProductImage).where(
                ProductImage.canonical_product_id.in_(product_ids),
                ProductImage.is_main.is_(True),
            )
        )
        .scalars()
        .all()
        if product_ids
        else []
    )
    images: dict[uuid.UUID, str] = {
        image.canonical_product_id: view_url
        for image in image_rows
        if (view_url := to_image_view(image).display_url) is not None
    }
    listings = (
        list(
            session.scalars(
                select(StoreListing).where(
                    StoreListing.canonical_product_id.in_(product_ids),
                    StoreListing.status == "active",
                )
            ).all()
        )
        if product_ids
        else []
    )
    listing_ids = [listing.id for listing in listings]
    latest: dict[uuid.UUID, OfferSnapshot] = {}
    if listing_ids:
        latest_ids = (
            select(
                OfferSnapshot.listing_id,
                func.max(OfferSnapshot.scraped_at).label("scraped_at"),
            )
            .where(OfferSnapshot.listing_id.in_(listing_ids))
            .group_by(OfferSnapshot.listing_id)
            .subquery()
        )
        snapshots = (
            session.execute(
                select(OfferSnapshot).join(
                    latest_ids,
                    (OfferSnapshot.listing_id == latest_ids.c.listing_id)
                    & (OfferSnapshot.scraped_at == latest_ids.c.scraped_at),
                )
            )
            .scalars()
            .all()
        )
        latest = {row.listing_id: row for row in snapshots}
    product_listings: dict[uuid.UUID, list[StoreListing]] = {}
    for listing in listings:
        product_listings.setdefault(listing.canonical_product_id, []).append(listing)
    items = []
    for favorite, product in rows:
        offer_views = []
        for listing in product_listings.get(product.id, []):
            offer = latest.get(listing.id)
            if offer is None or offer.available is False or not offer.currency:
                continue
            price = effective_offer_price(
                {
                    "price": offer.price,
                    "pix_price": offer.payload.get("pix_price"),
                    "original_price": offer.payload.get("original_price"),
                    "available": offer.available,
                }
            )
            if price is None:
                continue
            offer_views.append(
                FavoriteOfferView(
                    listing_id=listing.id,
                    store=listing.store,
                    listing_url=listing.url,
                    price=price,
                    currency=offer.currency,
                    last_updated_at=offer.scraped_at,
                )
            )
        items.append(
            FavoriteView(
                id=favorite.id,
                product_id=product.id,
                title=product.title,
                brand=product.brand,
                primary_image_url=images.get(product.id),
                offers=offer_views,
                offers_count=len(offer_views),
                last_updated_at=max(
                    (offer.last_updated_at for offer in offer_views), default=None
                ),
                notify_price_drop=favorite.notify_price_drop,
                email_available=bool(principal.email and get_settings().email_enabled),
                created_at=favorite.created_at,
            )
        )
    return FavoriteListResponse(items=items, count=total, limit=limit, offset=offset)


@router.put(
    "/products/{product_id}/favorite",
    response_model=FavoriteMutationResponse,
    dependencies=[_RL],
)
def put_favorite(
    product_id: uuid.UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    session: Annotated[Session, Depends(_require_db)],
) -> FavoriteMutationResponse:
    try:
        favorite = FavoritesService(session).put(
            user_id=principal.id, email=principal.email, product_id=product_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PRODUCT_NOT_FOUND",
                "message": "Produto não encontrado",
                "retryable": False,
            },
        ) from exc
    return FavoriteMutationResponse(
        id=favorite.id,
        product_id=favorite.product_id,
        notify_price_drop=favorite.notify_price_drop,
        email_available=bool(principal.email and get_settings().email_enabled),
    )


@router.delete(
    "/products/{product_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_RL],
)
def delete_favorite(
    product_id: uuid.UUID,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    session: Annotated[Session, Depends(_require_db)],
) -> None:
    FavoritesService(session).remove(user_id=principal.id, product_id=product_id)
    session.commit()


@router.patch(
    "/products/{product_id}/favorite",
    response_model=FavoriteMutationResponse,
    dependencies=[_RL],
)
def patch_favorite(
    product_id: uuid.UUID,
    payload: FavoriteAlertPatch,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    session: Annotated[Session, Depends(_require_db)],
) -> FavoriteMutationResponse:
    favorite = FavoritesService(session).set_alerts(
        user_id=principal.id, product_id=product_id, enabled=payload.notify_price_drop
    )
    if favorite is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FAVORITE_NOT_FOUND",
                "message": "Favorito não encontrado",
                "retryable": False,
            },
        )
    return FavoriteMutationResponse(
        id=favorite.id,
        product_id=favorite.product_id,
        notify_price_drop=favorite.notify_price_drop,
        email_available=bool(principal.email and get_settings().email_enabled),
    )
