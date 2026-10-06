import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
scrape = ProductScrapeService(guard=guard)
search = StoreSearchService()

QUERY = "Console Sony PlayStation 5 Edição Digital Slim SSD 825GB DualSense Branco"
# Find Magalu reference via search
print("FIND_MAGALU", flush=True)
hits = search.search("magazineluiza", QUERY, limit=5)
print(
    json.dumps(
        [
            {"id": h.product_id, "title": (h.title or "")[:100], "url": h.url}
            for h in hits
        ],
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
if not hits:
    raise SystemExit("no magalu hits")
REF = hits[0].url
print("REF", REF, flush=True)
ref_item = scrape.scrape(REF, include_images=False)
print(
    json.dumps(
        {
            "ref_title": ref_item.title,
            "price": str(ref_item.price),
            "currency": ref_item.currency,
            "url": ref_item.url,
        },
        ensure_ascii=False,
    ),
    flush=True,
)

STORES = [
    "kabum",
    "shoppingchina",
    "nissei",
    "bestbuy",
    "amazon_br",
    "amazon_us",
    "shopee",
]
print("MATCH_START", flush=True)
t0 = time.time()
svc = ProductMatchService(scrape_service=scrape)
resp = svc.match(
    MatchRequest(
        reference_url=REF,
        stores=["magazineluiza"] + STORES,
        persist=False,
        include_review=True,
        max_candidates_per_store=4,
    )
)
print(
    json.dumps(
        {
            "secs": round(time.time() - t0, 1),
            "reference": {
                "title": resp.reference.title,
                "price": str(resp.reference.price),
                "url": resp.reference.url,
            },
            "matches": [
                {
                    "store": h.store,
                    "decision": h.decision,
                    "confidence": str(h.confidence),
                    "title": (h.product.title or "")[:110],
                    "price": str(h.product.price),
                    "currency": h.product.currency,
                    "url": h.product.url,
                    "query": h.search_query,
                    "reasons": [r.code for r in h.reasons],
                }
                for h in resp.matches
            ],
            "unmatched": resp.unmatched_stores,
            "errors": [
                {"store": e.store, "code": e.code, "message": e.message[:180]}
                for e in resp.errors
            ],
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
