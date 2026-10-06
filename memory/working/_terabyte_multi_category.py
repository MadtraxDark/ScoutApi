"""Multi-category live Terabyte PDP probe via curl_cffi + spider."""

from __future__ import annotations

import json
import time
from pathlib import Path

from scout_api.modules.crawler.services.curl_cffi_fetcher import CurlCffiHtmlFetcher
from scout_api.modules.crawler.services.html_fetcher import is_challenge_page
from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

# Real PDPs across categories (discovered via store search patterns / homepage).
URLS = [
    (
        "motherboard",
        "https://www.terabyteshop.com.br/produto/22809/"
        "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5",
    ),
    (
        "cpu",
        "https://www.terabyteshop.com.br/busca?str=processador+ryzen",
    ),
    (
        "gpu",
        "https://www.terabyteshop.com.br/busca?str=placa+de+video",
    ),
    (
        "ram",
        "https://www.terabyteshop.com.br/busca?str=memoria+ddr5",
    ),
    (
        "ssd",
        "https://www.terabyteshop.com.br/busca?str=ssd+nvme",
    ),
]

OUT = Path("memory/working/_terabyte_probe/multi_category.json")


def first_product_url(serp_html: str, base: str) -> str | None:
    import re
    from urllib.parse import urljoin

    for href in re.findall(
        r'href=["\']([^"\']*/produto/\d+/[^"\']*)["\']', serp_html, re.I
    ):
        if "javascript" in href.casefold():
            continue
        return urljoin(base, href)
    return None


def summarize(url: str, category: str) -> dict:
    http = CurlCffiHtmlFetcher(timeout=45.0)
    spider = TerabyteShopSpider()
    t0 = time.perf_counter()
    try:
        response = http.fetch(url)
    except Exception as exc:
        return {
            "category": category,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    ms = round((time.perf_counter() - t0) * 1000, 1)
    title = ""
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", response.text or "", re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    if is_challenge_page(response.text or "", title=title):
        return {
            "category": category,
            "url": url,
            "challenge": True,
            "ms": ms,
            "bytes": len(response.text or ""),
        }
    # If search page, pick first PDP.
    final_url = response.url
    if "/busca" in (final_url or "") or category != "motherboard":
        if "/produto/" not in (final_url or ""):
            pdp = first_product_url(response.text or "", final_url)
            if not pdp:
                return {
                    "category": category,
                    "url": url,
                    "error": "no product link on SERP",
                    "ms": ms,
                }
            t1 = time.perf_counter()
            response = http.fetch(spider.prepare_fetch_url(pdp))
            ms += round((time.perf_counter() - t1) * 1000, 1)
            final_url = response.url
    try:
        offer = spider.extract_offer(response)
        details = spider.extract_details(response)
        images = spider.extract_images(response)
    except Exception as exc:
        return {
            "category": category,
            "url": final_url,
            "parser_error": f"{type(exc).__name__}: {exc}",
            "code": getattr(exc, "code", None),
            "ms": ms,
            "bytes": len(response.text or ""),
            "title": title[:120],
        }
    return {
        "category": category,
        "url": final_url,
        "ms": ms,
        "bytes": len(response.text or ""),
        "product_id": offer.product_id,
        "title": details.title,
        "brand": details.brand,
        "model": details.model,
        "price": str(offer.price),
        "pix_price": str(offer.pix_price) if offer.pix_price is not None else None,
        "original_price": str(offer.original_price)
        if offer.original_price is not None
        else None,
        "installments": f"{offer.installment_count}x {offer.installment_price}"
        if offer.installment_count
        else None,
        "availability": offer.availability,
        "spec_count": len(details.specifications),
        "images": len(images),
        "promotion": bool(offer.metadata.get("promotion")),
        "fetch_strategy": (response.meta.get("fetch_metrics") or {}).get(
            "fetch_strategy"
        ),
    }


def main() -> None:
    results = [summarize(url, cat) for cat, url in URLS]
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
