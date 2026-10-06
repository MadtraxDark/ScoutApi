"""Product Match focused on Nissei for Galaxy S25 Ultra 256GB."""

from __future__ import annotations

import json
import time
from pathlib import Path

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

OUT = Path("data/nissei_variant_diag/match_s25.json")


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    search = StoreSearchService()

    # Reference from the known good Black Titanium PDP (simple product).
    _ref_url = (
        "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual"
        "-256-gb-black-titanium-1"
    )
    print("REF scrape", flush=True)
    t0 = time.time()
    # Use Magalu-style synthetic? Better: scrape Magazineluiza if available,
    # else build from Nissei B itself is circular. Use identity item with attrs.
    reference = ProductPriceItem.model_validate(
        {
            "store": "synthetic",
            "country": "BR",
            "product_id": "ref-s25u-256",
            "url": "scout://identity/s25u-256",
            "canonical_url": "scout://identity/s25u-256",
            "title": "Samsung Galaxy S25 Ultra 256GB Black Titanium",
            "brand": "Samsung",
            "model": "Galaxy S25 Ultra",
            "variant": "color: Black Titanium; storage: 256 GB",
            "currency": "BRL",
            "price": "1.00",
            "scraped_at": "2026-09-22T12:00:00Z",
            "metadata": {
                "variant": {"storage": "256 GB", "color": "Black Titanium"},
                "specifications": {
                    "Memoria Interna": "256 GB",
                    "Cor": "Black Titanium",
                },
            },
        }
    )
    ident = identity_from_price_item(reference)
    print("queries", build_search_queries(ident)[:8], flush=True)
    print("ref attrs", ident.variant_attrs, "mpn", ident.mpn_display, flush=True)

    # Also try direct search with progressive queries
    search_report = []
    for q in build_search_queries(ident)[:5]:
        t1 = time.time()
        try:
            hits = search.search("nissei", q, limit=5)
            search_report.append(
                {
                    "query": q,
                    "ms": round((time.time() - t1) * 1000),
                    "hits": [{"title": h.title, "url": h.url} for h in hits],
                }
            )
            print("SEARCH", q, len(hits), flush=True)
            for h in hits:
                print(" ", (h.title or "")[:80], h.url, flush=True)
            if any(
                h.url and "s25-ultra" in h.url.lower() and "s26" not in h.url.lower()
                for h in hits
            ):
                break
        except Exception as exc:  # noqa: BLE001
            search_report.append(
                {"query": q, "error": type(exc).__name__, "message": str(exc)[:200]}
            )

    print("MATCH", flush=True)
    t2 = time.time()
    matcher = ProductMatchService(scrape_service=scrape, search_service=search)
    result = matcher.match_from_item(
        reference,
        stores=["nissei"],
        include_review=False,
        persist=False,
        include_images=False,
        max_candidates_per_store=5,
    )
    payload = {
        "ref_build_ms": round((time.time() - t0) * 1000),
        "match_ms": round((time.time() - t2) * 1000),
        "queries": build_search_queries(ident)[:8],
        "search_report": search_report,
        "matches": [
            {
                "store": m.store,
                "decision": str(m.decision),
                "confidence": str(m.confidence),
                "url": m.product.url,
                "title": m.product.title,
                "product_id": m.product.product_id,
                "sku": m.product.sku,
                "variant": m.product.variant,
                "search_query": m.search_query,
                "reasons": [r.model_dump() for r in m.reasons],
                "meta_variant": (m.product.metadata or {}).get("variant"),
            }
            for m in result.matches
        ],
        "unmatched_stores": result.unmatched_stores,
        "errors": [e.model_dump() for e in result.errors],
    }
    # Also scrape parent URL as candidate detail probe
    print("PROBE parent URL", flush=True)
    parent = scrape.scrape(
        "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual",
        include_images=False,
    )
    parent_ident = identity_from_price_item(parent)
    payload["parent_probe"] = {
        "sku": parent.sku,
        "variant": parent.variant,
        "attrs": parent_ident.variant_attrs,
        "decision_vs_ref": None,
    }
    from scout_api.modules.matching.identity import critical_identity_conflict

    payload["parent_probe"]["decision_vs_ref"] = critical_identity_conflict(
        ident, parent_ident
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
