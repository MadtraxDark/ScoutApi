"""Trace amazon_br candidate decisions for S25 Ultra."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
    serp_candidate_text,
)
from scout_api.modules.matching.product_match_service import _serp_title_reject_reason
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


TITLE = (
    'Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto 6,9" 12GB RAM'
)


def main() -> None:
    ref = identity_reference_item(TITLE, brand="Samsung", category="smartphone")
    ident = replace(identity_from_price_item(ref), price=None)
    query = build_search_queries(ident)[0]
    fetcher = get_shared_html_fetcher()
    search = StoreSearchService(fetcher=fetcher)
    scrape = ProductScrapeService(fetcher=fetcher)
    engine = MatchingEngine()
    print("query", query)
    cands = search.search("amazon_br", query, limit=5)
    print("n", len(cands))
    for index, candidate in enumerate(cands):
        text = serp_candidate_text(candidate)
        reject = _serp_title_reject_reason(ident, title=text)
        print(index, candidate.product_id, reject, (text or "")[:80])
        if reject:
            continue
        product = scrape.scrape(candidate.url)
        score = engine.score(ident, identity_from_price_item(product))
        print(
            "  SCORE",
            score.decision,
            score.confidence,
            [(reason.code, reason.detail) for reason in score.reasons],
        )
        print("  title", (product.title or "")[:100])
        print("  pid", product.product_id)


if __name__ == "__main__":
    main()
