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
print("proxy_set", bool((s.camoufox_proxy_url or "").strip()), flush=True)
url = "https://www.bestbuy.com/product/playstation-5-slim-digital-edition-console-825gb/JXHQ37TYYL?intl=nosplash"
# HTTP probe
http = UrllibHtmlFetcher()
t0 = time.time()
try:
    resp = http.fetch(url)
    text = resp.text or ""
    print(
        json.dumps(
            {
                "http_ok": True,
                "secs": round(time.time() - t0, 1),
                "status": resp.status,
                "len": len(text),
                "title": (
                    text[text.find("<title>") : text.find("</title>") + 8]
                    if "<title>" in text
                    else ""
                )[:120],
                "has_apollo": "__APOLLO_STATE__" in text or "customerPrice" in text,
                "has_ld": '"@type"' in text and "Product" in text,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "http_ok": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:200],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

# Browser with proxy path via ProductScrapeService
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
t0 = time.time()
try:
    item = ProductScrapeService(guard=guard).scrape(
        url.replace("?intl=nosplash", ""), include_images=False
    )
    print(
        json.dumps(
            {
                "browser_ok": True,
                "secs": round(time.time() - t0, 1),
                "title": item.title,
                "price": str(item.price),
                "currency": item.currency,
                "meta": (item.metadata or {}).get("fetch_metrics"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "browser_ok": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:250],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
