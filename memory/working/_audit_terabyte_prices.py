"""Inspect Terabyte price sources on saved PDP HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

HTML = Path("memory/working/_terabyte_probe/curl_cffi_clean.html")
URL = (
    "https://www.terabyteshop.com.br/produto/22809/"
    "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5"
)


def main() -> None:
    html = HTML.read_text(encoding="utf-8", errors="replace")
    response = HtmlResponse(
        URL, body=html.encode("utf-8"), encoding="utf-8", request=Request(URL)
    )
    spider = TerabyteShopSpider()
    ld = spider.json_ld(response)
    offers = ld.get("offers") if isinstance(ld, dict) else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    table = {
        "json_ld_offers_price": offers.get("price")
        if isinstance(offers, dict)
        else None,
        "dom_valVista": (response.css("#valVista::text").get() or "").strip(),
        "dom_valParc": (response.css("#valParc::text").get() or "").strip(),
        "dom_nParc": (response.css("#nParc::text").get() or "").strip(),
        "dom_Parc": (response.css("#Parc::text").get() or "").strip(),
        "dom_precode_del": (response.css("p.precode del::text").get() or "").strip(),
        "js_valVista_assign": re.findall(
            r"\$\('\.val-prod'\)\.text\('([^']+)'\)", html
        )[:3],
        "js_valParc_assign": re.findall(r"\$\('\.valParc'\)\.text\('([^']+)'\)", html)[
            :3
        ],
    }
    offer = spider.extract_offer(response)
    table["CURRENT_parser"] = {
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
    }
    print(json.dumps(table, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
