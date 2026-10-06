import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import identity_from_price_item

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
sc = svc.scrape(
    "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    include_images=False,
)
amz = svc.scrape("https://www.amazon.com/dp/B09W14BJF1", include_images=False)
score = MatchingEngine().score(
    identity_from_price_item(sc), identity_from_price_item(amz)
)
print("SCORE", score.decision, [r.detail for r in score.reasons], flush=True)
t0 = time.time()
try:
    bb = svc.scrape(
        "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
        include_images=False,
    )
    print(
        json.dumps(
            {
                "bb": True,
                "secs": round(time.time() - t0, 1),
                "title": bb.title,
                "price": str(bb.price),
                "currency": bb.currency,
                "available": bb.available,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "bb": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:180],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
