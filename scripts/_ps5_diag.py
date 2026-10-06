import json

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.html_fetcher import apply_proxy_geo_targeting
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
# verify US pin without printing secrets
u = get_settings().camoufox_proxy_url or ""
pinned = apply_proxy_geo_targeting(u, "https://www.bestbuy.com/product/x/1")
print("us_pin", "__cr.us" in pinned, "br_pin", "__cr.br" in pinned, flush=True)

guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
ref = svc.scrape(
    "https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/p/240604800/ga/gap5/?seller_id=magazineluiza",
    include_images=False,
)
ident = identity_from_price_item(ref)
print(
    json.dumps(
        {
            "ref": {
                "title": ref.title,
                "model": ident.model,
                "mpn": ident.mpn,
                "variant": ident.variant_attrs,
                "queries": build_search_queries(ident)[:6],
            }
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)
search = StoreSearchService()
hits = search.search(
    "shoppingchina",
    build_search_queries(ident)[0]
    if build_search_queries(ident)
    else "playstation 5 digital",
    limit=5,
)
# try better query
for q in build_search_queries(ident)[:5]:
    hits = search.search("shoppingchina", q, limit=5)
    print(
        json.dumps(
            {
                "q": q,
                "count": len(hits),
                "titles": [(h.title or "")[:70] for h in hits],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if hits:
        break
engine = MatchingEngine()
for h in hits:
    try:
        item = svc.scrape(h.url, include_images=False)
        score = engine.score(ident, identity_from_price_item(item))
        print(
            json.dumps(
                {
                    "cand": (item.title or "")[:90],
                    "price": str(item.price),
                    "decision": score.decision,
                    "reasons": [
                        r.code + ":" + (r.detail or "")[:60] for r in score.reasons
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
                    "cand_url": h.url,
                    "err": getattr(e, "code", type(e).__name__),
                    "msg": str(e)[:120],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

# Direct score Magalu vs SC 1031749
sc = svc.scrape(
    "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    include_images=False,
)
score = engine.score(ident, identity_from_price_item(sc))
print(
    json.dumps(
        {
            "direct_sc": {
                "decision": score.decision,
                "reasons": [
                    {"code": r.code, "detail": r.detail} for r in score.reasons
                ],
                "sc_model": identity_from_price_item(sc).model,
                "ref_model": ident.model,
            }
        },
        ensure_ascii=False,
        indent=2,
    ),
    flush=True,
)

# BB with US pin - try non-bundle PDP
print("BB", flush=True)
try:
    item = svc.scrape(
        "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4",
        include_images=False,
    )
    print(
        json.dumps(
            {"bb_ok": True, "title": item.title, "price": str(item.price)},
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:
    print(
        json.dumps(
            {
                "bb_ok": False,
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:200],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
