"""Live identity-only Product Search + Match for Gigabyte GeForce RTX 5060.

Starts from product identity only — no known KaBuM / Mercado Livre / AliExpress
URLs, IDs, sellers, or prices are fed into Search. Ground-truth stores from the
human screenshot are used only for reporting / investigation classification.

Does **not** pre-probe SERP separately from Match (avoids duplicate network).
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_order import order_stores_for_match
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


SOURCE_TITLE = "Placa de Vídeo Gigabyte GeForce RTX 5060"
# Human ground truth only — never passed to Search/Match as hints.
GROUND_TRUTH_STORES = frozenset({"kabum", "mercadolivre", "aliexpress"})


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _classify_failure(
    *,
    store: str,
    matched: bool,
    error_code: str | None,
) -> str:
    if matched:
        return "MATCH"
    if error_code in {
        "UPSTREAM_BLOCKED",
        "RATE_LIMITED",
        "CHALLENGE",
        "AUTH_WALL",
        "AUTH_REQUIRED",
        "NET_RESET",
        "TIMEOUT",
    }:
        return "BLOCKED"
    if error_code in {"SEARCH_UNSUPPORTED", "PARSE_ERROR"}:
        return "ERROR"
    if error_code and error_code not in {"NO_MATCH"}:
        return "ERROR"
    if store in GROUND_TRUTH_STORES:
        return "SEARCH_FAILURE"
    return "NO_MATCH"


def main() -> int:
    out_dir = ROOT / "data" / "live-match-reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"gigabyte_rtx5060_identity_{stamp}.json"

    reference = identity_reference_item(SOURCE_TITLE, category="gpu")
    ref_identity = identity_from_price_item(reference)
    queries = build_search_queries(ref_identity)

    implemented = [k for k, cfg in STORE_CONFIGS.items() if cfg.implemented]
    ordered = order_stores_for_match(
        implemented,
        reference_store=reference.store,
        reference_has_gtin=False,
    )

    print("=== IDENTITY-ONLY LIVE MATCH ===", flush=True)
    print("title=", SOURCE_TITLE, flush=True)
    print(
        "identity=",
        {
            "brand": ref_identity.brand,
            "model": ref_identity.model,
            "variant_attrs": ref_identity.variant_attrs,
            "gtin": ref_identity.gtin,
            "mpn": ref_identity.mpn,
        },
        flush=True,
    )
    print("queries=", queries, flush=True)
    print("stores=", ordered, flush=True)

    search = StoreSearchService()
    scrape = ProductScrapeService()
    matcher = ProductMatchService(scrape_service=scrape, search_service=search)

    t0 = time.perf_counter()
    try:
        response = matcher.match_from_item(
            reference,
            stores=ordered,
            include_review=True,
            persist=False,
            include_images=False,
            max_candidates_per_store=5,
            clear_reference_price=True,
        )
        match_error = None
    except Exception as exc:  # noqa: BLE001 — live report
        response = None
        match_error = {
            "type": type(exc).__name__,
            "message": str(exc)[:800],
            "traceback": traceback.format_exc()[-2000:],
        }
        print("MATCH_FATAL", match_error["type"], match_error["message"], flush=True)
    elapsed = time.perf_counter() - t0
    print(f"match_elapsed_s={elapsed:.1f}", flush=True)

    table_rows: list[dict[str, Any]] = []
    per_store: dict[str, Any] = {}

    if response is not None:
        matched_stores = {m.store: m for m in response.matches}
        errors_by_store = {e.store: e for e in response.errors}
        unmatched = set(response.unmatched_stores)

        for store in ordered:
            hit = matched_stores.get(store)
            err = errors_by_store.get(store)
            if hit is not None:
                status = "MATCH"
                reason = "; ".join(
                    f"{r.code}:{r.detail}" for r in (hit.reasons or [])[:4]
                )
                best = {
                    "title": hit.product.title,
                    "url": hit.product.url,
                    "product_id": hit.product.product_id,
                    "price": str(hit.product.price) if hit.product.price else None,
                    "seller": hit.product.seller,
                }
                score = str(hit.confidence)
                query = hit.search_query
                terminal = "MATCH"
            else:
                code = err.code if err else None
                msg = err.message if err else None
                failure = _classify_failure(
                    store=store,
                    matched=False,
                    error_code=code,
                )
                status = failure
                reason = msg or failure
                best = None
                score = None
                query = None
                if failure == "BLOCKED":
                    terminal = "ERROR"
                elif store in unmatched and not code:
                    terminal = "NO_MATCH"
                elif code:
                    terminal = "ERROR"
                else:
                    terminal = "NO_MATCH"

            row = {
                "store": store,
                "query": query,
                "best_candidate": (best or {}).get("title") if best else None,
                "best_url": (best or {}).get("url") if best else None,
                "match_score": score,
                "status": status,
                "terminal": terminal,
                "reason": reason,
                "ground_truth": store in GROUND_TRUTH_STORES,
            }
            table_rows.append(row)
            per_store[store] = {
                **row,
                "best": best,
                "error": err.model_dump() if err else None,
            }
            print(
                f"{store}: {status} score={score} query={query!r}",
                flush=True,
            )

    report = {
        "title": SOURCE_TITLE,
        "identity": {
            "brand": ref_identity.brand,
            "model": ref_identity.model,
            "variant_attrs": dict(ref_identity.variant_attrs),
            "gtin": ref_identity.gtin,
            "mpn": ref_identity.mpn,
        },
        "queries": queries,
        "stores": ordered,
        "match_elapsed_s": round(elapsed, 2),
        "match_error": match_error,
        "table": table_rows,
        "per_store": per_store,
        "response": response.model_dump(mode="json") if response else None,
        "notes": {
            "no_pre_probe": True,
            "include_images": False,
            "early_stop_on_auto_match": True,
            "serp_title_prefilter": True,
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print("report=", report_path, flush=True)
    return 0 if match_error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
