"""Explicit product registration (canonical identity + optional store listing)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.utils.product_identity import (
    canonical_model_key,
    canonical_variant_key,
)
from scout_api.modules.images.schemas import ApprovedImageInput, ProductImageView
from scout_api.modules.images.service import (
    ProductImageService,
    primary_display_url,
    to_image_view,
)
from scout_api.modules.matching.identity import (
    normalize_brand,
    normalize_gtin,
    variant_key,
)
from scout_api.modules.matching.models import CanonicalProduct, StoreListing
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import (
    ProductListingView,
    ProductListResponse,
    ProductRegisterRequest,
    ProductRegisterResponse,
    ProductSearchResponse,
    ProductUpdateRequest,
    ProductView,
)


def can_access_product(
    product: CanonicalProduct, principal: AuthenticatedPrincipal
) -> bool:
    """BOLA: private products are visible only to owner or admin."""
    if product.owner_user_id is None:
        return True
    if principal.role == UserRole.ADMIN:
        return True
    return product.owner_user_id == principal.id


class ProductRegistrationService:
    """Register catalog products without silent overwrite of existing rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def register(
        self,
        request: ProductRegisterRequest,
        *,
        owner: AuthenticatedPrincipal,
    ) -> ProductRegisterResponse:
        gtin = normalize_gtin(request.gtin)
        if request.gtin and gtin is None:
            raise RequestError(
                "GTIN/EAN/UPC inválido",
                code="INVALID_REQUEST",
            )

        store = request.store
        country = (request.country or "BR").upper() if request.store else None
        has_store_identity = bool(store and request.product_id and country)
        has_url_listing = bool(store and request.canonical_url and request.product_id)
        if gtin is None and not has_store_identity:
            raise RequestError(
                "Informe GTIN válido ou store + country + product_id "
                "(canonical_url recomendada para listing)",
                code="INVALID_REQUEST",
            )

        repo = MatchingRepository(self._session)

        if has_store_identity:
            assert store is not None and country is not None and request.product_id
            existing_listing = repo.find_listing_by_store_identity(
                store=store,
                country=country,
                product_id=request.product_id,
                sku=request.sku,
                canonical_url=(
                    str(request.canonical_url) if request.canonical_url else None
                ),
            )
            if existing_listing is not None:
                product = repo.get_canonical(existing_listing.canonical_product_id)
                if product is None:
                    raise RequestError(
                        "Listing órfão sem produto canônico",
                        code="DATABASE_ERROR",
                    )
                if not can_access_product(product, owner):
                    raise RequestError(
                        "Produto existente pertencente a outro usuário",
                        code="FORBIDDEN",
                    )
                product_view = self.get_product(product.id, viewer=owner)
                if product_view is None:
                    raise RequestError(
                        "Produto existente pertencente a outro usuário",
                        code="FORBIDDEN",
                    )
                if request.images:
                    self._persist_images(product.id, request.images, owner=owner)
                self._seed_offer_snapshot(repo, existing_listing, request)
                product_view = self.get_product(product.id, viewer=owner)
                assert product_view is not None
                listing = repo.get_listing(existing_listing.id) or existing_listing
                return ProductRegisterResponse(
                    created=False,
                    listing_created=False,
                    product=product_view,
                    listing=_to_listing_view(listing, session=self._session),
                )

        attrs = dict(request.attributes or {})
        if request.variant:
            attrs.setdefault("variant", request.variant)
        if "category" not in attrs:
            from scout_api.modules.crawler.utils.product_attributes import (
                detect_product_category,
            )

            detected = detect_product_category(request.title)
            if detected:
                attrs["category"] = detected
        string_attrs = {k: str(v) for k, v in attrs.items() if isinstance(v, str)}
        vkey = request.variant_key or variant_key(string_attrs)

        if gtin is not None:
            existing = repo.find_canonical_by_gtin(gtin)
            if existing is not None:
                if not can_access_product(existing, owner):
                    raise RequestError(
                        "Produto existente pertencente a outro usuário",
                        code="FORBIDDEN",
                    )
                listing_view = None
                listing_created = False
                if has_url_listing or has_store_identity:
                    listing, listing_created = self._attach_listing(
                        repo, existing, request, gtin=gtin, country=country or "BR"
                    )
                    listing_view = _to_listing_view(listing, session=self._session)
                product_view = self.get_product(existing.id, viewer=owner)
                assert product_view is not None
                if request.images:
                    self._persist_images(existing.id, request.images, owner=owner)
                    product_view = self.get_product(existing.id, viewer=owner)
                    assert product_view is not None
                return ProductRegisterResponse(
                    created=False,
                    listing_created=listing_created,
                    product=product_view,
                    listing=listing_view,
                )

        canonical, created = repo.get_or_create_canonical(
            title=request.title,
            brand=request.brand,
            model=request.model,
            variant_key=vkey,
            attributes=attrs,
            gtin=gtin,
            owner_user_id=owner.id,
        )
        if not can_access_product(canonical, owner):
            raise RequestError(
                "Produto existente pertencente a outro usuário",
                code="FORBIDDEN",
            )
        listing_view = None
        listing_created = False
        if has_url_listing or (has_store_identity and request.canonical_url):
            listing, listing_created = self._attach_listing(
                repo, canonical, request, gtin=gtin, country=country or "BR"
            )
            listing_view = _to_listing_view(listing, session=self._session)
        elif has_store_identity and not request.canonical_url:
            # Store identity without URL: still persist listing with synthetic URL.
            assert store and request.product_id
            synthetic = f"scout://{store}/{country}/{request.product_id}"
            listing, listing_created = repo.get_or_create_listing(
                canonical=canonical,
                store=store,
                country=country or "BR",
                product_id=request.product_id,
                url=synthetic,
                canonical_url=synthetic,
                sku=request.sku,
                gtin=gtin,
                title=request.title,
            )
            self._seed_offer_snapshot(repo, listing, request)
            listing_view = _to_listing_view(listing, session=self._session)
        self._session.flush()
        if request.images:
            self._persist_images(canonical.id, request.images, owner=owner)
        product_view = self.get_product(canonical.id, viewer=owner)
        assert product_view is not None
        return ProductRegisterResponse(
            created=created,
            listing_created=listing_created,
            product=product_view,
            listing=listing_view,
        )

    def _persist_images(
        self,
        product_id: UUID,
        images: list[ApprovedImageInput],
        *,
        owner: AuthenticatedPrincipal,
    ) -> None:
        image_service = ProductImageService(self._session)
        image_service.persist_approved(product_id, images, owner=owner)

    def get_product(
        self,
        product_id: UUID,
        *,
        viewer: AuthenticatedPrincipal | None = None,
    ) -> ProductView | None:
        repo = MatchingRepository(self._session)
        canonical = repo.get_canonical(product_id)
        if canonical is None:
            return None
        if viewer is not None and not can_access_product(canonical, viewer):
            return None
        listings = list(canonical.listings or [])
        if not listings:
            listings = repo.list_listings_for_canonical(canonical.id)
        from scout_api.modules.images.repository import ProductImageRepository

        images = [
            to_image_view(row)
            for row in ProductImageRepository(self._session).list_for_product(
                product_id
            )
        ]
        return _to_product_view(
            canonical, listings, images=images, session=self._session
        )

    def list_products(
        self,
        *,
        viewer: AuthenticatedPrincipal,
        limit: int = 50,
        offset: int = 0,
    ) -> ProductListResponse:
        """List canonical products visible to the viewer (stable updated_at desc)."""
        cap = max(1, min(limit, 100))
        start = max(0, offset)
        repo = MatchingRepository(self._session)
        is_admin = viewer.role == UserRole.ADMIN
        total = repo.count_canonical_products(viewer_id=viewer.id, is_admin=is_admin)
        rows = repo.search_canonical_products(
            viewer_id=viewer.id,
            is_admin=is_admin,
            limit=cap,
            offset=start,
        )
        from scout_api.modules.images.repository import ProductImageRepository

        product_ids = [product.id for product in rows]
        images_by_product = ProductImageRepository(self._session).list_for_products(
            product_ids
        )
        views: list[ProductView] = []
        for product in rows:
            if not can_access_product(product, viewer):
                continue
            listings = list(product.listings or [])
            if not listings:
                listings = repo.list_listings_for_canonical(product.id)
            images = [
                to_image_view(row) for row in images_by_product.get(product.id, [])
            ]
            views.append(
                _to_product_view(
                    product, listings, images=images, session=self._session
                )
            )
        return ProductListResponse(
            items=views,
            count=len(views),
            limit=cap,
            offset=start,
            total=total,
        )

    def update_product(
        self,
        product_id: UUID,
        request: ProductUpdateRequest,
        *,
        owner: AuthenticatedPrincipal,
    ) -> ProductView:
        repo = MatchingRepository(self._session)
        canonical = repo.get_canonical(product_id)
        if canonical is None or not can_access_product(canonical, owner):
            raise RequestError(
                "Produto canônico não encontrado",
                code="PRODUCT_NOT_FOUND",
            )
        attrs = dict(request.attributes or {})
        if request.variant:
            attrs.setdefault("variant", request.variant)
        variant_key_value = None
        if request.variant is not None or attrs:
            string_attrs = {
                k: str(v)
                for k, v in {**(canonical.attributes or {}), **attrs}.items()
                if isinstance(v, str)
            }
            if request.variant:
                string_attrs["variant"] = request.variant
            variant_key_value = variant_key(string_attrs)
        repo.update_canonical(
            canonical,
            title=request.title,
            brand=request.brand,
            model=request.model,
            variant_key=variant_key_value,
            attributes=attrs or None,
        )
        self._session.flush()
        view = self.get_product(product_id, viewer=owner)
        assert view is not None
        return view

    def delete_product(
        self,
        product_id: UUID,
        *,
        owner: AuthenticatedPrincipal,
    ) -> None:
        repo = MatchingRepository(self._session)
        canonical = repo.get_canonical(product_id)
        if canonical is None:
            return
        if not can_access_product(canonical, owner):
            raise RequestError(
                "Produto canônico não encontrado",
                code="PRODUCT_NOT_FOUND",
            )
        ProductImageService(self._session).cleanup_product_images(product_id)
        repo.delete_canonical(product_id)
        self._session.flush()

    def search_products(
        self,
        *,
        viewer: AuthenticatedPrincipal,
        brand: str | None = None,
        model: str | None = None,
        variant: str | None = None,
        category: str | None = None,
        attribute_filters: dict[str, str] | None = None,
        limit: int = 50,
    ) -> ProductSearchResponse:
        """Filter canonical products by optional brand / model / variant / attrs.

        ``model`` matches the base identity (``GeForce RTX 5070`` ≡ ``rtx5070``).
        ``variant`` is optional: omitted returns every commercial implementation
        of that model; when set, only that cooler line / refinement is kept.
        Missing variant on a stored product is not treated as a conflict.
        Extra ``attribute_filters`` match keys inside ``attributes`` JSON
        (normalized when a CategoryProfile normalizer applies).
        """
        from scout_api.modules.crawler.utils.category_profiles.common import (
            normalize_attribute_value,
        )

        brand_q = brand.strip() if brand and brand.strip() else None
        model_q = model.strip() if model and model.strip() else None
        variant_q = variant.strip() if variant and variant.strip() else None
        category_q = (
            category.strip().casefold() if category and category.strip() else None
        )
        attr_filters = {
            key.casefold(): str(value).strip()
            for key, value in (attribute_filters or {}).items()
            if value is not None and str(value).strip()
        }
        if not any((brand_q, model_q, variant_q, category_q, attr_filters)):
            raise RequestError(
                "Informe brand, model, variant, category ou um filtro de atributo",
                code="INVALID_REQUEST",
            )
        cap = max(1, min(limit, 100))
        repo = MatchingRepository(self._session)
        rows = repo.search_canonical_products(
            viewer_id=viewer.id,
            is_admin=viewer.role == UserRole.ADMIN,
            brand=brand_q,
            limit=max(cap * 8, 200),
        )
        wanted_model = canonical_model_key(model_q) if model_q else None
        wanted_variant = canonical_variant_key(variant_q) if variant_q else None
        wanted_brand = normalize_brand(brand_q) if brand_q else None
        items: list[ProductView] = []
        for product in rows:
            if wanted_brand:
                stored_brand = normalize_brand(product.brand)
                if stored_brand != wanted_brand:
                    continue
            if wanted_model:
                stored_model = canonical_model_key(product.model, title=product.title)
                if stored_model != wanted_model:
                    continue
            if wanted_variant:
                if wanted_variant not in _product_variant_keys(product):
                    continue
            attrs = {
                str(k).casefold(): v
                for k, v in (product.attributes or {}).items()
                if v not in (None, "")
            }
            if category_q:
                stored_cat = str(attrs.get("category") or "").casefold()
                if stored_cat != category_q:
                    continue
            if attr_filters:
                matched = True
                for key, wanted in attr_filters.items():
                    raw = attrs.get(key)
                    if raw is None:
                        matched = False
                        break
                    left = normalize_attribute_value(key, str(raw)) or str(raw)
                    right = normalize_attribute_value(key, wanted) or wanted
                    if left.casefold() != right.casefold():
                        matched = False
                        break
                if not matched:
                    continue
            view = self.get_product(product.id, viewer=viewer)
            if view is None:
                continue
            items.append(view)
            if len(items) >= cap:
                break
        return ProductSearchResponse(items=items, count=len(items))

    @staticmethod
    def _attach_listing(
        repo: MatchingRepository,
        canonical: CanonicalProduct,
        request: ProductRegisterRequest,
        *,
        gtin: str | None,
        country: str,
    ) -> tuple[StoreListing, bool]:
        assert request.store and request.product_id
        canonical_url = str(
            request.canonical_url
            or f"scout://{request.store}/{country}/{request.product_id}"
        )
        url = str(request.url or request.canonical_url or canonical_url)
        listing, created = repo.get_or_create_listing(
            canonical=canonical,
            store=request.store,
            country=country,
            product_id=request.product_id,
            url=url,
            canonical_url=canonical_url,
            sku=request.sku,
            gtin=gtin,
            title=request.title,
            match_decision="auto_match",
            confidence=Decimal("1.0000"),
            status="active",
        )
        ProductRegistrationService._seed_offer_snapshot(repo, listing, request)
        return listing, created

    @staticmethod
    def _seed_offer_snapshot(
        repo: MatchingRepository,
        listing: StoreListing,
        request: ProductRegisterRequest,
    ) -> None:
        """Persist preview commercial fields as the first OfferSnapshot.

        Import without a snapshot leaves listings with null prices in the UI
        (``Não informado``) until ``/offers/refresh`` or ``/match`` with
        ``persist=true``. Seeding from the crawl preview closes that gap.
        """
        if repo.latest_snapshot(listing.id) is not None:
            return
        card = request.price
        pix = request.pix_price
        seed_price = card or pix or request.original_price
        if seed_price is None:
            return
        from datetime import UTC, datetime

        from scout_api.modules.crawler.models.product import ProductOffer
        from scout_api.modules.monitoring.hooks import initialize_listing_schedule

        available = True if request.available is None else bool(request.available)
        offer = ProductOffer(
            store=listing.store,
            country=listing.country,
            product_id=listing.product_id,
            sku=listing.sku,
            seller=request.seller,
            url=listing.url,
            canonical_url=listing.canonical_url,
            currency=(request.currency or "BRL").upper(),
            price=seed_price,
            original_price=request.original_price,
            pix_price=pix,
            availability="available" if available else "out_of_stock",
            available=available,
            scraped_at=datetime.now(UTC),
            metadata={"source": "product_register_preview"},
        )
        repo.append_snapshot_from_offer(listing, offer)
        repo.append_event(
            listing,
            "offer_created",
            after={"url": listing.url, "source": "product_register_preview"},
        )
        initialize_listing_schedule(listing, checked_at=offer.scraped_at)


def _to_product_view(
    product: CanonicalProduct,
    listings: list[StoreListing],
    *,
    images: list[ProductImageView] | None = None,
    session: Session | None = None,
) -> ProductView:
    # Older Product Match runs may have persisted the identity-only placeholder
    # as a listing. It is a search reference, never a commercial offer.
    listings = [
        item
        for item in listings
        if not (
            item.store.casefold() == "synthetic"
            and (
                item.url.casefold().startswith("scout://identity/")
                or item.product_id.casefold().startswith("identity:")
            )
        )
    ]
    gtins = [
        ident.value_normalized
        for ident in (product.identifiers or [])
        if ident.type == "gtin"
    ]
    if not gtins:
        gtins = [item.gtin for item in listings if item.gtin]
    seen: set[str] = set()
    unique_gtins: list[str] = []
    for value in gtins:
        if value not in seen:
            seen.add(value)
            unique_gtins.append(value)
    image_items = list(images or [])
    main_image = next((item for item in image_items if item.is_main), None)
    if main_image is None and image_items:
        main_image = image_items[0]
    return ProductView(
        id=product.id,
        title=product.title,
        brand=product.brand,
        model=product.model,
        variant_key=product.variant_key,
        attributes=dict(product.attributes or {}),
        gtins=unique_gtins,
        created_at=product.created_at,
        updated_at=product.updated_at,
        listings=[_to_listing_view(item, session=session) for item in listings],
        images=image_items,
        primary_image_url=primary_display_url(image_items),
        primary_image_status=main_image.image_status if main_image else "missing",
        primary_image_error_code=(
            main_image.image_error_code if main_image else "image_not_configured"
        ),
        primary_image_retryable=main_image.image_retryable if main_image else False,
    )


def _product_variant_keys(product: CanonicalProduct) -> set[str]:
    """Comparable commercial-variant keys stored on a canonical product."""
    keys: set[str] = set()
    attrs = product.attributes or {}
    candidates: list[object] = [
        attrs.get("edition"),
        attrs.get("variant"),
        product.variant_key,
    ]
    for raw in candidates:
        if raw in (None, ""):
            continue
        text = str(raw)
        compact = canonical_variant_key(text)
        if compact:
            keys.add(compact)
        if "=" in text:
            for part in text.split("|"):
                if "edition=" in part.casefold():
                    _, _, value = part.partition("=")
                    compact = canonical_variant_key(value)
                    if compact:
                        keys.add(compact)
    return keys


def _to_listing_view(
    listing: StoreListing,
    *,
    session: Session | None = None,
) -> ProductListingView:
    from decimal import Decimal

    from scout_api.modules.monitoring.promotion import is_promotion_commercially_active

    latest = None
    if listing.snapshots:
        latest = max(listing.snapshots, key=lambda snap: snap.scraped_at)
    payload = (latest.payload if latest is not None else {}) or {}
    pix_raw = payload.get("pix_price")
    original_raw = payload.get("original_price")
    pix_price = Decimal(str(pix_raw)) if pix_raw not in (None, "") else None
    original_price = (
        Decimal(str(original_raw)) if original_raw not in (None, "") else None
    )
    price = latest.price if latest is not None else None
    currency = latest.currency if latest is not None else None
    fx: dict[str, object] = {
        "converted_price_brl": None,
        "exchange_rate": None,
        "exchange_rate_type": None,
        "exchange_rate_status": None,
        "exchange_rate_source": None,
        "exchange_rate_updated_at": None,
    }
    if session is not None and price is not None and currency is not None:
        try:
            from scout_api.modules.exchange.enrich import attach_conversion

            fx = attach_conversion(price, currency, session=session)
        except Exception:  # noqa: BLE001
            # Secondary feature: never break product serialization.
            pass
    return ProductListingView(
        id=listing.id,
        store=listing.store,
        country=listing.country,
        product_id=listing.product_id,
        sku=listing.sku,
        gtin=listing.gtin,
        url=listing.url,
        canonical_url=listing.canonical_url,
        status=listing.status,
        title=listing.title,
        match_decision=listing.match_decision,
        confidence=listing.confidence,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
        monitoring_enabled=bool(listing.monitoring_enabled),
        last_checked_at=listing.last_checked_at,
        next_check_at=listing.next_check_at,
        last_successful_check_at=listing.last_successful_check_at,
        consecutive_failures=int(listing.consecutive_failures or 0),
        price=price,
        currency=currency,
        seller=latest.seller if latest is not None else None,
        availability=latest.availability if latest is not None else None,
        available=latest.available if latest is not None else None,
        pix_price=pix_price,
        original_price=original_price,
        promotion_status=listing.promotion_status or "none",
        promotion_expires_at=listing.promotion_expires_at,
        promotion_type=listing.promotion_type,
        promotion_price=listing.promotion_price,
        promotion_conditions=dict(listing.promotion_conditions or {}),
        promotion_commercially_active=is_promotion_commercially_active(listing),
        converted_price_brl=fx.get("converted_price_brl"),  # type: ignore[arg-type]
        exchange_rate=fx.get("exchange_rate"),  # type: ignore[arg-type]
        exchange_rate_type=fx.get("exchange_rate_type"),  # type: ignore[arg-type]
        exchange_rate_status=fx.get("exchange_rate_status"),  # type: ignore[arg-type]
        exchange_rate_source=fx.get("exchange_rate_source"),  # type: ignore[arg-type]
        exchange_rate_updated_at=fx.get("exchange_rate_updated_at"),  # type: ignore[arg-type]
    )
