import json

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
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
ref_url = "https://www.kabum.com.br/produto/1050570/console-sony-playstation-5-edicao-digital-slim-ssd-825gb-controle-dualsense-astro-s-playroom-branco-cfi-2114b"
ref = svc.scrape(ref_url, include_images=False)
print("QUERIES", build_search_queries(identity_from_price_item(ref))[:8], flush=True)
r = ProductMatchService(scrape_service=svc).match(
    MatchRequest(reference_url=ref_url, stores=["shoppingchina"], persist=False)
)
print(
    json.dumps(
        {
            "matches": [
                {
                    "decision": m.decision,
                    "conf": str(m.confidence),
                    "title": m.product.title if m.product else None,
                    "url": m.product.url if m.product else None,
                    "query": m.search_query,
                    "reasons": [f"{x.code}:{x.detail}" for x in m.reasons][:5],
                }
                for m in r.matches
            ],
            "unmatched": r.unmatched_stores,
            "errors": [
                {"store": e.store, "code": e.code, "msg": e.message[:80]}
                for e in (r.errors or [])
            ],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
