"""Diagnose Amazon BR SERP: HTTP vs shared fetcher vs StoreSearchService.

Captures evidence for incomplete/blocked classification — not a production tool.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scrapy.http import HtmlResponse, Request

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    looks_like_amazon_search,
)
from scout_api.modules.crawler.services.html_fetcher import (
    UrllibHtmlFetcher,
    is_amazon_robot_check,
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.matching.search_adapters.amazon.parse import (
    classify_amazon_empty_result,
    parse_amazon_search_results,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "live-match-reports"
OUT.mkdir(parents=True, exist_ok=True)

QUERIES = [
    "samsung galaxy s25 ultra 256gb",
    "processador ryzen 7 5700x",
    "ssd samsung 990 evo plus 1tb",
    "iphone 16 128gb",
]

_ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})", re.I)
_DATA_ASIN_RE = re.compile(r'data-asin=["\']([A-Z0-9]{10})["\']', re.I)


def _title_from_html(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()[:200]


def _snippet(html: str, n: int = 800) -> str:
    text = re.sub(r"\s+", " ", html or "")
    return text[:n]


def _analyze_html(html: str, url: str, status: int | None = None) -> dict:
    folded = (html or "").casefold()
    asins_attr = sorted(set(_DATA_ASIN_RE.findall(html or "")))
    asins_links = sorted({m.group(1).upper() for m in _ASIN_RE.finditer(html or "")})
    resp = HtmlResponse(
        url=url,
        status=status or 200,
        body=(html or "").encode("utf-8", errors="replace"),
        encoding="utf-8",
        request=Request(url),
    )
    cards_css = resp.css(
        "div[data-component-type='s-search-result'], div.s-result-item[data-asin]"
    )
    parsed = parse_amazon_search_results(
        resp, host="amazon.com.br", source="diag", limit=20
    )
    return {
        "status": status,
        "url": url,
        "bytes": len((html or "").encode("utf-8", errors="replace")),
        "title": _title_from_html(html),
        "challenge": is_challenge_page(html or ""),
        "robot_check": is_amazon_robot_check(html or ""),
        "auth_wall": is_auth_wall_page(html or "", url=url),
        "looks_like_search": looks_like_amazon_search(resp),
        "data_asin_count": len(asins_attr),
        "data_asins_sample": asins_attr[:15],
        "dp_link_asins_sample": asins_links[:15],
        "css_card_count": len(cards_css),
        "parsed_count": len(parsed),
        "parsed_asins": [c.product_id for c in parsed],
        "classification": classify_amazon_empty_result(resp),
        "markers": {
            "s-search-result": "s-search-result" in folded,
            "data-asin": "data-asin" in folded,
            "robot check": "robot check" in folded,
            "validatecaptcha": "validatecaptcha" in folded,
            "nenhum resultado": "nenhum resultado" in folded,
            "não encontramos": "não encontramos" in folded
            or "nao encontramos" in folded,
            "awswaf": "awswaf" in folded or "token.awswaf.com" in folded,
            "continue shopping": "continue shopping" in folded,
        },
        "html_head_snippet": _snippet(html, 600),
        "html_body_snippet": _snippet(html[max(0, len(html) // 3) : len(html) // 3 + 600], 600)
        if html
        else "",
    }


def fetch_http_only(query: str) -> dict:
    settings = get_settings()
    url = f"https://www.amazon.com.br/s?k={quote_plus(query)}"
    fetcher = UrllibHtmlFetcher(user_agent=settings.scraper_user_agent, timeout=45)
    t0 = time.perf_counter()
    try:
        resp = fetcher.fetch(url)
        ms = (time.perf_counter() - t0) * 1000
        analysis = _analyze_html(resp.text or "", str(resp.url), int(resp.status))
        metrics = dict(resp.meta.get("fetch_metrics") or {})
        return {
            "ok": True,
            "ms": round(ms, 1),
            "fetch_metrics": metrics,
            "analysis": analysis,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": False,
            "ms": round(ms, 1),
            "fetch_metrics": {},
            "analysis": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": getattr(exc, "code", None),
            "url": url,
        }


def fetch_shared(query: str) -> dict:
    url = f"https://www.amazon.com.br/s?k={quote_plus(query)}"
    fetcher = get_shared_html_fetcher()
    t0 = time.perf_counter()
    try:
        resp = fetcher.fetch(url)
        ms = (time.perf_counter() - t0) * 1000
        analysis = _analyze_html(resp.text or "", str(resp.url), int(resp.status))
        metrics = dict(resp.meta.get("fetch_metrics") or {})
        # Persist a sample HTML for offline inspection
        sample_path = OUT / f"amazon_br_serp_sample_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.html"
        sample_path.write_text(resp.text or "", encoding="utf-8", errors="replace")
        return {
            "ok": True,
            "ms": round(ms, 1),
            "fetch_metrics": metrics,
            "analysis": analysis,
            "sample_html": str(sample_path),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": False,
            "ms": round(ms, 1),
            "fetch_metrics": {},
            "analysis": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": getattr(exc, "code", None),
            "url": url,
        }


def search_service(query: str) -> dict:
    search = StoreSearchService(fetcher=get_shared_html_fetcher())
    t0 = time.perf_counter()
    try:
        cands = search.search("amazon_br", query, limit=8)
        ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": True,
            "ms": round(ms, 1),
            "count": len(cands),
            "candidates": [
                {
                    "asin": c.product_id,
                    "title": (c.title or "")[:120],
                    "url": c.url,
                }
                for c in cands
            ],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": False,
            "ms": round(ms, 1),
            "count": 0,
            "candidates": [],
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": getattr(exc, "code", None),
        }


def main() -> None:
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "queries": [],
    }
    # First query: deep HTTP + shared + service
    q0 = QUERIES[0]
    print(f"=== Deep diag query={q0!r} ===", flush=True)
    http = fetch_http_only(q0)
    print(f"HTTP-only: ok={http['ok']} ms={http['ms']} err={http.get('error')}", flush=True)
    if http.get("analysis"):
        a = http["analysis"]
        print(
            f"  status={a['status']} bytes={a['bytes']} title={a['title']!r} "
            f"challenge={a['challenge']} robot={a['robot_check']} "
            f"cards={a['css_card_count']} parsed={a['parsed_count']} "
            f"class={a['classification']}",
            flush=True,
        )
    shared = fetch_shared(q0)
    print(
        f"Shared fetcher: ok={shared['ok']} ms={shared['ms']} "
        f"metrics={shared.get('fetch_metrics')} err={shared.get('error')}",
        flush=True,
    )
    if shared.get("analysis"):
        a = shared["analysis"]
        print(
            f"  status={a['status']} bytes={a['bytes']} title={a['title']!r} "
            f"challenge={a['challenge']} robot={a['robot_check']} "
            f"cards={a['css_card_count']} parsed={a['parsed_count']} "
            f"class={a['classification']} asins={a['parsed_asins'][:5]}",
            flush=True,
        )
    svc = search_service(q0)
    print(
        f"StoreSearchService: ok={svc['ok']} ms={svc['ms']} "
        f"count={svc.get('count')} err={svc.get('error')}",
        flush=True,
    )
    report["queries"].append(
        {"query": q0, "http_only": http, "shared": shared, "store_search": svc}
    )

    # Remaining: StoreSearchService only (cheaper)
    for q in QUERIES[1:]:
        print(f"=== StoreSearch query={q!r} ===", flush=True)
        svc = search_service(q)
        print(
            f"  ok={svc['ok']} ms={svc['ms']} count={svc.get('count')} "
            f"err={svc.get('error')} cands={svc.get('candidates', [])[:3]}",
            flush=True,
        )
        report["queries"].append({"query": q, "store_search": svc})

    report["finished_at"] = datetime.now(UTC).isoformat()
    out = OUT / f"amazon_br_serp_diag_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    # Strip huge snippets duplication for JSON size if needed — keep analysis
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}", flush=True)


if __name__ == "__main__":
    main()
