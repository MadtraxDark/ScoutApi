"""Diagnose Amazon BR/US match pipeline for S25 Ultra vs ground-truth ASIN."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
    serp_candidate_text,
)
from scout_api.modules.matching.product_match_service import (
    ProductMatchService,
    _serp_title_reject_reason,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


TITLE = (
    "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
    '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
)
# Ground truth for regression only — never used to force match.
GT_ASIN_BR = "B0DSYJCY45"
STORES = ["amazon_br", "amazon_us"]


def _asin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    import re

    m = re.search(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})", url, re.I)
    return m.group(1).upper() if m else None


def diagnose_store(store: str, ref_identity, queries, search, scrape, engine) -> dict:
    row: dict = {"store": store, "queries": []}
    for query in queries[:5]:
        t0 = time.perf_counter()
        try:
            cands = search.search(store, query, limit=8)
            err = None
        except Exception as exc:  # noqa: BLE001
            cands = []
            err = f"{type(exc).__name__}: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        entries = []
        gt_seen = False
        for c in cands:
            asin = (c.product_id or _asin_from_url(c.url) or "").upper()
            text = serp_candidate_text(c)
            reject = _serp_title_reject_reason(ref_identity, title=text)
            if asin == GT_ASIN_BR:
                gt_seen = True
            entries.append(
                {
                    "asin": asin or None,
                    "title": (text or "")[:140],
                    "url": canonicalize_url(c.url) if c.url else None,
                    "prefilter": reject or "pass",
                    "is_gt": asin == GT_ASIN_BR,
                }
            )
        row["queries"].append(
            {
                "query": query,
                "ms": round(ms, 1),
                "count": len(cands),
                "gt_in_serp": gt_seen,
                "error": err,
                "candidates": entries,
            }
        )
        if gt_seen:
            break

    # Full scrape + score for GT ASIN on BR only
    if store == "amazon_br":
        url = f"https://www.amazon.com.br/dp/{GT_ASIN_BR}"
        t0 = time.perf_counter()
        try:
            product = scrape.scrape(url)
            scrape_err = None
        except Exception as exc:  # noqa: BLE001
            product = None
            scrape_err = f"{type(exc).__name__}: {exc}"
        scrape_ms = (time.perf_counter() - t0) * 1000
        score_info = None
        if product is not None:
            cand_id = identity_from_price_item(product)
            score = engine.score(ref_identity, cand_id)
            score_info = {
                "decision": score.decision,
                "confidence": str(score.confidence),
                "reasons": [r.model_dump() for r in score.reasons],
                "title": product.title,
                "variant": product.variant,
                "product_id": product.product_id,
                "cand_model": cand_id.model,
                "cand_attrs": dict(cand_id.variant_attrs),
            }
        row["gt_scrape"] = {
            "url": url,
            "ms": round(scrape_ms, 1),
            "error": scrape_err,
            "score": score_info,
        }

    # End-to-end match for this store
    t0 = time.perf_counter()
    svc = ProductMatchService(scrape_service=scrape, search_service=search)
    ref = identity_reference_item(TITLE, brand="Samsung", category="smartphone")
    resp = svc.match_from_item(
        ref,
        stores=[store],
        persist=False,
        include_review=True,
        max_candidates_per_store=5,
        clear_reference_price=True,
    )
    row["match"] = {
        "ms": round((time.perf_counter() - t0) * 1000, 1),
        "hits": [
            {
                "decision": h.decision,
                "confidence": str(h.confidence),
                "query": h.search_query,
                "asin": h.product.product_id if h.product else None,
                "title": (h.product.title if h.product else None),
                "url": (h.product.url if h.product else None),
                "reasons": [r.model_dump() for r in h.reasons],
            }
            for h in resp.matches
        ],
        "unmatched": list(resp.unmatched_stores),
        "errors": [e.model_dump() for e in resp.errors],
    }
    return row


def main() -> None:
    ref = identity_reference_item(TITLE, brand="Samsung", category="smartphone")
    ref_identity = identity_from_price_item(ref)
    queries = build_search_queries(ref_identity)
    print(
        "identity", ref_identity.brand, ref_identity.model, ref_identity.variant_attrs
    )
    print("queries", queries[:6])

    fetcher = get_shared_html_fetcher()
    search = StoreSearchService(fetcher=fetcher)
    scrape = ProductScrapeService(fetcher=fetcher)
    engine = MatchingEngine()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "title": TITLE,
        "gt_asin_br": GT_ASIN_BR,
        "identity": {
            "brand": ref_identity.brand,
            "model": ref_identity.model,
            "variant_attrs": dict(ref_identity.variant_attrs),
        },
        "queries": queries,
        "stores": {},
    }
    for store in STORES:
        print("===", store)
        report["stores"][store] = diagnose_store(
            store, ref_identity, queries, search, scrape, engine
        )
        s = report["stores"][store]
        print(
            "serp_gt",
            any(q.get("gt_in_serp") for q in s["queries"]),
            "match",
            s["match"]["hits"],
            "unmatched",
            s["match"]["unmatched"],
            "errors",
            s["match"]["errors"],
        )

    out = (
        ROOT
        / "data"
        / "live-match-reports"
        / f"amazon_s25_diag_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
