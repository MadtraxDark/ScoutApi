import json
import time

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
REF = "https://www.magazineluiza.com.br/apple-iphone-16-128gb-preto-61-48mp-ios-5g/p/238803400/te/ip16/?seller_id=magazineluiza"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
print("SCRAPE_REF", flush=True)
ref = scrape.scrape(REF, include_images=False)
ident = identity_from_price_item(ref)
queries = build_search_queries(ident)[:2]
print("QUERIES", queries, flush=True)
search = StoreSearchService()
for store in ["shopee", "bestbuy", "nissei", "amazon_us"]:
    print("SEARCH", store, flush=True)
    t0 = time.time()
    try:
        hits = search.search(store, queries[0], limit=5)
        print(
            json.dumps(
                {
                    "store": store,
                    "secs": round(time.time() - t0, 1),
                    "count": len(hits),
                    "titles": [(h.title or "")[:80] for h in hits[:5]],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except RequestError as e:
        print(
            json.dumps(
                {
                    "store": store,
                    "secs": round(time.time() - t0, 1),
                    "error": e.code,
                    "msg": str(e)[:200],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "store": store,
                    "secs": round(time.time() - t0, 1),
                    "error": type(e).__name__,
                    "msg": str(e)[:200],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
print("MATCH_START", flush=True)
t0 = time.time()
svc = ProductMatchService(scrape_service=scrape)
resp = svc.match(
    MatchRequest(
        reference_url=REF,
        stores=["shopee", "bestbuy", "nissei", "amazon_us"],
        persist=False,
        include_review=True,
        max_candidates_per_store=5,
    )
)
print(
    json.dumps(
        {
            "secs": round(time.time() - t0, 1),
            "matches": [
                {
                    "store": h.store,
                    "decision": h.decision,
                    "title": (h.product.title or "")[:90],
                    "url": h.product.url,
                    "reasons": [r.code for r in h.reasons],
                }
                for h in resp.matches
            ],
            "unmatched": resp.unmatched_stores,
            "errors": [
                {"store": e.store, "code": e.code, "message": e.message[:180]}
                for e in resp.errors
            ],
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
