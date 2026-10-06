"""Focused live retest: Mercado Livre identity-only search after auth-wall fix."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


SOURCE_TITLE = "Placa de Vídeo Gigabyte GeForce RTX 5060"


def main() -> int:
    reference = identity_reference_item(SOURCE_TITLE, category="gpu")
    identity = identity_from_price_item(reference)
    queries = build_search_queries(identity)
    print("identity", identity.brand, identity.model, identity.variant_attrs)
    print("queries", queries)

    search = StoreSearchService()
    report: dict = {"queries": queries, "search": {}, "match": None}

    for query in queries[:2]:
        try:
            cands = search.search("mercadolivre", query, limit=5)
            report["search"][query] = {
                "count": len(cands),
                "candidates": [
                    {
                        "title": (c.title or "")[:120],
                        "url": c.url,
                        "product_id": c.product_id,
                    }
                    for c in cands
                ],
            }
            print(f"search q={query!r} n={len(cands)}")
            for c in cands[:5]:
                print(" -", c.product_id, (c.title or "")[:80], c.url[:100])
            if cands:
                break
        except (RequestError, ParseError) as exc:
            report["search"][query] = {
                "error": {
                    "type": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                    "message": str(exc)[:400],
                }
            }
            print("search_error", getattr(exc, "code", None), exc)

    matcher = ProductMatchService(search_service=search)
    try:
        resp = matcher.match_from_item(
            reference,
            stores=["mercadolivre"],
            include_review=True,
            persist=False,
            max_candidates_per_store=5,
        )
        report["match"] = {
            "matches": [
                {
                    "decision": m.decision,
                    "confidence": str(m.confidence),
                    "query": m.search_query,
                    "url": m.product.url,
                    "title": m.product.title,
                    "product_id": m.product.product_id,
                }
                for m in resp.matches
            ],
            "unmatched": list(resp.unmatched_stores),
            "errors": [e.model_dump() for e in resp.errors],
        }
        print("MATCH", report["match"])
    except Exception as exc:  # noqa: BLE001
        report["match"] = {"fatal": f"{type(exc).__name__}: {exc}"}
        print("MATCH_FATAL", exc)

    out = (
        ROOT
        / "data"
        / "live-match-reports"
        / f"ml_rtx5060_retest_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", out)

    matched = bool(report.get("match") and report["match"].get("matches"))
    return 0 if matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
