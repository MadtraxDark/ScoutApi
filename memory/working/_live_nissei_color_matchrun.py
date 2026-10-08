"""Live MatchRun — Nissei color identity (Sage title format)."""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from scout_api.modules.crawler.core.browser_health import (
    reset_browser_circuit_for_tests,
)
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.core.store_capability_health import (
    reset_store_capability_circuits_for_tests,
)
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    identity_from_price_item,
    identity_reference_item,
    normalize_variant_value,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
OUT = Path("memory/working/_live_nissei_color_matchrun.json")
TITLE = "Apple iPhone 17 MG6C4VC/A A3519 256GB / eSIM - Sage"


def main() -> None:
    # --- Unit-path regression on the exact Nissei title format ---
    ref_item = identity_reference_item(
        "Apple iPhone 17 256GB Sage",
        brand="Apple",
        model="iPhone 17",
        category="smartphone",
    )
    ref_item = ref_item.model_copy(
        update={
            "variant": "color: Sage; storage: 256 GB",
            "metadata": {
                "variant": {"color": "Sage", "storage": "256 GB"},
                "category": "smartphone",
            },
        }
    )
    rid = identity_from_price_item(ref_item)
    cand = ProductPriceItem(
        store="nissei",
        country="PY",
        product_id="synthetic-sage",
        title=TITLE,
        brand="APPLE",
        model="iPhone 17 Sage",
        variant="color: Sage; storage: 256 GB",
        url="https://nissei.com/br/apple-iphone-17-mg6c4vc-a-a3519-256gb-esim-sage",
        canonical_url="https://nissei.com/br/apple-iphone-17-mg6c4vc-a-a3519-256gb-esim-sage",
        currency="USD",
        price=Decimal("990"),
        available=True,
        availability="available",
        metadata={
            "variant": {"color": "Sage", "storage": "256 GB"},
            "category": "smartphone",
        },
    )
    cid = identity_from_price_item(cand)
    score = MatchingEngine().score(rid, cid)
    synthetic = {
        "raw_title": TITLE,
        "ref_model": rid.model,
        "cand_model": cid.model,
        "cand_attrs": cid.variant_attrs,
        "cand_mpn": cid.mpn,
        "cand_model_numbers": sorted(cid.model_numbers),
        "connectivity": cid.connectivity,
        "market_variant": cid.market_variant,
        "canonical_color": normalize_variant_value(
            "color", cid.variant_attrs.get("color", "")
        ),
        "decision": score.decision,
        "confidence": float(score.confidence),
        "reasons": [(r.code, r.detail) for r in score.reasons],
    }
    print("SYNTHETIC", json.dumps(synthetic, ensure_ascii=False, indent=2), flush=True)

    # --- Live Nissei MatchRun (best available iPhone 17 sibling) ---
    get_shared_html_fetcher.cache_clear()
    reset_browser_circuit_for_tests()
    reset_store_capability_circuits_for_tests()
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
        ref_item,
        stores=["nissei"],
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=5,
    )
    payload = json.loads(result.model_dump_json())
    payload["_meta"] = {
        "synthetic": synthetic,
        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, flush=True)
    for m in payload.get("matches") or []:
        print(
            "MATCH",
            m.get("decision"),
            m.get("confidence"),
            (m.get("product") or {}).get("title"),
            flush=True,
        )
    print("ERRORS", payload.get("errors"), flush=True)


if __name__ == "__main__":
    main()
