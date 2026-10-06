"""Scrape Amazon BR Preto ASIN and score vs Magalu ref."""

from __future__ import annotations

import json

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

REF = (
    "https://www.magazineluiza.com.br/apple-iphone-16-128gb-preto-61-48mp-ios-5g/"
    "p/238803400/te/ip16/?seller_id=magazineluiza"
)
AMZ = "https://www.amazon.com.br/dp/B0DJFTJ6LX"


def main() -> None:
    scrape = ProductScrapeService()
    ref = scrape.scrape(REF, include_images=False)
    amz = scrape.scrape(AMZ, include_images=False)
    ref_i = identity_from_price_item(ref)
    amz_i = identity_from_price_item(amz)
    score = MatchingEngine().score(ref_i, amz_i)
    search = StoreSearchService()
    hits = search.search("amazon_br", "apple iphone 16 128gb preto", limit=5)
    ranked = [{"title": h.title, "product_id": h.product_id} for h in hits]
    print(
        json.dumps(
            {
                "ref_attrs": ref_i.variant_attrs,
                "amz": {
                    "title": amz.title,
                    "variant": amz.variant,
                    "attrs": amz_i.variant_attrs,
                    "brand": amz.brand,
                    "model": amz.model,
                },
                "score": {
                    "decision": score.decision,
                    "confidence": str(score.confidence),
                    "reasons": [
                        {"code": r.code, "detail": r.detail} for r in score.reasons
                    ],
                },
                "queries_head": build_search_queries(ref_i)[:5],
                "serp_top5": ranked,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
