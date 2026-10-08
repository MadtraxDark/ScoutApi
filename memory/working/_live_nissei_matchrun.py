"""Live MatchRun for Nissei only — diagnostics for SERP → PDP → matcher."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from scout_api.modules.crawler.core.browser_health import (
    reset_browser_circuit_for_tests,
)
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.core.store_capability_health import (
    reset_store_capability_circuits_for_tests,
)
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
# Keep match diagnostics visible.
for name in (
    "scout_api.modules.matching.product_match_service",
    "scout_api.modules.matching.store_search_service",
):
    logging.getLogger(name).setLevel(logging.INFO)

OUT = Path("memory/working/_live_nissei_matchrun.json")


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    reset_browser_circuit_for_tests()
    reset_store_capability_circuits_for_tests()

    ref = identity_reference_item(
        "Apple iPhone 17 256GB Black",
        brand="Apple",
        model="iPhone 17",
        category="smartphone",
    )
    ref = ref.model_copy(
        update={
            "variant": "color: Black; storage: 256 GB",
            "metadata": {
                **(ref.metadata or {}),
                "variant": {"storage": "256 GB", "color": "Black"},
            },
        }
    )
    identity = identity_from_price_item(ref)
    queries = build_search_queries(identity, locale="en-US")
    print("QUERIES", queries[:8], flush=True)

    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    search = StoreSearchService()
    matcher = ProductMatchService(scrape_service=scrape, search_service=search)

    t0 = time.perf_counter()
    result = matcher.match_from_item(
        ref,
        stores=["nissei"],
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=5,
    )
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    payload = json.loads(result.model_dump_json())
    payload["_meta"] = {
        "total_ms": total_ms,
        "queries": queries[:10],
        "reference_title": ref.title,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, "total_ms", total_ms, flush=True)

    # Compact terminal summary
    for store in payload.get("stores") or payload.get("store_outcomes") or []:
        print("STORE", json.dumps(store, ensure_ascii=False)[:2000], flush=True)
    for offer in payload.get("offers") or []:
        if offer.get("store") == "nissei":
            print(
                "OFFER",
                offer.get("decision"),
                offer.get("confidence"),
                (offer.get("title") or "")[:100],
                offer.get("url"),
                flush=True,
            )
    print(
        "MATCHES",
        len(payload.get("matches") or []),
        "ERRORS",
        payload.get("errors"),
        flush=True,
    )


if __name__ == "__main__":
    main()
