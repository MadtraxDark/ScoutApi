from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.paraguay.shoppingchina import ShoppingChinaSpider

get_shared_html_fetcher.cache_clear()
legacy = "https://www.shoppingchina.com.py/produto/celular-apple-iphone-16-a3287-128gb-black-sim-948623"
print("prep", ShoppingChinaSpider().prepare_fetch_url(legacy))
svc = ProductScrapeService()
item = svc.scrape(legacy, include_images=False)
print("ok", item.title, item.price, item.currency, item.url, item.product_id)
