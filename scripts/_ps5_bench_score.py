import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import identity_from_price_item
from scout_api.modules.matching.store_search_service import StoreSearchService

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
ri = identity_from_price_item(sc)
ci = identity_from_price_item(amz)
score = MatchingEngine().score(ri, ci)
print(
    json.dumps(
        {
            "sc": {
                "brand": ri.brand,
                "model": ri.model,
                "mpn": ri.mpn,
                "gtin": ri.gtin,
                "variant": ri.variant_attrs,
                "title": ri.title,
            },
            "amz": {
                "brand": ci.brand,
                "model": ci.model,
                "mpn": ci.mpn,
                "gtin": ci.gtin,
                "variant": ci.variant_attrs,
                "title": ci.title,
            },
            "score": {
                "decision": score.decision,
                "confidence": str(score.confidence),
                "reasons": [
                    {"code": r.code, "detail": r.detail} for r in score.reasons
                ],
            },
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)

# Best Buy reproduce
bb_url = "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4"
print("BB_SCRAPE", flush=True)
t0 = time.time()
try:
    item = svc.scrape(bb_url, include_images=False)
    print(
        json.dumps(
            {
                "ok": True,
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
                "ok": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:250],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

print("BB_SEARCH", flush=True)
t0 = time.time()
hits = StoreSearchService().search(
    "bestbuy", "playstation 5 slim digital edition", limit=5
)
print(
    json.dumps(
        {
            "secs": round(time.time() - t0, 1),
            "count": len(hits),
            "urls": [h.url for h in hits],
        },
        ensure_ascii=False,
    ),
    flush=True,
)

# SC search from amazon-like query
print("SC_SEARCH", flush=True)
for q in [
    "playstation 5 digital 825",
    "sony playstation 5 CFI-2115B",
    "ps5 digital 825gb",
]:
    hits = StoreSearchService().search("shoppingchina", q, limit=3)
    print(
        json.dumps(
            {
                "q": q,
                "count": len(hits),
                "sample": [
                    {"title": h.title, "url": h.url, "id": h.product_id} for h in hits
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
