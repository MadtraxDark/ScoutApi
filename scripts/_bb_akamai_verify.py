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
urls = [
    "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
    "https://www.bestbuy.com/product/playstation-5-slim-digital-edition-console-825gb/JXHQ37TYYL",
]
for u in urls:
    t0 = time.time()
    try:
        bb = svc.scrape(u, include_images=False)
        print(
            json.dumps(
                {
                    "ok": True,
                    "secs": round(time.time() - t0, 1),
                    "title": (bb.title or "")[:80],
                    "price": str(bb.price),
                    "avail": bb.available,
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
                    "ok": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:160],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
