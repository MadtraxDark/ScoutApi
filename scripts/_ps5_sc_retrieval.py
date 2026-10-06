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
    critical_identity_conflict,
    identity_from_price_item,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)
engine = MatchingEngine()

print("scraping sc...", flush=True)
sc = svc.scrape(
    "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
    include_images=False,
)
print("scraping kab...", flush=True)
kab = svc.scrape(
    "https://www.kabum.com.br/produto/1050570/console-sony-playstation-5-edicao-digital-slim-ssd-825gb-controle-dualsense-astro-s-playroom-branco-cfi-2114b",
    include_images=False,
)
id_sc, id_kab = identity_from_price_item(sc), identity_from_price_item(kab)
print("SC", sc.title, sc.model, sc.gtin, sc.variant, id_sc.model, id_sc.mpn, flush=True)
print(
    "KAB",
    kab.title,
    kab.model,
    kab.gtin,
    kab.variant,
    id_kab.model,
    id_kab.mpn,
    flush=True,
)
print("conflict", critical_identity_conflict(id_kab, id_sc), flush=True)
s = engine.score(id_kab, id_sc)
print(
    "SCORE",
    s.decision,
    s.confidence,
    [(r.code, r.detail) for r in s.reasons],
    flush=True,
)
qs = build_search_queries(id_kab)
print("QUERIES", qs[:8], flush=True)
ss = StoreSearchService(html_fetcher=get_shared_html_fetcher())
for q in qs[:5]:
    t0 = time.time()
    hits = ss.search("shoppingchina", q)
    print(
        json.dumps(
            {
                "q": q,
                "n": len(hits),
                "secs": round(time.time() - t0, 1),
                "top": [{"title": h.title, "url": h.url} for h in hits[:3]],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if any(
        "1031749" in (h.url or "")
        or "cfi-2115" in (h.url or "").lower()
        or "playstation" in (h.title or "").lower()
        for h in hits
    ):
        # score first hit
        hit = hits[0]
        try:
            cand = svc.scrape(hit.url, include_images=False)
            sc2 = engine.score(id_kab, identity_from_price_item(cand))
            print(
                "HIT_SCORE",
                hit.url,
                sc2.decision,
                [(r.code, r.detail) for r in sc2.reasons][:6],
                flush=True,
            )
        except Exception as e:
            print(
                "HIT_SCRAPE_FAIL",
                getattr(e, "code", type(e).__name__),
                str(e)[:120],
                flush=True,
            )
        break
