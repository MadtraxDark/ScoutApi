"""Unit tests for the AliExpress store adapter."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.services.html_fetcher import (
    is_aliexpress_block_page,
    is_aliexpress_pdp_api_url,
    is_aliexpress_url,
    looks_like_aliexpress_pdp,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.spiders.aliexpress import AliExpressSpider
from scout_api.modules.crawler.spiders.registry import resolve_store_spider
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.search_adapters.aliexpress import (
    AliExpressSearchAdapter,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "aliexpress"

REF_URL = (
    "https://pt.aliexpress.com/item/1005011737968391.html"
    "?spm=a2g0o.productlist.main.4.71503df5Lne8tU"
    "&algo_pvid=11381abf-662c-478c-a65a-932710472a37"
    "&pdp_npi=6%40dis%21BRL%218999.00%218819.02%21%21%218999.00%218819.02"
    "%21%402101e80b17899043839964776e103a%2112000059875009119%21sea%21BR"
    "%213185799387%21X%211%210%21n_tag%3A-29919"
    "&curPageLogUid=8FKs0NORhJ9n"
)
REF_SKU_URL = (
    "https://pt.aliexpress.com/item/1005011737968391.html?sku_id=12000059875009999"
)
MULTI_URL = "https://pt.aliexpress.com/item/1005011737968391.html"
OOS_URL = "https://pt.aliexpress.com/item/1005011737968391.html"
SEARCH_URL = "https://pt.aliexpress.com/w/wholesale-msi-rtx-5070-ti.html"


def _html(name: str, url: str) -> HtmlResponse:
    body = (FIXTURES / name).read_bytes()
    return HtmlResponse(url, body=body, encoding="utf-8", request=Request(url))


def test_store_registered_and_implemented() -> None:
    config = STORE_CONFIGS["aliexpress"]
    assert config.implemented is True
    assert config.proxy_policy.value == "fallback"


def test_resolve_aliexpress_spider_locales() -> None:
    for url in (
        "https://pt.aliexpress.com/item/1005011737968391.html",
        "https://www.aliexpress.com/item/1005011737968391.html",
        "https://aliexpress.com/item/1005011737968391.html",
        "https://es.aliexpress.com/item/1005011737968391.html",
    ):
        spider = resolve_store_spider(url)
        assert isinstance(spider, AliExpressSpider)


def test_canonical_url_strips_tracking_keeps_sku() -> None:
    spider = AliExpressSpider()
    offer = spider.extract_offer(_html("product_msi_rtx5070ti.html", REF_URL))
    assert offer.canonical_url == (
        "https://pt.aliexpress.com/item/1005011737968391.html?sku_id=12000059875009119"
    )
    assert "spm" not in offer.canonical_url
    assert "pdp_npi" not in offer.canonical_url
    assert canonicalize_url(REF_URL).endswith("/item/1005011737968391.html")


def test_ids_from_url_and_payload() -> None:
    spider = AliExpressSpider()
    ids = spider._url_identity(REF_URL)
    assert ids["item_id"] == "1005011737968391"
    assert ids["sku_id"] == "12000059875009119"

    offer = spider.extract_offer(_html("product_msi_rtx5070ti.html", REF_URL))
    assert offer.product_id == "1005011737968391"
    assert offer.sku == "12000059875009119"
    assert offer.metadata["item_id"] == "1005011737968391"
    assert offer.metadata["sku_id"] == "12000059875009119"


def test_offer_price_seller_currency_not_from_query() -> None:
    offer = AliExpressSpider().extract_offer(
        _html("product_msi_rtx5070ti.html", REF_URL)
    )
    assert offer.currency == "BRL"
    assert offer.price == Decimal("8819.02")
    assert offer.original_price == Decimal("8999.00")
    assert offer.discount_percentage == Decimal("2")
    assert offer.seller == "MA INFOSTORE Store"
    assert offer.seller != "AliExpress"
    assert offer.available is True
    assert offer.availability == "available"
    assert offer.installment_count == 12
    assert offer.installment_price == Decimal("808.41")
    assert offer.pix_price is None
    assert offer.metadata["source"]["price"] == "sale-price-local"
    assert offer.metadata["seller_origin"] == "Brasil"
    assert "m03_new_user" in offer.metadata.get("promotions", {})


def test_selected_sku_from_query_not_cheapest() -> None:
    offer = AliExpressSpider().extract_offer(
        _html("product_multi_sku.html", REF_SKU_URL)
    )
    assert offer.sku == "12000059875009999"
    assert offer.price == Decimal("8740.00")
    assert offer.price != Decimal("8819.02")
    details = AliExpressSpider().extract_details(
        _html("product_multi_sku.html", REF_SKU_URL)
    )
    assert details.variant == "Cor: Branco"


def test_out_of_stock_sku() -> None:
    offer = AliExpressSpider().extract_offer(
        _html("product_out_of_stock.html", OOS_URL)
    )
    assert offer.available is False
    assert offer.availability == "out_of_stock"


def test_details_identity_normalization() -> None:
    details = AliExpressSpider().extract_details(
        _html("product_msi_rtx5070ti.html", REF_URL)
    )
    assert "RTX 5070 Ti" in details.title or "5070" in details.title
    assert details.brand == "MSI"
    assert details.model is not None
    assert "GeForce RTX 5070 Ti" in (details.model or "")
    assert details.specifications.get("Capacidade de memória de vídeo") == "16 GBs"
    assert details.specifications.get("Tipo de memória de vídeo") == "GDDR7"
    assert details.images == []


def test_images_selected_sku_and_gallery() -> None:
    spider = AliExpressSpider()
    response = _html("product_multi_sku.html", REF_SKU_URL)
    images = spider.extract_images(response)
    assert images
    assert images[0].endswith("white.png")
    assert all("avatar" not in url and "banner" not in url for url in images)


def test_include_images_false_skips_gallery_in_service() -> None:
    response = _html("product_msi_rtx5070ti.html", REF_URL)
    fetcher = MagicMock()
    fetcher.fetch.return_value = response
    service = ProductScrapeService(fetcher=fetcher, guard=MagicMock())
    service._guard.get_cached.return_value = None
    service._guard.run_coalesced.side_effect = lambda _url, fn, **_kw: fn()

    item = service.scrape(REF_URL, include_images=False)
    assert item.images == []
    assert item.price == Decimal("8819.02")
    assert item.brand == "MSI"


def test_include_images_true_returns_gallery() -> None:
    response = _html("product_msi_rtx5070ti.html", REF_URL)
    fetcher = MagicMock()
    fetcher.fetch.return_value = response
    response.meta["fetch_metrics"] = {"proxy_used": False}
    service = ProductScrapeService(fetcher=fetcher, guard=MagicMock())
    service._guard.get_cached.return_value = None
    service._guard.run_coalesced.side_effect = lambda _url, fn, **_kw: fn()

    item = service.scrape(REF_URL, include_images=True)
    assert len(item.images) >= 3
    assert all(
        "ae-pic" in url or "alicdn" in url or "aliexpress-media" in url
        for url in item.images
    )


def test_blocked_mtop_raises_upstream_blocked_not_unavailable() -> None:
    with pytest.raises(RequestError) as exc:
        AliExpressSpider().extract_offer(_html("blocked_rgv587.html", REF_URL))
    assert exc.value.code == "UPSTREAM_BLOCKED"


def test_fetcher_helpers_detect_aliexpress() -> None:
    assert is_aliexpress_url("https://pt.aliexpress.com/item/1.html")
    assert is_aliexpress_pdp_api_url(
        "https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?x=1"
    )
    assert looks_like_aliexpress_pdp(
        json.dumps(
            {
                "ret": ["SUCCESS::OK"],
                "data": {"result": {"PRODUCT_TITLE": {"text": "x"}}},
            }
        )
    )
    assert is_aliexpress_block_page(
        "<html><title></title><body>RGV587 FAIL_SYS_USER_VALIDATE</body></html>"
    )
    assert not is_aliexpress_block_page(
        '<html><script data-aliexpress-pdp="1">'
        '{"data":{"result":{"PRODUCT_TITLE":{"text":"x"}}}}'
        "</script></html>"
    )


def test_search_candidates() -> None:
    candidates = AliExpressSearchAdapter().parse_candidates(
        _html("search_msi.html", SEARCH_URL)
    )
    assert candidates
    assert candidates[0].product_id == "1005011737968391"
    assert "1005011737968391" in candidates[0].url
    assert candidates[0].title and "5070" in candidates[0].title


def test_search_candidates_from_embedded_item_list() -> None:
    html = """
    <html><body><script>
    window._dida_config_ = {"data":{"root":{"fields":{"mods":
    {"itemList":{"content":[
      {"productId":"1005012081177760",
       "title":{"displayTitle":"Ventilador MAG CORELIQUID 240R"}},
      {"productId":"1005009990000001",
       "title":{"displayTitle":"Water Cooler MSI MAG CoreLiquid A12 360"}}
    ]}}}}}}
    </script>
    <a href="/item/999.html">sem titulo</a>
    </body></html>
    """
    from scrapy.http import HtmlResponse

    response = HtmlResponse(url=SEARCH_URL, body=html.encode(), encoding="utf-8")
    candidates = AliExpressSearchAdapter().parse_candidates(response)
    assert [c.product_id for c in candidates] == [
        "1005012081177760",
        "1005009990000001",
    ]
    assert candidates[1].title and "A12" in candidates[1].title
    assert candidates[0].metadata.get("source") == "aliexpress-search-api"


def test_prepare_fetch_url_preserves_sku_hint() -> None:
    prepared = AliExpressSpider().prepare_fetch_url(REF_URL)
    assert "spm" not in prepared
    assert "sku_id=12000059875009119" in prepared


def test_shipping_free_on_full_parse() -> None:
    item = AliExpressSpider().parse_product(
        _html("product_msi_rtx5070ti.html", REF_URL)
    )
    assert item.shipping_price == Decimal("0.00")
    assert item.metadata.get("shipping", {}).get("ship_from_code") == "BR"
