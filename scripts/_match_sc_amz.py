import json

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

get_shared_html_fetcher.cache_clear()
REF = "https://www.magazineluiza.com.br/apple-iphone-16-128gb-preto-61-48mp-ios-5g/p/238803400/te/ip16/?seller_id=magazineluiza"
svc = ProductMatchService(scrape_service=ProductScrapeService())
resp = svc.match(
    MatchRequest(
        reference_url=REF,
        stores=["shoppingchina", "amazon_us"],
        persist=False,
        include_review=True,
        max_candidates_per_store=5,
    )
)
print(
    json.dumps(
        {
            "matches": [
                {
                    "store": h.store,
                    "decision": h.decision,
                    "title": h.product.title,
                    "url": h.product.url,
                    "query": h.search_query,
                    "reasons": [r.code for r in h.reasons],
                }
                for h in resp.matches
            ],
            "unmatched": resp.unmatched_stores,
            "errors": [
                {"store": e.store, "code": e.code, "message": e.message[:160]}
                for e in resp.errors
            ],
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
