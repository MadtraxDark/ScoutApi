import json
import time

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
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


# SC candidate search from Kabum identity queries via store search

guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)

svc = ProductScrapeService(guard=guard)

engine = MatchingEngine()

sc = svc.scrape(
    "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    include_images=False,
)

amz = svc.scrape("https://www.amazon.com/dp/B09W14BJF1", include_images=False)

kab = svc.scrape(
    "https://www.kabum.com.br/produto/1050570/console-sony-playstation-5-edicao-digital-slim-ssd-825gb-controle-dualsense-astro-s-playroom-branco-cfi-2114b",
    include_images=False,
)

id_sc, id_amz, id_kab = map(identity_from_price_item, [sc, amz, kab])

s1 = engine.score(id_sc, id_amz)

s2 = engine.score(id_sc, id_kab)

s3 = engine.score(id_kab, id_sc)

print(
    json.dumps(
        {
            "sc": {
                "title": sc.title,
                "model": sc.model,
                "gtin": sc.gtin,
                "variant": sc.variant,
                "price": str(sc.price),
            },
            "amz": {
                "title": amz.title,
                "model": amz.model,
                "asin": amz.product_id,
                "variant": amz.variant,
                "price": str(amz.price),
                "seller": amz.seller,
            },
            "kab": {
                "title": kab.title,
                "model": kab.model,
                "gtin": kab.gtin,
                "variant": kab.variant,
                "price": str(kab.price),
            },
            "sc_vs_amz": {
                "decision": s1.decision,
                "reasons": [f"{r.code}:{r.detail}" for r in s1.reasons],
            },
            "sc_vs_kab": {
                "decision": s2.decision,
                "conf": str(s2.confidence),
                "reasons": [f"{r.code}:{r.detail}" for r in s2.reasons][:8],
            },
            "kab_vs_sc": {
                "decision": s3.decision,
                "conf": str(s3.confidence),
                "reasons": [f"{r.code}:{r.detail}" for r in s3.reasons][:8],
            },
        },
        ensure_ascii=False,
    ),
    flush=True,
)


ss = StoreSearchService(html_fetcher=get_shared_html_fetcher())


qs = build_search_queries(id_kab)
print("KAB_QUERIES", qs[:6], flush=True)
for q in qs[:4]:
    hits = ss.search("shoppingchina", q)
    print(
        "SC_SERP",
        q,
        len(hits),
        [{"title": h.title, "url": h.url} for h in hits[:3]],
        flush=True,
    )
    if hits:
        break

t0 = time.time()
bb_hits = ss.search("bestbuy", "playstation 5 slim digital")
print(
    json.dumps(
        {
            "bb_search": True,
            "secs": round(time.time() - t0, 1),
            "n": len(bb_hits),
            "top": [{"title": h.title, "url": h.url} for h in bb_hits[:3]],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
t0 = time.time()
try:
    url = (
        bb_hits[0].url
        if bb_hits
        else "https://www.bestbuy.com/product/playstation-5-slim-console-digital-edition/JXHQ3CCTY4"
    )
    bb = svc.scrape(url, include_images=False)
    print(
        json.dumps(
            {
                "bb_scrape": True,
                "secs": round(time.time() - t0, 1),
                "title": bb.title,
                "price": str(bb.price),
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
                "bb_scrape": False,
                "secs": round(time.time() - t0, 1),
                "code": getattr(e, "code", type(e).__name__),
                "msg": str(e)[:180],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
