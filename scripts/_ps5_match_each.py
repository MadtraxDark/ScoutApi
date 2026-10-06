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
# Exact Magalu listing matching user title (Astro's Playroom / Slim Digital / Branco)
REF = "https://www.magazineluiza.com.br/console-sony-playstation-5-edicao-digital-slim-ssd-825gb-controle-dualsense-astro-s-playroom-branco-cfi-2114b/p/gcf919d9d7/ga/gap5/?seller_id=kabum"
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
print("REF_SCRAPE", flush=True)
ref = scrape.scrape(REF, include_images=False)
print(
    json.dumps(
        {
            "title": ref.title,
            "price": str(ref.price),
            "currency": ref.currency,
            "url": ref.url,
            "variant": ref.variant,
        },
        ensure_ascii=False,
    ),
    flush=True,
)

STORES = [
    "kabum",
    "amazon_br",
    "shoppingchina",
    "nissei",
    "bestbuy",
    "amazon_us",
    "shopee",
]
svc = ProductMatchService(scrape_service=scrape)
for stores in [[s] for s in STORES]:
    print("MATCH", stores, flush=True)
    t0 = time.time()
    try:
        resp = svc.match(
            MatchRequest(
                reference_url=REF,
                stores=stores,
                persist=False,
                include_review=True,
                max_candidates_per_store=4,
            )
        )
        print(
            json.dumps(
                {
                    "stores": stores,
                    "secs": round(time.time() - t0, 1),
                    "matches": [
                        {
                            "store": h.store,
                            "decision": h.decision,
                            "title": (h.product.title or "")[:100],
                            "price": str(h.product.price),
                            "currency": h.product.currency,
                            "url": h.product.url,
                            "reasons": [r.code for r in h.reasons],
                        }
                        for h in resp.matches
                    ],
                    "unmatched": resp.unmatched_stores,
                    "errors": [
                        {"store": e.store, "code": e.code, "message": e.message[:160]}
                        for e in resp.errors
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps(
                {
                    "stores": stores,
                    "secs": round(time.time() - t0, 1),
                    "fatal": type(e).__name__,
                    "msg": str(e)[:200],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
