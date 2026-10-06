"""Reproduce Terabyte 24707 original_price baseline before fix."""

from __future__ import annotations

import json
from pathlib import Path

from scout_api.modules.crawler.services.curl_cffi_fetcher import CurlCffiHtmlFetcher
from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

URL = (
    "https://www.terabyteshop.com.br/produto/24707/"
    "placa-mae-gigabyte-a520m-k-v2-chipset-a520-amd-am4-matx-ddr4"
)
OUT = Path("memory/working/_terabyte_probe")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    http = CurlCffiHtmlFetcher(timeout=45)
    spider = TerabyteShopSpider()
    resp = http.fetch(URL)
    (OUT / "24707.html").write_text(resp.text or "", encoding="utf-8")
    # Price box snippet
    chunks = resp.css("#topopreco, .info-price, p.precode, .precotopo").getall()
    (OUT / "24707_price_box.html").write_text("\n".join(chunks), encoding="utf-8")
    offer = spider.extract_offer(resp)
    payload = {
        "url": resp.url,
        "price": str(offer.price),
        "pix_price": str(offer.pix_price) if offer.pix_price is not None else None,
        "original_price": str(offer.original_price)
        if offer.original_price is not None
        else None,
        "installment_count": offer.installment_count,
        "installment_price": str(offer.installment_price)
        if offer.installment_price is not None
        else None,
        "discount_percentage": str(offer.discount_percentage)
        if offer.discount_percentage is not None
        else None,
        "sources": offer.metadata.get("source"),
        "pricing": offer.metadata.get("pricing"),
        "promotion": offer.metadata.get("promotion"),
        "dom": {
            "valVista": (resp.css("#valVista::text").get() or "").strip(),
            "valParc": (resp.css("#valParc::text").get() or "").strip(),
            "precode_del": (resp.css("p.precode del::text").get() or "").strip(),
            "precode_html": (resp.css("p.precode").get() or "")[:500],
        },
    }
    (OUT / "24707_before.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
