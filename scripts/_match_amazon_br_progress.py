"""Match amazon_br with SSE-like progress printing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


TITLE = "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto"


def main() -> None:
    ref = identity_reference_item(TITLE, brand="Samsung", category="smartphone")
    fetcher = get_shared_html_fetcher()
    svc = ProductMatchService(
        scrape_service=ProductScrapeService(fetcher=fetcher),
        search_service=StoreSearchService(fetcher=fetcher),
    )

    def on_progress(event) -> None:
        print(
            event.type,
            event.store,
            event.display_name,
            event.status,
            event.message,
            event.candidate_count,
        )

    resp = svc.match_from_item(
        ref,
        stores=["amazon_br"],
        persist=False,
        include_review=True,
        max_candidates_per_store=5,
        clear_reference_price=True,
        on_progress=on_progress,
    )
    print("matches", len(resp.matches), resp.unmatched_stores, resp.errors)


if __name__ == "__main__":
    main()
