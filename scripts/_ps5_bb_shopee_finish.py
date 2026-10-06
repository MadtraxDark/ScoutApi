import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

get_shared_html_fetcher.cache_clear()
REF = "https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/p/240604800/ga/gap5/?seller_id=magazineluiza"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
print("BB_SCRAPE", flush=True)
t0 = time.time()
try:
    item = ProductScrapeService(guard=guard).scrape(
        "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
        include_images=False,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "secs": round(time.time() - t0, 1),
                "title": item.title,
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
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:160],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
print("SHOPEE", flush=True)
t0 = time.time()
resp = ProductMatchService(scrape_service=ProductScrapeService(guard=guard)).match(
    MatchRequest(
        reference_url=REF,
        stores=["shopee"],
        persist=False,
        include_review=True,
        max_candidates_per_store=3,
    )
)
print(
    json.dumps(
        {
            "secs": round(time.time() - t0, 1),
            "matches": [
                {
                    "decision": h.decision,
                    "title": (h.product.title or "")[:100],
                    "price": str(h.product.price),
                }
                for h in resp.matches
            ],
            "unmatched": resp.unmatched_stores,
            "errors": [
                {"code": e.code, "message": e.message[:140]} for e in resp.errors
            ],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
