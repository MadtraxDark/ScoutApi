"""Diagnose ProductIdentity + search queries for long smartphone titles."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    model_search_phrase,
    resolve_model,
)

TITLE = (
    "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
    '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
)


def main() -> None:
    item = ProductPriceItem.model_validate(
        {
            "store": "magalu",
            "country": "BR",
            "product_id": "x",
            "url": "https://example.com/p",
            "canonical_url": "https://example.com/p",
            "title": TITLE,
            "brand": "Samsung",
            "currency": "BRL",
            "price": Decimal("1"),
            "scraped_at": datetime.now(UTC),
        }
    )
    ident = identity_from_price_item(item)
    queries = build_search_queries(ident)
    series = model_search_phrase(model=ident.model, title=ident.title)
    print("=== IDENTITY ===")
    print("brand:", ident.brand)
    print("model:", ident.model)
    print("resolve_model:", resolve_model(None, TITLE))
    print("series phrase:", series)
    print("gtin:", ident.gtin)
    print("mpn:", ident.mpn)
    print("variant_attrs:", dict(ident.variant_attrs))
    print("title_normalized:", ident.title_normalized)
    print("raw_title_length:", len(TITLE))
    print("raw_title_tokens:", len(TITLE.split()))
    print(
        "norm_title_tokens:",
        len(ident.title_normalized.split()) if ident.title_normalized else 0,
    )
    print("=== QUERIES ===")
    for i, q in enumerate(queries, 1):
        print(f"{i}. [{len(q)} chars / {len(q.split())} toks] {q}")


if __name__ == "__main__":
    main()
