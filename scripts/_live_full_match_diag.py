"""Live full-store product match + failure diagnostics (no persist).

Reference: Magalu iPhone 17 256GB Preto.
Writes JSON report under data/live-match-reports/.
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

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.services.html_fetcher import (
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.services.store_resolver import (
    stores_supporting_search,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.store_search_order import order_stores_for_match
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]


REF_URL = (
    "https://www.magazineluiza.com.br/"
    "apple-iphone-17-256gb-preto-63-48mp-ios-5g/p/241268000/te/ip17/"
    "?seller_id=magazineluiza"
)

MARKERS = (
    "Algo deu errado",
    "Sorry, we just need to make sure you're not a robot",
    "validateCaptcha",
    "Robot Check",
    "cf-challenge",
    "cf-browser-verification",
    "Just a moment",
    "Access Denied",
    "unusual traffic",
    "captcha",
    "challenge-platform",
    "ap/signin",
    "signin",
    "login",
    "verificar",
    "anti-bot",
    "px-captcha",
    "PerimeterX",
    "datadome",
    "akamai",
    "Incapsula",
    "rate limit",
    "too many requests",
    "__NEXT_DATA__",
    "no results",
    "nenhum resultado",
    "não encontramos",
    "0 results",
)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _diagnose_html(html: str, *, url: str, title: str) -> dict[str, Any]:
    low = html.lower()
    markers_hit = [m for m in MARKERS if m.lower() in low]
    return {
        "url": url,
        "title": title[:200],
        "html_len": len(html),
        "challenge": is_challenge_page(html, title=title),
        "auth_wall": is_auth_wall_page(html, url=url, title=title),
        "markers": markers_hit,
        "has_next_data": "__NEXT_DATA__" in html,
        "digit_ratio": (sum(ch.isdigit() for ch in html) / max(len(html), 1)),
    }


def diagnose_store_search(
    store: str,
    query: str,
    *,
    fetcher: Any,
    search: StoreSearchService,
) -> dict[str, Any]:
    out: dict[str, Any] = {"store": store, "query": query}
    try:
        from scout_api.modules.matching.search_adapters.registry import (
            resolve_search_adapter,
        )

        adapter = resolve_search_adapter(store)
        search_url = adapter.build_search_request(query).url
        fetch_url = search_url
        out["search_url"] = search_url
        out["fetch_url"] = fetch_url
        started = time.perf_counter()
        response = fetcher.fetch(fetch_url)
        elapsed = time.perf_counter() - started
        html = response.text or ""
        title = (response.css("title::text").get() or "").strip()
        out["fetch_seconds"] = round(elapsed, 2)
        out["final_url"] = getattr(response, "url", None)
        out["status"] = getattr(response, "status", None)
        out["page"] = _diagnose_html(
            html, url=str(out["final_url"] or fetch_url), title=title
        )
        try:
            candidates = adapter.parse_candidates(response)
            out["parsed_candidates"] = len(candidates)
            out["candidate_titles"] = [(c.title or "")[:120] for c in candidates[:5]]
            out["candidate_urls"] = [c.url for c in candidates[:5]]
        except Exception as exc:
            out["parse_error"] = f"{type(exc).__name__}: {exc}"
        # Cross-check search service
        try:
            via_svc = search.search(store, query, limit=5)
            out["service_candidates"] = len(via_svc)
        except (RequestError, ParseError) as exc:
            out["service_error"] = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc)[:400],
            }
    except (RequestError, ParseError) as exc:
        out["fetch_error"] = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc)[:500],
        }
    except Exception as exc:
        out["unexpected"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-1500:]
    return out


def main() -> int:
    out_dir = ROOT / "data" / "live-match-reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"full_match_{stamp}.json"

    print("=== FULL LIVE MATCH ===", flush=True)
    print("reference=", REF_URL, flush=True)

    scrape = ProductScrapeService()
    search = StoreSearchService()
    fetcher = get_shared_html_fetcher()
    service = ProductMatchService(scrape_service=scrape, search_service=search)

    available = list(stores_supporting_search())
    print("stores_supporting_search=", available, flush=True)

    request = MatchRequest(
        reference_url=REF_URL,  # type: ignore[arg-type]
        stores=None,
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=3,
    )

    t0 = time.perf_counter()
    try:
        response = service.match(request)
        match_error = None
    except Exception as exc:
        response = None
        match_error = {
            "type": type(exc).__name__,
            "message": str(exc)[:800],
            "traceback": traceback.format_exc()[-2000:],
        }
        print("MATCH_FATAL", match_error["type"], match_error["message"], flush=True)
    elapsed_match = time.perf_counter() - t0
    print(f"match_elapsed_s={elapsed_match:.1f}", flush=True)

    report: dict[str, Any] = {
        "started_at": stamp,
        "reference_url": REF_URL,
        "match_elapsed_seconds": round(elapsed_match, 2),
        "match_error": match_error,
        "stores_available": available,
        "per_store": {},
        "diagnostics": {},
    }

    if response is not None:
        ref = response.reference
        ref_id = identity_from_price_item(ref)
        queries = build_search_queries(ref_id)
        ordered = order_stores_for_match(
            available,
            reference_store=ref.store,
            reference_has_gtin=bool(ref_id.gtin),
        )
        report["search_order"] = ordered
        report["queries"] = queries
        report["discovered_gtin"] = response.discovered_gtin
        report["gtin_source"] = response.gtin_source
        report["reference"] = {
            "store": ref.store,
            "product_id": ref.product_id,
            "title": ref.title,
            "brand": ref.brand,
            "model": ref.model,
            "variant": ref.variant,
            "gtin": ref.gtin,
            "price": str(ref.price) if ref.price is not None else None,
            "currency": ref.currency,
            "url": ref.url,
            "identity": {
                "gtin": ref_id.gtin,
                "brand": ref_id.brand,
                "model": ref_id.model,
                "variant_attrs": ref_id.variant_attrs,
                "title_normalized": ref_id.title_normalized,
            },
        }
        report["unmatched_stores"] = list(response.unmatched_stores)
        report["errors"] = [e.model_dump() for e in response.errors]
        report["matches"] = []
        for hit in response.matches:
            report["matches"].append(
                {
                    "store": hit.store,
                    "decision": hit.decision,
                    "confidence": str(hit.confidence),
                    "reasons": [r.model_dump() for r in hit.reasons],
                    "search_query": hit.search_query,
                    "product": {
                        "product_id": hit.product.product_id,
                        "title": hit.product.title,
                        "brand": hit.product.brand,
                        "model": hit.product.model,
                        "variant": hit.product.variant,
                        "gtin": hit.product.gtin,
                        "price": str(hit.product.price)
                        if hit.product.price is not None
                        else None,
                        "currency": hit.product.currency,
                        "url": hit.product.url,
                        "availability": hit.product.availability,
                    },
                }
            )
            print(
                f"MATCH {hit.store} decision={hit.decision} "
                f"conf={hit.confidence} title={(hit.product.title or '')[:70]}",
                flush=True,
            )

        # Build per-store summary skeleton
        _matched_stores = {h.store for h in response.matches}
        error_by_store = {e.store: e for e in response.errors}
        for store in ordered:
            row: dict[str, Any] = {"store": store, "status": "unknown"}
            hits = [h for h in response.matches if h.store == store]
            if hits:
                h = hits[0]
                row["status"] = "matched"
                row["decision"] = h.decision
                row["confidence"] = str(h.confidence)
                row["reasons"] = [r.model_dump() for r in h.reasons]
                row["product_title"] = h.product.title
                row["product_id"] = h.product.product_id
                row["gtin"] = h.product.gtin
                row["search_query"] = h.search_query
            elif store in response.unmatched_stores:
                row["status"] = "unmatched"
                if store in error_by_store:
                    err = error_by_store[store]
                    row["error"] = {
                        "code": err.code,
                        "message": err.message,
                    }
            report["per_store"][store] = row

        # Deep diagnostics for unmatched / error stores
        primary_query = queries[0] if queries else "iphone 17 256gb preto"
        print("=== DIAGNOSTICS FOR FAILURES ===", flush=True)
        for store in ordered:
            row = report["per_store"].get(store, {})
            if row.get("status") == "matched" and row.get("decision") == "auto_match":
                continue
            print(f"--- diagnose {store} ---", flush=True)
            diag = diagnose_store_search(
                store, primary_query, fetcher=fetcher, search=search
            )
            report["diagnostics"][store] = diag
            # If no candidates on primary, try a second query when available
            if (
                diag.get("parsed_candidates", 0) == 0
                and len(queries) > 1
                and not diag.get("fetch_error")
            ):
                diag2 = diagnose_store_search(
                    store, queries[1], fetcher=fetcher, search=search
                )
                report["diagnostics"][f"{store}__q2"] = diag2
            time.sleep(2)

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print("REPORT=", report_path, flush=True)
    print("DONE", flush=True)
    return 0 if match_error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
