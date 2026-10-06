"""Diagnose iPhone 18 Pro Max PDP buybox / scrape failure."""

from __future__ import annotations

import time

from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    has_buybox_price_signal,
    looks_like_amazon_pdp,
    looks_like_clear_oos,
)
from scout_api.modules.crawler.services.html_fetcher import is_amazon_soft_error_page
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.amazon.parsing import prepare_amazon_fetch_url

RAW = (
    "https://www.amazon.com.br/Apple-iPhone-18-Pro-Max/dp/B0HJBCQ9B7/"
    "ref=asc_df_B0HJBCQ9B7?mcid=9672f715a4123477ac242fea66626748"
    "&tag=googleshopp00-20&linkCode=df0&psc=1&language=pt_BR"
)


def main() -> None:
    prepared = prepare_amazon_fetch_url(RAW, "amazon.com.br")
    print("raw:", RAW[:120], "...")
    print("prepared:", prepared)

    fetcher = get_shared_html_fetcher()
    t0 = time.perf_counter()
    resp = fetcher.fetch(prepared)
    ms = (time.perf_counter() - t0) * 1000
    text = resp.text or ""
    print("fetch_ms", round(ms, 1), "bytes", len(text.encode("utf-8", errors="replace")))
    print("final_url", resp.url)
    print("metrics", dict(resp.meta.get("fetch_metrics") or {}))
    print(
        "soft_error",
        is_amazon_soft_error_page(text),
        "pdp",
        looks_like_amazon_pdp(resp),
        "buybox",
        has_buybox_price_signal(resp),
        "oos",
        looks_like_clear_oos(resp),
    )
    print("title", (resp.css("#productTitle::text").get() or "").strip()[:100])
    for sel in (
        "#ppd .priceToPay .a-offscreen",
        "#ppd .apex-pricetopay-value .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#apex_desktop .apex-pricetopay-value .a-offscreen",
        "#buybox .a-price .a-offscreen",
        "#qualifiedBuybox .a-price .a-offscreen",
    ):
        vals = [t.strip() for t in resp.css(f"{sel}::text").getall() if t and t.strip()]
        if vals:
            print(" ", sel, "=>", vals[:3])

    scrape = ProductScrapeService(fetcher=fetcher)
    t0 = time.perf_counter()
    try:
        item = scrape.scrape(RAW)
        print(
            "SCRAPE_OK",
            round((time.perf_counter() - t0) * 1000, 1),
            "ms",
            item.price,
            item.product_id,
            (item.title or "")[:80],
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "SCRAPE_FAIL",
            round((time.perf_counter() - t0) * 1000, 1),
            "ms",
            type(exc).__name__,
            exc,
            getattr(exc, "code", None),
        )


if __name__ == "__main__":
    main()
