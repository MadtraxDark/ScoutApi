"""Trace amazon_br match decisions for each SERP candidate."""

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


def main() -> None:
    scrape = ProductScrapeService()
    ref = scrape.scrape(REF, include_images=False)
    ref_i = identity_from_price_item(ref)
    engine = MatchingEngine()
    search = StoreSearchService()
    query = build_search_queries(ref_i)[0]
    hits = search.search("amazon_br", query, limit=5)
    rows = []
    for hit in hits:
        row: dict[str, object] = {
            "serp_title": hit.title,
            "product_id": hit.product_id,
            "query": query,
        }
        try:
            product = scrape.scrape(hit.url, include_images=False)
        except Exception as exc:  # noqa: BLE001 — diagnostic
            row["scrape"] = f"FAIL {type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        cand_i = identity_from_price_item(product)
        score = engine.score(ref_i, cand_i)
        row["scrape_title"] = product.title
        row["attrs"] = cand_i.variant_attrs
        row["decision"] = score.decision
        row["reasons"] = [r.code for r in score.reasons]
        rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
