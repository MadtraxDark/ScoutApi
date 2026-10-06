"""Focused re-match after variant normalization fix + SERP diagnostics."""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.services.html_fetcher import (
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

REF_URL = (
    "https://www.magazineluiza.com.br/"
    "apple-iphone-17-256gb-preto-63-48mp-ios-5g/p/241268000/te/ip17/"
    "?seller_id=magazineluiza"
)

STORES = (
    "kabum",
    "bestbuy",
    "amazon_us",
    "nissei",
    "shoppingchina",
    "shopee",
)

OUT = Path("/home/app/live_match_report.json")
SLEEP_BETWEEN = 18

MARKERS = (
    "Algo deu errado",
    "validateCaptcha",
    "Robot Check",
    "cf-challenge",
    "Just a moment",
    "Access Denied",
    "captcha",
    "px-captcha",
    "akamai",
    "__NEXT_DATA__",
    "nenhum resultado",
    "não encontramos",
    "90309999",
)


def diagnose(store: str, query: str, fetcher: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"store": store, "query": query}
    try:
        from scout_api.modules.matching.search_adapters.registry import (
            resolve_search_adapter,
        )

        adapter = resolve_search_adapter(store)
        url = adapter.build_search_request(query).url
        out["search_url"] = url
        resp = fetcher.fetch(url)
        html = resp.text or ""
        title = (resp.css("title::text").get() or "").strip()
        out["final_url"] = getattr(resp, "url", None)
        out["page"] = {
            "title": title[:200],
            "html_len": len(html),
            "challenge": is_challenge_page(html, title=title),
            "auth_wall": is_auth_wall_page(
                html, url=str(out["final_url"] or url), title=title
            ),
            "markers": [m for m in MARKERS if m.lower() in html.lower()],
        }
        try:
            cands = adapter.parse_candidates(resp)
            out["parsed_candidates"] = len(cands)
            out["titles"] = [(c.title or "")[:100] for c in cands[:5]]
        except Exception as exc:
            out["parse_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        out["error"] = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": str(exc)[:400],
        }
    return out


def main() -> int:
    scrape = ProductScrapeService()
    search = StoreSearchService()
    engine = MatchingEngine()
    fetcher = get_shared_html_fetcher()

    print("scraping reference...", flush=True)
    reference = scrape.scrape(REF_URL, include_images=False)
    ref_id = identity_from_price_item(reference)
    queries = build_search_queries(ref_id)
    print("ref", reference.title, ref_id.variant_attrs, queries, flush=True)
    time.sleep(SLEEP_BETWEEN)

    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "reference": {
            "title": reference.title,
            "variant_attrs": ref_id.variant_attrs,
            "price": str(reference.price),
            "gtin": reference.gtin,
        },
        "queries": queries,
        "stores": {},
    }

    for store in STORES:
        print(f"\n===== {store} =====", flush=True)
        row: dict[str, Any] = {"hits": [], "errors": [], "diagnostics": None}
        store_matched = False
        for query in queries:
            if store_matched:
                break
            print("query", query, flush=True)
            time.sleep(SLEEP_BETWEEN)
            try:
                candidates = search.search(store, query, limit=3)
            except (RequestError, ParseError) as exc:
                row["errors"].append(
                    {
                        "phase": "search",
                        "code": getattr(exc, "code", None),
                        "message": str(exc)[:300],
                    }
                )
                print(
                    "search_err", getattr(exc, "code", None), str(exc)[:160], flush=True
                )
                continue
            print("candidates", len(candidates), flush=True)
            for cand in candidates:
                if canonicalize_url(cand.url) == canonicalize_url(
                    reference.canonical_url
                ):
                    continue
                time.sleep(SLEEP_BETWEEN)
                try:
                    product = scrape.scrape(cand.url, include_images=False)
                except (RequestError, ParseError) as exc:
                    row["errors"].append(
                        {
                            "phase": "scrape",
                            "code": getattr(exc, "code", None),
                            "message": str(exc)[:300],
                            "url": cand.url,
                        }
                    )
                    print("scrape_err", getattr(exc, "code", None), flush=True)
                    continue
                cid = identity_from_price_item(product)
                score = engine.score(ref_id, cid)
                hit = {
                    "decision": score.decision,
                    "confidence": str(score.confidence),
                    "reasons": [
                        {"code": r.code, "detail": r.detail, "score": r.score}
                        for r in score.reasons
                    ],
                    "title": product.title,
                    "product_id": product.product_id,
                    "gtin": product.gtin,
                    "variant_attrs": cid.variant_attrs,
                    "price": str(product.price) if product.price is not None else None,
                    "url": product.url,
                    "query": query,
                }
                print(
                    "score",
                    score.decision,
                    score.confidence,
                    (product.title or "")[:70],
                    cid.variant_attrs,
                    flush=True,
                )
                row["hits"].append(hit)
                if score.decision == "auto_match":
                    store_matched = True
                    break
            if store_matched:
                break

        if not any(h["decision"] == "auto_match" for h in row["hits"]):
            time.sleep(SLEEP_BETWEEN)
            row["diagnostics"] = diagnose(store, queries[0], fetcher)
            print(
                "diag",
                json.dumps(row["diagnostics"], ensure_ascii=False)[:400],
                flush=True,
            )

        report["stores"][store] = row

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", OUT, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
