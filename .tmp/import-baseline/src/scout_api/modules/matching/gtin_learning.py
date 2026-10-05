"""Trusted GTIN discovery during cross-store matching."""

from __future__ import annotations

from dataclasses import dataclass

from scout_api.modules.matching.identity import ProductIdentity, normalize_gtin
from scout_api.modules.matching.schemas import MatchHit


@dataclass(frozen=True)
class TrustedGtin:
    """GTIN accepted only under precision-first rules."""

    gtin: str
    source: str
    """Where it came from, e.g. ``reference`` or ``auto_match:kabum``."""


def _brand_compatible(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    return a == b or a in b or b in a


def resolve_trusted_gtin(
    reference: ProductIdentity,
    matches: list[MatchHit],
) -> TrustedGtin | None:
    """Pick a single validated GTIN safe enough to reuse in future searches.

    Safe when:
    - check-digit validation passes (``normalize_gtin``);
    - from the reference itself, or from an ``auto_match`` hit only;
    - all trusted observations agree on the same normalized value;
    - brand is compatible when both sides have a brand.
    """
    observations: list[TrustedGtin] = []

    ref_gtin = normalize_gtin(reference.gtin)
    if ref_gtin:
        observations.append(TrustedGtin(gtin=ref_gtin, source="reference"))

    for hit in matches:
        if hit.decision != "auto_match":
            continue
        cand = normalize_gtin(hit.product.gtin)
        if not cand:
            continue
        cand_brand = None
        if hit.product.brand:
            from scout_api.modules.matching.identity import normalize_brand

            cand_brand = normalize_brand(hit.product.brand)
        if not _brand_compatible(reference.brand, cand_brand):
            continue
        observations.append(TrustedGtin(gtin=cand, source=f"auto_match:{hit.store}"))

    if not observations:
        return None

    unique = {obs.gtin for obs in observations}
    if len(unique) != 1:
        # Conflicting barcodes across auto-matches → do not learn.
        return None

    gtin = next(iter(unique))
    # Prefer documenting a non-reference source when we learned it from a match.
    learned = next(
        (obs for obs in observations if obs.source != "reference"),
        observations[0],
    )
    return TrustedGtin(gtin=gtin, source=learned.source)


def identity_with_gtin(identity: ProductIdentity, gtin: str) -> ProductIdentity:
    """Return a copy of ``identity`` carrying a trusted GTIN."""
    return ProductIdentity(
        gtin=gtin,
        brand=identity.brand,
        model=identity.model,
        title=identity.title,
        title_normalized=identity.title_normalized,
        variant_attrs=dict(identity.variant_attrs),
        store=identity.store,
        product_id=identity.product_id,
        price=identity.price,
        currency=identity.currency,
        mpn=identity.mpn,
        mpn_display=identity.mpn_display,
        mpn_aliases=identity.mpn_aliases,
    )
