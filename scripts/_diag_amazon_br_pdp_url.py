"""Diagnose a single Amazon BR PDP URL."""

from __future__ import annotations

import re
import sys
import time
from urllib.parse import urlparse, urlunparse

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    has_buybox_price_signal,
    looks_like_amazon_pdp,
    looks_like_clear_oos,
)
from scout_api.modules.crawler.services.html_fetcher import (
    UrllibHtmlFetcher,
    is_amazon_robot_check,
    is_amazon_soft_error_page,
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)

RAW = sys.argv[1] if len(sys.argv) > 1 else (
    "https://www.amazon.com.br/Apple-iPhone-18-Pro-Max/dp/B0HJBCQ9B7/"
    "ref=asc_df_B0HJBCQ9B7?mcid=9672f715a4123477ac242fea66626748"
    "&tag=googleshopp00-20&linkCode=df0&language=pt_BR&psc=1"
)


def _title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()[:200]


def _analyze(label: str, resp=None, err: Exception | None = None, ms: float = 0) -> None:
    print(f"\n=== {label} ({ms:.0f} ms) ===")
    if err is not None:
        print(f"ERROR {type(err).__name__}: {err}")
        print(f"  code={getattr(err, 'code', None)}")
        print(f"  upstream_status={getattr(err, 'upstream_status', None)}")
        return
    assert resp is not None
    text = resp.text or ""
    title = _title(text)
    print(f"status={resp.status} final_url={resp.url}")
    print(f"bytes={len(text.encode('utf-8', errors='replace'))} title={title!r}")
    print(
        f"challenge={is_challenge_page(text, title=title)} "
        f"robot={is_amazon_robot_check(text, title=title)} "
        f"soft_error={is_amazon_soft_error_page(text, title=title)} "
        f"auth={is_auth_wall_page(text, url=str(resp.url), title=title)}"
    )
    print(
        f"looks_pdp={looks_like_amazon_pdp(resp)} "
        f"buybox={has_buybox_price_signal(resp)} "
        f"oos={looks_like_clear_oos(resp)}"
    )
    print(f"metrics={dict(resp.meta.get('fetch_metrics') or {})}")
    pt = resp.css("#productTitle::text").get()
    asin = resp.css("input#ASIN::attr(value), input[name='ASIN']::attr(value)").get()
    print(f"productTitle={(pt or '').strip()[:120]!r} ASIN={asin!r}")
    print("snippet:", re.sub(r"\s+", " ", text)[:350])


def main() -> None:
    print("raw_url:", RAW)
    canon = canonicalize_url(RAW)
    print("canonical:", canon)
    settings = get_settings()

    # 1) HTTP only
    http = UrllibHtmlFetcher(user_agent=settings.scraper_user_agent, timeout=45)
    t0 = time.perf_counter()
    try:
        resp = http.fetch(canon)
        _analyze("http_urllib_canonical", resp, ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        _analyze("http_urllib_canonical", err=exc, ms=(time.perf_counter() - t0) * 1000)

    # 2) Shared AmazonHttpFirst stack
    shared = get_shared_html_fetcher()
    t0 = time.perf_counter()
    try:
        resp = shared.fetch(canon)
        _analyze("shared_fetcher", resp, ms=(time.perf_counter() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        _analyze("shared_fetcher", err=exc, ms=(time.perf_counter() - t0) * 1000)

    # 3) Full offer scrape
    scrape = ProductScrapeService(fetcher=shared)
    t0 = time.perf_counter()
    try:
        item = scrape.scrape(canon)
        ms = (time.perf_counter() - t0) * 1000
        print(f"\n=== product_scrape ({ms:.0f} ms) ===")
        print(
            f"title={item.title!r} price={item.price} available={item.available} "
            f"product_id={item.product_id}"
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        print(f"\n=== product_scrape ({ms:.0f} ms) ===")
        print(f"ERROR {type(exc).__name__}: {exc}")
        print(f"  code={getattr(exc, 'code', None)}")


if __name__ == "__main__":
    main()
