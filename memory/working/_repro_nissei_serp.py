"""Reproduce Nissei SERP through the real StoreSearchService path."""

from __future__ import annotations

import time
import traceback

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.services.html_fetcher import (
    is_challenge_page,
    locale_for_url,
    warmup_url_for,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.matching.search_adapters.paraguay.nissei import (
    NisseiSearchAdapter,
)
from scout_api.modules.matching.store_search_service import StoreSearchService


def probe_http_raw(url: str) -> None:
    import urllib.request

    print("=== RAW HTTP ===", url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(8000).decode("utf-8", errors="replace")
            print("status", resp.status, "len", len(body))
            print("challenge?", is_challenge_page(body))
            print(
                "title snippet",
                body[body.find("<title>") : body.find("</title>") + 8][:120],
            )
            print("has product-item?", "product-item" in body)
            print("cf markers", "cloudflare" in body.lower(), "Just a moment" in body)
    except Exception as exc:  # noqa: BLE001
        print("RAW HTTP FAIL", type(exc).__name__, exc)


def probe_fetcher(url: str) -> None:
    print("=== FETCHER (shared HtmlFetcher) ===", url)
    print("locale", locale_for_url(url), "warmup", warmup_url_for(url))
    fetcher = get_shared_html_fetcher()
    t0 = time.perf_counter()
    try:
        response = fetcher.fetch(url)
        ms = (time.perf_counter() - t0) * 1000
        text = response.text or ""
        print("ok ms", round(ms, 1), "len", len(text), "final_url", response.url)
        print("challenge?", is_challenge_page(text))
        print("has product-item?", "product-item" in text)
        print("product-item-link count", text.count("product-item-link"))
        adapter = NisseiSearchAdapter()
        cands = adapter.parse_candidates(response)
        print("parsed", len(cands))
        for c in cands[:5]:
            print(" -", (c.title or "")[:80], c.url)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        print(
            "FETCHER FAIL",
            round(ms, 1),
            "ms",
            type(exc).__name__,
            getattr(exc, "code", None),
            exc,
        )
        traceback.print_exc()


def probe_store_search(query: str) -> None:
    print("=== StoreSearchService.search ===", query)
    adapter = NisseiSearchAdapter()
    req = adapter.build_search_request(query)
    print("request", req.url, "prefer_browser", req.prefer_browser)
    svc = StoreSearchService()
    t0 = time.perf_counter()
    try:
        cands = svc.search("nissei", query, limit=5)
        print("OK", round((time.perf_counter() - t0) * 1000, 1), "ms", len(cands))
        for c in cands[:5]:
            print(" -", (c.title or "")[:80], c.url)
    except RequestError as exc:
        print(
            "SEARCH FAIL",
            round((time.perf_counter() - t0) * 1000, 1),
            "ms",
            exc.code,
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        print("SEARCH FAIL", type(exc).__name__, exc)
        traceback.print_exc()


def main() -> None:
    url = "https://nissei.com/br/catalogsearch/result/?q=apple+iphone+17+256gb+black"
    probe_http_raw(url)
    probe_fetcher(url)
    probe_store_search("apple iphone 17 256gb black")
    probe_store_search("samsung galaxy s25 ultra")


if __name__ == "__main__":
    main()
