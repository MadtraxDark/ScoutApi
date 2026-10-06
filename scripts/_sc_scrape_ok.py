from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
url = "https://www.shoppingchina.com.py/producto/celular-apple-iphone-16-a3287-128gb-black-sim-948623"
svc = ProductScrapeService(
    guard=ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=0,
    )
)
item = svc.scrape(url, include_images=False)
print("ok", item.title, item.price, item.currency, item.product_id, item.available)
