import json

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import identity_from_price_item

get_shared_html_fetcher.cache_clear()
guard = ScrapeGuard(
    url_cooldown_seconds=0, domain_min_interval_seconds=0, result_cache_ttl_seconds=0
)
svc = ProductScrapeService(guard=guard)


def dump(label, item):
    meta = item.metadata if isinstance(item.metadata, dict) else {}
    idn = identity_from_price_item(item)
    out = {
        "store": item.store,
        "title": item.title,
        "brand": item.brand,
        "model": item.model,
        "product_id": item.product_id,
        "sku": item.sku,
        "gtin": item.gtin,
        "variant": item.variant,
        "price": str(item.price) if item.price is not None else None,
        "currency": item.currency,
        "available": item.available,
        "seller": item.seller,
        "url": item.url,
        "canonical_url": item.canonical_url,
        "identity": {
            "brand": idn.brand,
            "model": idn.model,
            "mpn": idn.mpn,
            "gtin": idn.gtin,
            "variant_attrs": idn.variant_attrs,
            "title": idn.title,
        },
        "meta_keys": sorted(meta.keys()),
        "specifications": meta.get("specifications"),
        "color": meta.get("color"),
        "storage": meta.get("storage"),
        "package": meta.get("package_contents")
        or meta.get("whats_in_the_box")
        or meta.get("includes"),
    }
    print(label, json.dumps(out, ensure_ascii=False, default=str)[:4000], flush=True)
    return item, idn


sc, id_sc = dump(
    "SC",
    svc.scrape(
        "https://www.shoppingchina.com.br/produto/console-sony-playstation-5-cfi-2115b-digital-825gb-8k-1031749",
        include_images=False,
    ),
)
amz, id_amz = dump(
    "AMZ", svc.scrape("https://www.amazon.com/dp/B09W14BJF1", include_images=False)
)
score = MatchingEngine().score(id_sc, id_amz)
print(
    "SCORE",
    score.decision,
    score.confidence,
    [(r.code, r.detail) for r in score.reasons],
    flush=True,
)
