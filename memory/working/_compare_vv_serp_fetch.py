"""Compare HTTP vs Camoufox Visão VIP SERP parse counts (no product hardcodes)."""

from __future__ import annotations

import time

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.spiders.paraguay.visaovip import VisaoVipSpider

QUERY = "ASUS TUF Gaming B650M-E WIFI"
spider = VisaoVipSpider()
url = spider.build_search_url(QUERY)


def _parse(body: bytes, final_url: str) -> int:
    resp = HtmlResponse(
        final_url, body=body, encoding="utf-8", request=Request(final_url)
    )
    return len(spider.parse_search_results(resp))


def main() -> None:
    import httpx

    t0 = time.perf_counter()
    http = httpx.get(url, follow_redirects=True, timeout=30.0)
    http_ms = int((time.perf_counter() - t0) * 1000)
    http_n = _parse(http.content, str(http.url))
    print(
        f"http status={http.status_code} ms={http_ms} "
        f"candidates={http_n} len={len(http.content)}"
    )

    from scout_api.modules.crawler.services.product_scrape_service import (
        get_shared_html_fetcher,
    )

    get_shared_html_fetcher.cache_clear()
    t1 = time.perf_counter()
    cam = get_shared_html_fetcher().fetch(url)
    cam_ms = int((time.perf_counter() - t1) * 1000)
    body = (cam.text or "").encode("utf-8", errors="replace")
    cam_n = _parse(body, str(cam.url or url))
    print(
        f"camoufox ms={cam_ms} candidates={cam_n} len={len(body)} "
        f"has_prod={'/prod/' in (cam.text or '')}"
    )


if __name__ == "__main__":
    main()
