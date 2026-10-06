"""Diagnose Visão VIP → Kabum/Magalu search queries and candidate retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scrapy.http import HtmlResponse, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scout_api.modules.crawler.models.product import compose_product_price_item
from scout_api.modules.crawler.spiders.paraguay.visaovip import VisaoVipSpider
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    model_search_phrase,
)

ROOT = Path(__file__).resolve().parents[1]


FIXTURE = ROOT / "tests/fixtures/visaovip/product_available.html"
URL = (
    "https://visaovip.com/prod/placas-de-video-nvidia/"
    "placa-de-video-msi-shadow-3x-oc-12gb-geforce-rtx5070-gddr7-912-v532-232/55359/"
)
KABUM_GT = "777166"
MAGALU_GT = "fkff6cf4a2"


def main() -> None:
    spider = VisaoVipSpider()
    response = HtmlResponse(
        URL, body=FIXTURE.read_bytes(), encoding="utf-8", request=Request(URL)
    )
    offer = spider.extract_offer(response)
    details = spider.extract_details(response)
    item = compose_product_price_item(offer, details)
    print(
        "ITEM",
        json.dumps(
            {
                "title": item.title,
                "brand": item.brand,
                "model": item.model,
                "sku": item.sku,
                "gtin": item.gtin,
                "variant": item.variant,
                "metadata_keys": list(item.metadata.keys()),
                "has_specs_in_meta": bool(
                    isinstance(item.metadata.get("specifications"), dict)
                    and item.metadata.get("specifications")
                ),
                "details_specs_n": len(details.specifications),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    identity = identity_from_price_item(item)
    print(
        "IDENTITY",
        json.dumps(
            {
                "brand": identity.brand,
                "model": identity.model,
                "mpn": identity.mpn,
                "mpn_display": identity.mpn_display,
                "gtin": identity.gtin,
                "variant_attrs": identity.variant_attrs,
                "title_normalized": identity.title_normalized,
                "series_phrase": model_search_phrase(
                    model=identity.model, title=identity.title
                ),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    queries = build_search_queries(identity)
    print("QUERIES", json.dumps(queries, ensure_ascii=False), flush=True)

    # Also show what would happen if specs were available
    item2 = item.model_copy(
        update={"metadata": {**item.metadata, "specifications": details.specifications}}
    )
    id2 = identity_from_price_item(item2)
    q2 = build_search_queries(id2)
    print(
        "WITH_SPECS_IDENTITY",
        json.dumps(
            {
                "model": id2.model,
                "mpn": id2.mpn,
                "mpn_display": id2.mpn_display,
                "variant_attrs": id2.variant_attrs,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    print("WITH_SPECS_QUERIES", json.dumps(q2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
