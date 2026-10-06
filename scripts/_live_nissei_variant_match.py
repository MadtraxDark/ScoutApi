"""Live validation: Nissei variant PDPs + Product Match for 256 GB reference."""

from __future__ import annotations

import json
import time
from pathlib import Path

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

OUT = Path("data/nissei_variant_diag")
OUT.mkdir(parents=True, exist_ok=True)

URLS = [
    "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual",
    "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual-256-gb-black-titanium-1",
    # Other products for generic validation
    "https://nissei.com/br/apple-iphone-16-a3287-128-gb-black",
]


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    report: dict = {"scrapes": [], "search": None, "match": None}

    for url in URLS:
        print("SCRAPE", url, flush=True)
        t0 = time.time()
        try:
            item = scrape.scrape(url, include_images=False)
            ms = round((time.time() - t0) * 1000)
            ident = identity_from_price_item(item)
            entry = {
                "ok": True,
                "nissei_candidate_fetch_ms": ms,
                "url": item.url,
                "canonical_url": item.canonical_url,
                "title": item.title,
                "product_id": item.product_id,
                "sku": item.sku,
                "brand": item.brand,
                "model": item.model,
                "variant": item.variant,
                "price": str(item.price),
                "currency": item.currency,
                "availability": item.availability,
                "gtin": item.gtin,
                "metadata_variant": (item.metadata or {}).get("variant"),
                "metadata_source": (item.metadata or {}).get("source"),
                "selected_variant": (item.metadata or {}).get("selected_variant"),
                "parent_product_id": (item.metadata or {}).get("parent_product_id"),
                "variant_product_id": (item.metadata or {}).get("variant_product_id"),
                "identity_attrs": ident.variant_attrs,
                "mpn": ident.mpn_display or ident.mpn,
                "queries": build_search_queries(ident)[:6],
            }
            print(json.dumps(entry, ensure_ascii=False, indent=2), flush=True)
            report["scrapes"].append(entry)
        except Exception as exc:  # noqa: BLE001
            entry = {
                "ok": False,
                "url": url,
                "ms": round((time.time() - t0) * 1000),
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }
            print(entry, flush=True)
            report["scrapes"].append(entry)

    print("SEARCH nissei", flush=True)
    search = StoreSearchService()
    t0 = time.time()
    try:
        hits = search.search("nissei", "Samsung Galaxy S25 Ultra 256GB", limit=5)
        report["search"] = {
            "ok": True,
            "nissei_search_ms": round((time.time() - t0) * 1000),
            "count": len(hits),
            "hits": [
                {"title": h.title, "url": h.url, "id": h.product_id} for h in hits
            ],
        }
    except Exception as exc:  # noqa: BLE001
        report["search"] = {
            "ok": False,
            "nissei_search_ms": round((time.time() - t0) * 1000),
            "error": type(exc).__name__,
            "message": str(exc)[:300],
        }
    print(json.dumps(report["search"], ensure_ascii=False, indent=2), flush=True)

    print("MATCH nissei only", flush=True)
    t0 = time.time()
    try:
        ref = identity_reference_item(
            "Samsung Galaxy S25 Ultra 256 GB Black Titanium",
            brand="Samsung",
            model="Galaxy S25 Ultra",
            category="smartphone",
        )
        # Ensure storage is present on the synthetic reference.
        ref = ref.model_copy(
            update={
                "variant": "color: Black Titanium; storage: 256 GB",
                "metadata": {
                    **(ref.metadata or {}),
                    "variant": {"storage": "256 GB", "color": "Black Titanium"},
                },
            }
        )
        matcher = ProductMatchService(scrape_service=scrape, search_service=search)
        result = matcher.match_from_item(
            ref,
            stores=["nissei"],
            include_review=False,
            persist=False,
            include_images=False,
            max_candidates_per_store=3,
        )
        _store_hit = None
        for offer in result.offers or []:
            if getattr(offer, "store", None) == "nissei":
                _store_hit = offer
                break
        # MatchResponse shape may use matches/stores — dump compact.
        report["match"] = {
            "ok": True,
            "nissei_total_ms": round((time.time() - t0) * 1000),
            "dump": json.loads(result.model_dump_json())
            if hasattr(result, "model_dump_json")
            else str(result)[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        report["match"] = {
            "ok": False,
            "nissei_total_ms": round((time.time() - t0) * 1000),
            "error": type(exc).__name__,
            "message": str(exc)[:400],
        }
    print(json.dumps(report["match"], ensure_ascii=False, indent=2)[:4000], flush=True)

    path = OUT / "live_validation.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("WROTE", path, flush=True)


if __name__ == "__main__":
    main()
