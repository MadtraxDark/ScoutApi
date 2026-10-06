"""Diagnose ProductIdentity + queries for long motherboard titles."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.utils.category_profiles.extra_parsers import (
    parse_motherboard,
)
from scout_api.modules.crawler.utils.product_attributes import (
    detect_product_category,
    resolve_product_identity,
)
from scout_api.modules.crawler.utils.product_identity import parse_title_identity
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    extract_all_mpn_forms,
    identity_from_price_item,
    model_search_phrase,
    models_compatible,
    resolve_model,
    token_set_ratio,
)

TITLE = (
    "Placa Mae Asus Tuf Gaming B650M-E WIFI, DDR5, Socket AMD AM5, "
    "M-ATX, Chipset AMD B650, TUF-GAMING-B650M-E-WIFI"
)
STORED_MODEL = "TUF Gaming B650M-E Socket AMD M-atx"
CANDIDATE_SHORT = "ASUS TUF GAMING B650M-E WIFI"
CANDIDATE_KABUM = (
    "Placa-Mãe ASUS TUF Gaming B650M-E, WIFI, AMD "
    "AM5, B650, DDR5, Preto - 90MB1FV0-M0EAY0"
)
CANDIDATE_PLUS = "ASUS TUF GAMING B650M-PLUS WIFI"
CANDIDATE_A = "ASUS PRIME B650M-A WIFI"


def _item(
    title: str,
    *,
    brand: str | None = None,
    model: str | None = None,
    variant: str | None = None,
    gtin: str | None = "4711387222041",
) -> ProductPriceItem:
    payload: dict = {
        "store": "pichau",
        "country": "BR",
        "product_id": "x",
        "url": "https://example.com/p",
        "canonical_url": "https://example.com/p",
        "title": title,
        "brand": brand,
        "model": model,
        "variant": variant,
        "currency": "BRL",
        "price": Decimal("1"),
        "scraped_at": datetime.now(UTC),
    }
    if gtin:
        payload["gtin"] = gtin
    return ProductPriceItem.model_validate(payload)


def main() -> None:
    print("=== CATEGORY / PARSE ===")
    print("detect_product_category:", detect_product_category(TITLE))
    print("parse_motherboard:", parse_motherboard(TITLE, "motherboard"))
    print("parse_title_identity:", parse_title_identity(TITLE, category="motherboard"))
    bundle = resolve_product_identity(title=TITLE, category="motherboard")
    print("resolve brand:", bundle.value("brand"))
    print("resolve model:", bundle.value("model"))
    for key in (
        "chipset",
        "socket",
        "memory_type",
        "form_factor",
        "wifi",
        "mpn",
        "variant",
    ):
        print(f"  attr {key}:", bundle.value(key))

    print("\n=== MPN EXTRACTION ===")
    print(
        "forms:", extract_all_mpn_forms(TITLE, STORED_MODEL, "TUF-GAMING-B650M-E-WIFI")
    )

    print("\n=== IDENTITY (stored model + variant=WiFi like canonical) ===")
    ident = identity_from_price_item(
        _item(TITLE, brand="Asus", model=STORED_MODEL, variant="WiFi")
    )
    print("brand:", ident.brand)
    print("model:", ident.model)
    print("resolve_model:", resolve_model(STORED_MODEL, TITLE))
    print("series phrase:", model_search_phrase(model=ident.model, title=ident.title))
    print("gtin:", ident.gtin)
    print("mpn:", ident.mpn, "display:", ident.mpn_display)
    print("variant_attrs:", dict(ident.variant_attrs))
    print("title_normalized:", ident.title_normalized)
    queries = build_search_queries(ident)
    print("\n=== QUERIES (with WiFi-as-variant bug path) ===")
    for i, q in enumerate(queries, 1):
        print(f"{i}. [{len(q.split())} toks] {q}")

    print("\n=== IDENTITY (title only) ===")
    ident2 = identity_from_price_item(_item(TITLE, brand="Asus"))
    print("model:", ident2.model)
    print("series:", model_search_phrase(model=ident2.model, title=ident2.title))
    print("mpn:", ident2.mpn, ident2.mpn_display)
    print("variant_attrs:", dict(ident2.variant_attrs))
    for i, q in enumerate(build_search_queries(ident2), 1):
        print(f"{i}. {q}")

    print("\n=== identity_reference_item path ===")
    from scout_api.modules.matching.identity import identity_reference_item

    ref_item = identity_reference_item(
        TITLE, brand="Asus", model=STORED_MODEL, variant="WiFi", category="motherboard"
    )
    print("ref_item.model:", ref_item.model)
    print("ref_item.variant:", ref_item.variant)
    print("ref_item.brand:", ref_item.brand)
    ident3 = identity_from_price_item(ref_item)
    print("ident3.model:", ident3.model)
    print("ident3.variant_attrs:", dict(ident3.variant_attrs))
    print("ident3.mpn:", ident3.mpn)
    for i, q in enumerate(build_search_queries(ident3), 1):
        print(f"{i}. {q}")

    engine = MatchingEngine()
    print("\n=== MATCHER ===")
    for label, cand_title, cand_variant in (
        ("SHORT_OK", CANDIDATE_SHORT, None),
        ("KABUM_OK", CANDIDATE_KABUM, None),
        ("KABUM_COLOR", CANDIDATE_KABUM, "Preto"),
        ("PLUS_BAD", CANDIDATE_PLUS, None),
        ("A_BAD", CANDIDATE_A, None),
    ):
        cand = identity_from_price_item(
            _item(cand_title, brand="ASUS", variant=cand_variant, gtin=None)
        )
        score = engine.score(ident, cand)
        models_ok = models_compatible(
            ident.model, cand.model, left_title=ident.title, right_title=cand.title
        )
        print(
            f"{label}: decision={score.decision} conf={score.confidence} "
            f"model={cand.model!r} mpn={cand.mpn} "
            f"vars={cand.variant_attrs} "
            f"title_sim={token_set_ratio(ident.title, cand.title):.3f} "
            f"models_ok={models_ok}"
        )
        for r in score.reasons:
            print(f"  - {r.code}: {r.detail}")


if __name__ == "__main__":
    main()
