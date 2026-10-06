import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
search = StoreSearchService()
# Capture whether shopee returned wall or empty HTML markers
print("SHOPEE_DETAIL", flush=True)
t0 = time.time()
try:
    # use search service internals
    hits = search.search("shopee", "apple iphone 16 128gb preto", limit=5)
    print(
        json.dumps(
            {
                "secs": round(time.time() - t0, 1),
                "count": len(hits),
                "sample": [
                    {"title": h.title, "url": h.url, "meta": h.metadata}
                    for h in hits[:3]
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
                "secs": round(time.time() - t0, 1),
                "err": type(e).__name__,
                "code": getattr(e, "code", None),
                "msg": str(e)[:250],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

print("BB_TITLES", flush=True)
hits = search.search("bestbuy", "apple iphone 16 128gb black", limit=5)
print(
    json.dumps(
        {"count": len(hits), "urls": [h.url for h in hits[:5]]}, ensure_ascii=False
    ),
    flush=True,
)
if hits:
    url = hits[0].url
    print("BB_SCRAPE", url, flush=True)
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=0,
    )
    svc = ProductScrapeService(guard=guard)
    t0 = time.time()
    try:
        item = svc.scrape(url, include_images=False)
        print(
            json.dumps(
                {
                    "secs": round(time.time() - t0, 1),
                    "title": item.title,
                    "price": str(item.price),
                    "currency": item.currency,
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
                    "msg": str(e)[:250],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
