"""Resolve a registration request to an existing canonical product (or none).

Order of evidence (strongest first):

1. listing of the **same store** (canonical_url / product_id / sku);
2. GTIN shared across stores;
3. explicit user confirmation (``target_product_id``);
4. product identity (brand + model key gate, then ``MatchingEngine.score``),
   the same scoring used by Product Match, so a product sold by another store
   under a different title becomes a new ``StoreListing`` of the existing
   ``CanonicalProduct`` instead of a duplicate canonical.

The resolver only reads (plus an optional transaction-scoped advisory lock).
It never creates rows; ``ProductRegistrationService`` acts on the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.utils.product_identity import canonical_model_key
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    ProductIdentity,
    identity_from_price_item,
    identity_reference_item,
    normalize_brand,
    normalize_variant_value,
)
from scout_api.modules.matching.models import CanonicalProduct, StoreListing
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import (
    ProductMatchedBy,
    ProductRegisterRequest,
    ProductResolutionKind,
)

logger = logging.getLogger(__name__)

IDENTITY_LOCK_PREFIX = "canonical-identity:"


def can_access_product(
    product: CanonicalProduct, principal: AuthenticatedPrincipal
) -> bool:
    if product.owner_user_id is None:
        return True
    if principal.role == UserRole.ADMIN:
        return True
    return product.owner_user_id == principal.id


@dataclass(frozen=True)
class CanonicalResolution:
    kind: ProductResolutionKind
    product: CanonicalProduct | None = None
    listing: StoreListing | None = None
    matched_by: ProductMatchedBy | None = None
    confidence: Decimal | None = None


@dataclass(frozen=True)
class _ScoredCandidate:
    product: CanonicalProduct
    decision: str
    confidence: Decimal


class ProductIdentityReviewRequired(RequestError):
    """Registration needs a human decision before attaching or creating."""

    def __init__(self, candidate: CanonicalProduct, confidence: Decimal | None):
        super().__init__(
            "Produto possivelmente já cadastrado. Confirme se a oferta pertence "
            "ao produto existente ou se é um produto diferente.",
            code="PRODUCT_IDENTITY_REVIEW_REQUIRED",
        )
        self.candidate = candidate
        self.confidence = confidence


# Structured variant keys read by ``identity_from_price_item`` from metadata.
_VARIANT_METADATA_KEYS = (
    "color",
    "storage",
    "size",
    "capacity",
    "ram",
    "vram",
    "edition",
    "network_lock",
)


def _identity(
    title: str,
    *,
    brand: str | None,
    model: str | None,
    variant: str | None,
    category: str | None,
    attributes: dict[str, Any],
) -> ProductIdentity:
    """Build identity like Product Match references, keeping variant attrs.

    Structured attributes (edition, color, storage...) are passed through
    metadata so a short title never erases a known variant.
    """
    item = identity_reference_item(
        title, brand=brand, model=model, variant=variant, category=category
    )
    structured = {
        key: str(attributes[key]).strip()
        for key in _VARIANT_METADATA_KEYS
        if attributes.get(key) is not None and str(attributes[key]).strip()
    }
    if structured:
        meta = dict(item.metadata or {})
        meta.update(structured)
        item = item.model_copy(update={"metadata": meta})
    identity = identity_from_price_item(item)
    # Some lines (GPU edition) are only derived from the title; keep the
    # explicit attribute when the title does not carry it. Title-derived
    # values always win.
    missing = {
        key: normalize_variant_value(key, value)
        for key, value in structured.items()
        if key not in identity.variant_attrs
    }
    missing = {key: value for key, value in missing.items() if value}
    if missing:
        identity = replace(
            identity, variant_attrs={**identity.variant_attrs, **missing}
        )
    return replace(identity, price=None)


def request_identity(
    request: ProductRegisterRequest,
    *,
    gtin: str | None,
    category: str | None,
) -> ProductIdentity:
    """Identity of the incoming listing."""
    identity = _identity(
        request.title,
        brand=request.brand,
        model=request.model,
        variant=request.variant,
        category=category,
        attributes=dict(request.attributes or {}),
    )
    return replace(identity, gtin=gtin, store=request.store)


def canonical_identity(product: CanonicalProduct) -> ProductIdentity:
    """Identity of a stored canonical (same builder as match_run_worker)."""
    attrs: dict[str, Any] = dict(product.attributes or {})
    variant = attrs.get("variant")
    category = attrs.get("category")
    identity = _identity(
        product.title or "",
        brand=product.brand,
        model=product.model,
        variant=str(variant) if variant else None,
        category=str(category) if category else None,
        attributes=attrs,
    )
    gtins = [
        ident.value_normalized
        for ident in (product.identifiers or [])
        if ident.type == "gtin"
    ]
    return replace(identity, price=None, gtin=gtins[0] if gtins else None)


def identity_lock_key(identity: ProductIdentity, gtin: str | None) -> str | None:
    """Stable key so equivalent products from different stores serialize.

    Brand + model key is preferred (covers GTIN and non-GTIN requests of the
    same product); GTIN is the fallback when identity is too weak.
    """
    model_key = canonical_model_key(identity.model, title=identity.title)
    if identity.brand and model_key:
        return f"{IDENTITY_LOCK_PREFIX}{identity.brand}|{model_key}"
    if gtin:
        return f"{IDENTITY_LOCK_PREFIX}gtin|{gtin}"
    return None


class CanonicalIdentityResolver:
    def __init__(
        self,
        session: Session,
        *,
        engine: MatchingEngine | None = None,
        candidate_limit: int = 5000,
    ) -> None:
        self._session = session
        self._repo = MatchingRepository(session)
        self._engine = engine or MatchingEngine()
        self._candidate_limit = candidate_limit

    def resolve(
        self,
        request: ProductRegisterRequest,
        *,
        gtin: str | None,
        country: str | None,
        category: str | None,
        viewer: AuthenticatedPrincipal,
        lock: bool = False,
    ) -> CanonicalResolution:
        repo = self._repo
        identity = request_identity(request, gtin=gtin, category=category)
        if lock:
            # Fixed order (store listing, then canonical identity) avoids
            # deadlocks between concurrent registrations.
            if request.store and request.product_id and country:
                repo.acquire_store_identity_lock(
                    request.store, country, request.product_id
                )
            key = identity_lock_key(identity, gtin)
            if key is not None:
                repo.acquire_identity_lock(key)

        # 1. Same-store listing.
        if request.store and request.product_id and country:
            listing = repo.find_listing_by_store_identity(
                store=request.store,
                country=country,
                product_id=request.product_id,
                sku=request.sku,
                canonical_url=(
                    str(request.canonical_url) if request.canonical_url else None
                ),
            )
            if listing is not None:
                product = repo.get_canonical(listing.canonical_product_id)
                if product is None:
                    raise RequestError(
                        "Listing órfão sem produto canônico",
                        code="DATABASE_ERROR",
                    )
                self._require_access(product, viewer)
                return CanonicalResolution(
                    kind="existing_listing",
                    product=product,
                    listing=listing,
                    matched_by="store_identity",
                )

        # 2. GTIN.
        gtin_product = repo.find_canonical_by_gtin(gtin) if gtin else None
        if gtin_product is not None:
            self._require_access(gtin_product, viewer)
            if (
                request.target_product_id is not None
                and request.target_product_id != gtin_product.id
            ):
                raise RequestError(
                    "O GTIN informado pertence a outro produto cadastrado",
                    code="PRODUCT_IDENTITY_CONFLICT",
                )
            return CanonicalResolution(
                kind="existing_product_new_listing",
                product=gtin_product,
                matched_by="gtin",
            )

        # 3. Explicit user confirmation.
        if request.target_product_id is not None:
            target = repo.get_canonical(request.target_product_id)
            if target is None or not can_access_product(target, viewer):
                raise RequestError(
                    "Produto canônico não encontrado",
                    code="PRODUCT_NOT_FOUND",
                )
            return CanonicalResolution(
                kind="existing_product_new_listing",
                product=target,
                matched_by="user_confirmed",
            )

        if request.create_new_product:
            return CanonicalResolution(kind="new_product")

        # 4. Identity match.
        best = self._best_identity_candidate(identity, request, country, viewer)
        if best is None:
            return CanonicalResolution(kind="new_product")
        kind: ProductResolutionKind = (
            "existing_product_new_listing"
            if best.decision == "auto_match"
            else "needs_review"
        )
        return CanonicalResolution(
            kind=kind,
            product=best.product,
            matched_by="identity",
            confidence=best.confidence,
        )

    @staticmethod
    def _require_access(
        product: CanonicalProduct, viewer: AuthenticatedPrincipal
    ) -> None:
        if not can_access_product(product, viewer):
            raise RequestError(
                "Produto existente pertencente a outro usuário",
                code="FORBIDDEN",
            )

    def _best_identity_candidate(
        self,
        identity: ProductIdentity,
        request: ProductRegisterRequest,
        country: str | None,
        viewer: AuthenticatedPrincipal,
    ) -> _ScoredCandidate | None:
        wanted_model = canonical_model_key(identity.model, title=identity.title)
        if not identity.brand or not wanted_model:
            # Without brand + model, title similarity alone is never enough
            # to merge products (precision first).
            return None
        rows = self._repo.list_identity_candidates(
            viewer_id=viewer.id,
            is_admin=viewer.role == UserRole.ADMIN,
            limit=self._candidate_limit,
        )
        gated: list[CanonicalProduct] = []
        for product in rows:
            stored_brand = normalize_brand(product.brand)
            if stored_brand is not None and stored_brand != identity.brand:
                continue
            stored_model = canonical_model_key(product.model, title=product.title)
            if stored_model != wanted_model:
                continue
            gated.append(product)
        if not gated:
            return None

        stores_by_product = self._repo.listing_stores_by_canonical(
            [product.id for product in gated]
        )
        same_store = (request.store, country) if request.store and country else None
        scored: list[_ScoredCandidate] = []
        for product in gated:
            reference = canonical_identity(product)
            if reference.brand and reference.brand != identity.brand:
                continue
            score = self._engine.score(reference, identity)
            if score.decision == "reject":
                continue
            decision = score.decision
            # Another listing of the same store on this canonical suggests a
            # different variant/product: never attach automatically.
            if (
                decision == "auto_match"
                and same_store is not None
                and same_store in stores_by_product.get(product.id, set())
            ):
                decision = "review"
            scored.append(_ScoredCandidate(product, decision, score.confidence))
        if not scored:
            return None

        scored.sort(
            key=lambda c: (c.decision == "auto_match", c.confidence), reverse=True
        )
        best = scored[0]
        if best.decision == "auto_match" and len(scored) > 1:
            runner_up = scored[1]
            if (
                runner_up.decision == "auto_match"
                and runner_up.confidence == best.confidence
            ):
                # Two equally strong canonicals (pre-existing duplicates):
                # ask instead of guessing.
                best = replace(best, decision="review")
        logger.info(
            "product_identity_resolution brand=%s model_key=%s candidates=%s "
            "best_product=%s decision=%s confidence=%s",
            identity.brand,
            wanted_model,
            len(scored),
            best.product.id,
            best.decision,
            best.confidence,
        )
        return best
