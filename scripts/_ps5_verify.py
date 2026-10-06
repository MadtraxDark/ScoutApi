import json
import time

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
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
sc = svc.scrape(
    "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    include_images=False,
)
idn = identity_from_price_item(sc)
print("QUERIES", build_search_queries(idn)[:8], flush=True)

# Match SC -> amazon_us + kabum
req = MatchRequest(
    reference_url=sc.canonical_url or sc.url,
    stores=["amazon_us", "kabum"],
    persist=False,
)
r = ProductMatchService(scrape_service=svc).match(req)
print(
    json.dumps(
        {
            "ref": sc.title,
            "matches": [
                {
                    "store": m.store,
                    "decision": m.decision,
                    "confidence": str(m.confidence),
                    "product_title": m.product.title if m.product else None,
                    "reasons": [f"{x.code}:{x.detail}" for x in m.reasons][:8],
                    "query": m.search_query,
                }
                for m in r.matches
            ],
            "unmatched": r.unmatched_stores,
            "errors": r.errors,
        },
        ensure_ascii=False,
        default=str,
    )[:4500],
    flush=True,
)

# Score SC digital vs Amazon Digital-ish listing (if we can find ASIN via search)
ss = StoreSearchService(html_fetcher=get_shared_html_fetcher())
hits = ss.search("amazon_us", "playstation 5 digital edition 825GB")
print("AMZ_SERP", [{"title": h.title, "url": h.url} for h in hits[:5]], flush=True)

# BB search + scrape
t0 = time.time()
bb_hits = ss.search("bestbuy", "playstation 5 slim digital edition")
print(
    json.dumps(
        {
            "bb_search": True,
            "secs": round(time.time() - t0, 1),
            "n": len(bb_hits),
            "top": [{"title": h.title, "url": h.url} for h in bb_hits[:4]],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
if bb_hits:
    t0 = time.time()
    try:
        bb = svc.scrape(bb_hits[0].url, include_images=False)
        print(
            json.dumps(
                {
                    "bb_scrape": True,
                    "secs": round(time.time() - t0, 1),
                    "title": bb.title,
                    "price": str(bb.price),
                    "available": bb.available,
                    "pid": bb.product_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "bb_scrape": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:180],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
else:
    # direct PDP retry
    t0 = time.time()
    try:
        bb = svc.scrape(
            "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
            include_images=False,
        )
        print(
            json.dumps(
                {
                    "bb_scrape_direct": True,
                    "secs": round(time.time() - t0, 1),
                    "title": bb.title,
                    "price": str(bb.price),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "bb_scrape_direct": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:180],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
