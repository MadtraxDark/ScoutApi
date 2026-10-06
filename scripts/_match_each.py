import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

get_shared_html_fetcher.cache_clear()
REF = "https://www.magazineluiza.com.br/apple-iphone-16-128gb-preto-61-48mp-ios-5g/p/238803400/te/ip16/?seller_id=magazineluiza"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
svc = ProductMatchService(scrape_service=scrape)
for stores in (["bestbuy"], ["nissei"], ["amazon_us"], ["shopee"]):
    print("MATCH", stores, flush=True)
    t0 = time.time()
    try:
        resp = svc.match(
            MatchRequest(
                reference_url=REF,
                stores=stores,
                persist=False,
                include_review=True,
                max_candidates_per_store=3,
            )
        )
        print(
            json.dumps(
                {
                    "stores": stores,
                    "secs": round(time.time() - t0, 1),
                    "matches": [
                        {
                            "store": h.store,
                            "decision": h.decision,
                            "title": (h.product.title or "")[:100],
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
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "stores": stores,
                    "secs": round(time.time() - t0, 1),
                    "fatal": type(e).__name__,
                    "msg": str(e)[:250],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
