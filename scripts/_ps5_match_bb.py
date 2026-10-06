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
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)

# Magalu Digital 1 controle -> SC
req = MatchRequest(
    reference_url="https://www.magazineluiza.com.br/console-sony-playstation-5-edicao-digital-slim-ssd-825gb-1-controle-branco/p/238803400/in/cnsl/",
    stores=["shoppingchina"],
    persist=False,
)
match_svc = ProductMatchService(scrape_service=svc)
t0 = time.time()
result = match_svc.match(req)
print(
    json.dumps(
        {
            "secs": round(time.time() - t0, 1),
            "ref_title": result.reference.title if result.reference else None,
            "matches": [
                {
                    "store": m.store,
                    "decision": m.decision,
                    "confidence": str(m.confidence),
                    "title": m.title,
                    "reasons": [r.code + ":" + (r.detail or "") for r in m.reasons][:6],
                }
                for m in result.matches
            ],
            "unmatched": result.unmatched_stores,
            "errors": [
                {k: str(v) for k, v in (e if isinstance(e, dict) else {"e": e}).items()}
                for e in (result.errors or [])
            ][:3],
        },
        ensure_ascii=False,
        default=str,
    )[:3500],
    flush=True,
)

# BB retries
urls = [
    "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
    "https://www.bestbuy.com/site/searchpage.jsp?st=playstation+5+slim+digital",
]
for u in urls:
    t0 = time.time()
    try:
        if "searchpage" in u:
            from scout_api.modules.matching.store_search_service import (
                StoreSearchService,
            )

            ss = StoreSearchService(html_fetcher=get_shared_html_fetcher())
            hits = ss.search("bestbuy", "playstation 5 slim digital edition")
            print(
                json.dumps(
                    {
                        "bb_search": True,
                        "secs": round(time.time() - t0, 1),
                        "n": len(hits),
                        "top": [{"title": h.title, "url": h.url} for h in hits[:3]],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        else:
            bb = svc.scrape(u, include_images=False)
            print(
                json.dumps(
                    {
                        "bb_scrape": True,
                        "secs": round(time.time() - t0, 1),
                        "title": bb.title,
                        "price": str(bb.price),
                        "available": bb.available,
                        "product_id": bb.product_id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception as e:
        print(
            json.dumps(
                {
                    "bb": u[:60],
                    "ok": False,
                    "secs": round(time.time() - t0, 1),
                    "code": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:160],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
