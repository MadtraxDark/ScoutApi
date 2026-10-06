"""Multi-category Terabyte pricing semantics after card/Pix fix."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from scout_api.modules.crawler.services.curl_cffi_fetcher import CurlCffiHtmlFetcher
from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

CASES = [
    (
        "motherboard",
        "https://www.terabyteshop.com.br/produto/22809/"
        "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5",
    ),
    ("cpu", "https://www.terabyteshop.com.br/busca?str=processador+ryzen"),
    ("gpu", "https://www.terabyteshop.com.br/busca?str=placa+de+video"),
    ("ram", "https://www.terabyteshop.com.br/busca?str=memoria+ddr5"),
    ("ssd", "https://www.terabyteshop.com.br/busca?str=ssd+nvme"),
]
OUT = Path("memory/working/_terabyte_probe/pricing_multi_category.json")


def first_product(html: str, base: str) -> str | None:
    for href in re.findall(r"""href=["']([^"']*/produto/\d+/[^"']*)["']""", html, re.I):
        return urljoin(base, href)
    return None


def scrape(url: str, category: str) -> dict:
    http = CurlCffiHtmlFetcher(timeout=45)
    spider = TerabyteShopSpider()
    t0 = time.perf_counter()
    resp = http.fetch(url)
    if "/produto/" not in (resp.url or ""):
        pdp = first_product(resp.text or "", resp.url)
        if not pdp:
            return {"category": category, "error": "no pdp"}
        resp = http.fetch(spider.prepare_fetch_url(pdp))
    offer = spider.extract_offer(resp)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    card = offer.price
    pix = offer.pix_price
    return {
        "category": category,
        "product_id": offer.product_id,
        "url": resp.url,
        "ms": ms,
        "price_card": str(card),
        "pix_price": str(pix) if pix is not None else None,
        "original_price": str(offer.original_price)
        if offer.original_price is not None
        else None,
        "installments": (
            f"{offer.installment_count}x {offer.installment_price}"
            if offer.installment_count
            else None
        ),
        "discount_pct": str(offer.discount_percentage)
        if offer.discount_percentage is not None
        else None,
        "pix_lt_card": bool(pix is not None and pix < card),
        "original_gt_card": bool(
            offer.original_price is not None and offer.original_price > card
        ),
        "sources": offer.metadata.get("source"),
        "pricing_meta": offer.metadata.get("pricing"),
        "promotion": bool(offer.metadata.get("promotion")),
        "availability": offer.availability,
    }


def main() -> None:
    rows = [scrape(url, cat) for cat, url in CASES]
    OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
