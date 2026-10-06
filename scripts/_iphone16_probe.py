"""Probe iPhone 16 identity/scoring (host venv)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)


def item(**kw: object) -> ProductPriceItem:
    base: dict[str, object] = {
        "store": "magazineluiza",
        "country": "BR",
        "product_id": "238803400",
        "url": "https://www.magazineluiza.com.br/p/238803400/",
        "canonical_url": "https://www.magazineluiza.com.br/p/238803400/",
        "currency": "BRL",
        "price": Decimal("4500"),
        "scraped_at": datetime(2026, 9, 19, tzinfo=UTC),
        "title": "x",
    }
    base.update(kw)
    return ProductPriceItem.model_validate(base)


def main() -> None:
    ref = identity_from_price_item(
        item(
            title='Apple iPhone 16 128GB Preto 6,1" 48MP iOS 5G',
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
        )
    )
    print("REF", ref.model, ref.variant_attrs, ref.brand)
    print("QUERIES", build_search_queries(ref)[:6])

    amazon = identity_from_price_item(
        item(
            store="amazon",
            product_id="B0DJFTJ6LX",
            title="Apple iPhone 16 (128 GB) - Preto",
            brand="Apple",
            model="iPhone 16",
            url="https://www.amazon.com.br/dp/B0DJFTJ6LX",
            canonical_url="https://www.amazon.com.br/dp/B0DJFTJ6LX",
            metadata={"color": "Preto", "storage": "128 GB"},
        )
    )
    eng = MatchingEngine()
    s = eng.score(ref, amazon)
    print(
        "AMZ",
        amazon.variant_attrs,
        s.decision,
        s.confidence,
        [r.code for r in s.reasons],
    )

    pro = identity_from_price_item(
        item(
            title="Apple iPhone 16 Pro 128GB Preto",
            brand="Apple",
            model="iPhone 16 Pro",
            variant="Preto",
        )
    )
    s = eng.score(ref, pro)
    print("PRO", s.decision, [r.detail for r in s.reasons])

    gb256 = identity_from_price_item(
        item(
            title="Apple iPhone 16 256GB Preto",
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
            metadata={"storage": "256gb"},
        )
    )
    s = eng.score(ref, gb256)
    print("256", s.decision, [r.code for r in s.reasons])

    white = identity_from_price_item(
        item(
            title="Apple iPhone 16 128GB Branco",
            brand="Apple",
            model="iPhone 16",
            variant="Branco",
        )
    )
    s = eng.score(ref, white)
    print("WHITE", white.variant_attrs, s.decision, [r.code for r in s.reasons])

    black_en = identity_from_price_item(
        item(
            store="bestbuy",
            product_id="6507508",
            title="Apple - iPhone 16 128GB - Unlocked - Black",
            brand="Apple",
            model="iPhone 16",
            url="https://www.bestbuy.com/site/x/6507508.p",
            canonical_url="https://www.bestbuy.com/site/x/6507508.p",
            metadata={"color": "Black", "storage": "128GB"},
        )
    )
    s = eng.score(ref, black_en)
    print("BB", black_en.variant_attrs, s.decision, s.confidence)


if __name__ == "__main__":
    main()
