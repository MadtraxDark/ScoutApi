import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
REF = "https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/p/240604800/ga/gap5/?seller_id=magazineluiza"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
svc = ProductMatchService(scrape_service=scrape)
search = StoreSearchService()

# Best Buy: search only (avoid multi-PDP hang on polluted SERP)
print("BB_SEARCH", flush=True)
t0 = time.time()
try:
    hits = search.search("bestbuy", "playstation 5 digital edition slim white", limit=5)
    print(
        json.dumps(
            {
                "secs": round(time.time() - t0, 1),
                "count": len(hits),
                "sample": [{"title": (h.title or "")[:80], "url": h.url} for h in hits],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "secs": round(time.time() - t0, 1),
                "err": type(e).__name__,
                "code": getattr(e, "code", None),
                "msg": str(e)[:160],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

for store in ["amazon_us", "shopee"]:
    print("MATCH", store, flush=True)
    t0 = time.time()
    resp = svc.match(
        MatchRequest(
            reference_url=REF,
            stores=[store],
            persist=False,
            include_review=True,
            max_candidates_per_store=3,
        )
    )
    print(
        json.dumps(
            {
                "store": store,
                "secs": round(time.time() - t0, 1),
                "matches": [
                    {
                        "decision": h.decision,
                        "title": (h.product.title or "")[:110],
                        "price": str(h.product.price),
                        "currency": h.product.currency,
                        "url": h.product.url,
                        "reasons": [r.code for r in h.reasons],
                    }
                    for h in resp.matches
                ],
                "unmatched": resp.unmatched_stores,
                "errors": [
                    {"code": e.code, "message": e.message[:160]} for e in resp.errors
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
