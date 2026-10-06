import json

from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
s = StoreSearchService()
for q in [
    "sony playstation 5 digital 825gb",
    "playstation 5 digital 825",
    "playstation 5 CFI-2115B",
    "console sony playstation 5 digital",
]:
    hits = s.search("shoppingchina", q, limit=5)
    print(
        json.dumps(
            {
                "q": q,
                "count": len(hits),
                "sample": [(h.title or "")[:80] for h in hits],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
