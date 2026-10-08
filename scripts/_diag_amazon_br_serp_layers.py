"""Capture Amazon BR SERP failure layers inside Docker match-runner."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

from scrapy.http import HtmlResponse

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.exceptions import RequestError
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

OUT = Path("/tmp/amazon_br_serp_layers.json")
HTML_DIR = Path("/tmp/amazon_br_serp_html")
HTML_DIR.mkdir(parents=True, exist_ok=True)

QUERY = sys.argv[1] if len(sys.argv) > 1 else "samsung galaxy s25 ultra 256gb"
URL = f"https://www.amazon.com.br/s?k={quote_plus(QUERY)}"


def analyze(
    label: str, resp: HtmlResponse | None, err: Exception | None = None
) -> dict:
    row: dict = {"label": label, "ok": err is None}
    if err is not None:
        row["error"] = f"{type(err).__name__}: {err}"
        row["error_code"] = getattr(err, "code", None)
        row["upstream_status"] = getattr(err, "upstream_status", None)
        return row
    assert resp is not None
    text = resp.text or ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip()[:200] if title_m else None
    cards = resp.css(
        "div[data-component-type='s-search-result'], div.s-result-item[data-asin]"
    )
    parsed = parse_amazon_search_results(
        resp, host="amazon.com.br", source="diag", limit=10
    )
    path = HTML_DIR / f"{label}.html"
    path.write_text(text, encoding="utf-8", errors="replace")
    row.update(
        {
            "status": int(resp.status),
            "final_url": str(resp.url),
            "bytes": len(text.encode("utf-8", errors="replace")),
            "title": title,
            "challenge": is_challenge_page(text),
            "robot": is_amazon_robot_check(text),
            "auth_wall": is_auth_wall_page(text, url=str(resp.url)),
            "looks_like_search": looks_like_amazon_search(resp),
            "css_cards": len(cards),
            "data_asin_attrs": len(re.findall(r"data-asin=", text, re.I)),
            "dp_links": len(re.findall(r"/dp/[A-Z0-9]{10}", text, re.I)),
            "parsed": [c.product_id for c in parsed],
            "classification": classify_amazon_empty_result(resp),
            "metrics": dict(resp.meta.get("fetch_metrics") or {}),
            "html_path": str(path),
            "snippet": re.sub(r"\s+", " ", text)[:700],
        }
    )
    return row


def main() -> None:
    settings = get_settings()
    report: dict = {"query": QUERY, "url": URL, "layers": []}

    # Layer 1: raw urllib HTTP
    http = UrllibHtmlFetcher(user_agent=settings.scraper_user_agent, timeout=45)
    t0 = time.perf_counter()
    try:
        resp = http.fetch(URL)
        report["layers"].append(
            {
                **analyze("http_urllib", resp),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        )
    except Exception as exc:  # noqa: BLE001
        report["layers"].append(
            {
                **analyze("http_urllib", None, exc),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        )

    # Layer 2: shared AmazonHttpFirst (HTTP→browser)
    shared = get_shared_html_fetcher()
    t0 = time.perf_counter()
    try:
        resp = shared.fetch(URL)
        report["layers"].append(
            {
                **analyze("shared_amazon_http_first", resp),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        )
    except Exception as exc:  # noqa: BLE001
        report["layers"].append(
            {
                **analyze("shared_amazon_http_first", None, exc),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        )

    # Layer 3: StoreSearchService
    search = StoreSearchService(fetcher=shared)
    t0 = time.perf_counter()
    try:
        cands = search.search("amazon_br", QUERY, limit=8)
        report["layers"].append(
            {
                "label": "store_search_service",
                "ok": True,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "count": len(cands),
                "asins": [c.product_id for c in cands],
                "titles": [(c.title or "")[:80] for c in cands],
            }
        )
    except RequestError as exc:
        report["layers"].append(
            {
                "label": "store_search_service",
                "ok": False,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": str(exc),
                "error_code": exc.code,
            }
        )
    except Exception as exc:  # noqa: BLE001
        report["layers"].append(
            {
                "label": "store_search_service",
                "ok": False,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
