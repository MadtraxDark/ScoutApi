import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
for asin in ["B0DJFTJ6LX", "B0DGML9C16"]:
    url = f"https://www.amazon.com.br/dp/{asin}"
    t0 = time.time()
    try:
        item = svc.scrape(url, include_images=False)
        print(
            json.dumps(
                {
                    "asin": asin,
                    "ok": True,
                    "secs": round(time.time() - t0, 1),
                    "title": (item.title or "")[:80],
                    "price": str(item.price),
                    "currency": item.currency,
                    "available": item.available,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "asin": asin,
                    "ok": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:160],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
