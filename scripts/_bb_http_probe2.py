import json
import time

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.html_fetcher import UrllibHtmlFetcher
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
s = get_settings()
ua = (
    getattr(s, "scraper_user_agent", None)
    or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)
http = UrllibHtmlFetcher(user_agent=ua)
url = "https://www.bestbuy.com/product/playstation-5-slim-digital-edition-console-825gb/JXHQ37TYYL?intl=nosplash"
t0 = time.time()
try:
    resp = http.fetch(url)
    text = resp.text or ""
    print(
        json.dumps(
            {
                "http": True,
                "secs": round(time.time() - t0, 1),
                "status": resp.status,
                "len": len(text),
                "apollo": "ApolloSSRDataTransport" in text,
                "blocked": "access denied" in text.casefold()
                or "pardon our interruption" in text.casefold(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "http": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:200],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
t0 = time.time()
try:
    item = ProductScrapeService(guard=guard).scrape(
        "https://www.bestbuy.com/product/playstation-5-slim-digital-edition-console-825gb/JXHQ37TYYL",
        include_images=False,
    )
    print(
        json.dumps(
            {
                "browser": True,
                "secs": round(time.time() - t0, 1),
                "title": item.title,
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
                "browser": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:250],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
