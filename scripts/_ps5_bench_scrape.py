import json

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
urls = {
    "shoppingchina": "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    "amazon_us": "https://www.amazon.com/Sony-Playstation-Version-Ultra-High-Bluetooth/dp/B09W14BJF1/",
}
for name, url in urls.items():
    print("SCRAPE", name, flush=True)
    try:
        item = svc.scrape(url, include_images=False)
        d = item.model_dump(mode="json")
        keep = {
            k: d.get(k)
            for k in [
                "store",
                "country",
                "product_id",
                "sku",
                "gtin",
                "brand",
                "model",
                "title",
                "variant",
                "price",
                "currency",
                "available",
                "availability",
                "seller",
                "url",
                "canonical_url",
                "description",
                "specifications",
                "metadata",
            ]
        }
        print(
            json.dumps({name: keep}, ensure_ascii=False, indent=2, default=str),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    name: {
                        "error": type(e).__name__,
                        "code": getattr(e, "code", None),
                        "msg": str(e)[:300],
                    }
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
