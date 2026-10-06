"""Inspect iPhone 16 Nissei specs vs title storage conflict."""

from __future__ import annotations

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.paraguay.nissei import NisseiSpider

URL = "https://nissei.com/br/apple-iphone-16-a3287-128-gb-black"


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    _scrape = ProductScrapeService(guard=guard)
    fetcher = get_shared_html_fetcher()
    response = fetcher.fetch(URL)
    spider = NisseiSpider()
    specs = spider._specifications(response)
    print("title", response.css("h1 .base::text").get())
    print("canonical", response.css("link[rel=canonical]::attr(href)").get())
    for key in ("Cor", "Memoria Interna", "Memoria RAM", "UPC"):
        print(key, specs.get(key))
    item = spider.parse_product(response)
    print(
        "parsed",
        item.title,
        item.variant,
        item.metadata.get("variant"),
        item.metadata.get("source"),
    )


if __name__ == "__main__":
    main()
