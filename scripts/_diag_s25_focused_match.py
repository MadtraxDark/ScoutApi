"""Focused live match: S25 Ultra identity → kabum, magalu, amazon_br."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


TITLE = (
    "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
    '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
)
STORES = ["kabum", "magazineluiza", "amazon_br", "shoppingchina"]


def main() -> None:
    ref = identity_reference_item(TITLE, brand="Samsung", category="smartphone")
    identity = identity_from_price_item(ref)
    print("queries:", build_search_queries(identity)[:4])
    scrape = ProductScrapeService(fetcher=get_shared_html_fetcher())
    search = StoreSearchService(fetcher=get_shared_html_fetcher())
    svc = ProductMatchService(scrape_service=scrape, search_service=search)
    t0 = time.perf_counter()
    resp = svc.match_from_item(
        ref,
        stores=STORES,
        persist=False,
        include_review=True,
        max_candidates_per_store=3,
        clear_reference_price=True,
    )
    elapsed = time.perf_counter() - t0
    out = {
        "elapsed_s": round(elapsed, 1),
        "queries": build_search_queries(identity),
        "matches": [
            {
                "store": h.store,
                "decision": h.decision,
                "confidence": str(h.confidence),
                "query": h.search_query,
                "title": (h.product.title if h.product else None),
                "url": (h.product.url if h.product else None),
                "reasons": [r.model_dump() for r in h.reasons],
            }
            for h in resp.matches
        ],
        "unmatched": list(resp.unmatched_stores),
        "errors": [e.model_dump() for e in resp.errors],
    }
    path = (
        ROOT
        / "data"
        / "live-match-reports"
        / f"s25_focused_match_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
