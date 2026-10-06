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

QUERY = "PlayStation 5 Edição Digital 825GB 1 Controle Branco Sony"
print("FIND_MAGALU", flush=True)
hits = search.search("magazineluiza", QUERY, limit=5)
print(
    json.dumps(
        [{"id": h.product_id, "url": h.url} for h in hits], ensure_ascii=False, indent=2
    ),
    flush=True,
)

# Prefer exact slug without 'com 2 jogos' / bundles
REF = None
for h in hits:
    u = (h.url or "").casefold()
    if "2-jogos" in u or "astro" in u or "watch" in u:
        continue
    if (
        "edicao-digital" in u
        or "edi%c3%a7" in u
        or "digital-825" in u
        or "playstation-5-edicao-digital" in u
    ):
        REF = h.url
        break
if REF is None and hits:
    REF = hits[0].url
print("REF", REF, flush=True)
ref = scrape.scrape(REF, include_images=False)
print(
    json.dumps(
        {
            "title": ref.title,
            "price": str(ref.price),
            "currency": ref.currency,
            "variant": ref.variant,
            "color": getattr(ref, "color", None),
            "url": ref.url,
        },
        ensure_ascii=False,
    ),
    flush=True,
)

svc = ProductMatchService(scrape_service=scrape)
for store in [
    "kabum",
    "amazon_br",
    "shoppingchina",
    "nissei",
    "bestbuy",
    "amazon_us",
    "shopee",
]:
    print("MATCH", store, flush=True)
    t0 = time.time()
    try:
        resp = svc.match(
            MatchRequest(
                reference_url=REF,
                stores=[store],
                persist=False,
                include_review=True,
                max_candidates_per_store=4,
            )
        )
        print(
            json.dumps(
                {
                    "store": store,
                    "secs": round(time.time() - t0, 1),
                    "matches": [
                        {
                            "decision": h.decision,
                            "title": (h.product.title or "")[:110],
                            "price": str(h.product.price),
                            "currency": h.product.currency,
                            "url": h.product.url,
                            "reasons": [r.code for r in h.reasons],
                        }
                        for h in resp.matches
                    ],
                    "unmatched": resp.unmatched_stores,
                    "errors": [
                        {"code": e.code, "message": e.message[:160]}
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
                    "store": store,
                    "secs": round(time.time() - t0, 1),
                    "fatal": type(e).__name__,
                    "msg": str(e)[:200],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
