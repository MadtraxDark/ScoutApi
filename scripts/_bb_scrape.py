import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
url = "https://www.bestbuy.com/product/apple-iphone-16-128gb-apple-intelligence-black-verizon/JCQ6HRGR8C"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
t0 = time.time()
try:
    item = svc.scrape(url, include_images=False)
    print(
        json.dumps(
            {
                "ok": True,
                "secs": round(time.time() - t0, 1),
                "title": item.title,
                "price": str(item.price),
                "currency": item.currency,
                "proxy": (item.metadata or {}).get("fetch_metrics"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "err": type(e).__name__,
                "code": getattr(e, "code", None),
                "msg": str(e)[:300],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
