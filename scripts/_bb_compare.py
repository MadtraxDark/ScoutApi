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
# Control: iPhone BB that worked before
for label, url in [
    (
        "iphone",
        "https://www.bestbuy.com/product/apple-iphone-16-128gb-apple-intelligence-black-verizon/JCQ6HRGR8C",
    ),
    (
        "ps5",
        "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
    ),
]:
    t0 = time.time()
    try:
        item = svc.scrape(url, include_images=False)
        print(
            json.dumps(
                {
                    "label": label,
                    "ok": True,
                    "secs": round(time.time() - t0, 1),
                    "title": (item.title or "")[:70],
                    "price": str(item.price),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "label": label,
                    "ok": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:180],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
