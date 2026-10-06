import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=1, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
REF = "https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/p/240604800/ga/gap5/?seller_id=magazineluiza"
print(
    json.dumps(
        {
            "queries": build_search_queries(
                identity_from_price_item(svc.scrape(REF, include_images=False))
            )[:8]
        },
        ensure_ascii=False,
    ),
    flush=True,
)
t0 = time.time()
resp = ProductMatchService(scrape_service=svc).match(
    MatchRequest(
        reference_url=REF,
        stores=["shoppingchina"],
        persist=False,
        include_review=True,
        max_candidates_per_store=5,
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
                    "url": h.product.url,
                    "reasons": [r.code for r in h.reasons],
                    "query": h.search_query,
                }
                for h in resp.matches
            ],
            "unmatched": resp.unmatched_stores,
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
