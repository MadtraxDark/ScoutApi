"""Fetch Visão VIP motherboard PDP once and write a sanitized fixture."""

from __future__ import annotations

import re
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.paraguay.visaovip import VisaoVipSpider

URL = (
    "https://www.visaovip.com/prod/placas-mae-amd/"
    "placa-mae-asus-tuf-gaming-b650m-e-wi-fi-socket-am5-ddr5/41749/"
)
OUT = Path("tests/fixtures/visaovip/product_motherboard.html")


def _sanitize(html: str) -> str:
    # Drop large analytics / third-party noise; keep Next flight + OG.
    html = re.sub(
        r"<script[^>]+(?:googletagmanager|google-analytics|facebook|tiktok|"
        r"doubleclick|hotjar|clarity)[^>]*>.*?</script>",
        "",
        html,
        flags=re.I | re.S,
    )
    return html


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    resp = get_shared_html_fetcher().fetch(URL)
    text = resp.text or ""
    OUT.write_text(_sanitize(text), encoding="utf-8")
    spider = VisaoVipSpider()
    details = spider.extract_details(
        HtmlResponse(
            str(resp.url or URL),
            body=OUT.read_bytes(),
            encoding="utf-8",
            request=Request(URL),
        )
    )
    offer = spider.extract_offer(
        HtmlResponse(
            str(resp.url or URL),
            body=OUT.read_bytes(),
            encoding="utf-8",
            request=Request(URL),
        )
    )
    print(
        {
            "wrote": str(OUT),
            "bytes": OUT.stat().st_size,
            "product_id": details.product_id,
            "sku": details.sku,
            "title": details.title,
            "brand": details.brand,
            "model": details.model,
            "price": str(offer.price),
            "currency": offer.currency,
            "available": offer.available,
        }
    )


if __name__ == "__main__":
    main()
