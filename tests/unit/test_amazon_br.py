"""Amazon Brazil marketplace adapter tests."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.spiders.brazil.amazon import AmazonBrazilSpider

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amazon"
URL = "https://www.amazon.com.br/dp/B09WNK39JN"


def response_from_fixture(name: str, url: str = URL) -> HtmlResponse:
    return HtmlResponse(
        url,
        body=(FIXTURES / name).read_bytes(),
        encoding="utf-8",
        request=Request(url),
    )


def test_amazon_br_offer_price_pix_installment_seller_and_currency() -> None:
    offer = AmazonBrazilSpider().extract_offer(
        response_from_fixture("br_available_pix_installment.html")
    )
    assert offer.store == "amazon"
    assert offer.country == "BR"
    assert offer.currency == "BRL"
    assert offer.product_id == offer.sku == "B09WNK39JN"
    assert offer.price == Decimal("349.00")
    assert offer.original_price == Decimal("449.00")
    assert offer.pix_price == Decimal("331.55")
    assert offer.installment_count == 10
    assert offer.installment_price == Decimal("34.90")
    assert offer.seller == "Amazon.com.br"
    assert offer.metadata["fulfilled_by"] == "Amazon.com.br"
    assert offer.available is True
    assert offer.metadata["pricing"]["pix_available"] is True
    assert offer.metadata["pricing"]["coupon_required"] is True
    assert offer.canonical_url == "https://www.amazon.com.br/dp/B09WNK39JN"


def test_amazon_br_marketplace_seller_and_fulfilled_by() -> None:
    offer = AmazonBrazilSpider().extract_offer(
        response_from_fixture(
            "br_marketplace_seller.html",
            "https://www.amazon.com.br/dp/B0BBWH6RKF",
        )
    )
    assert offer.seller == "TechShop BR"
    assert offer.metadata["fulfilled_by"] == "Amazon.com.br"
    assert offer.pix_price == Decimal("408.40")


def test_amazon_br_out_of_stock() -> None:
    offer = AmazonBrazilSpider().extract_offer(
        response_from_fixture(
            "br_out_of_stock.html",
            "https://www.amazon.com.br/dp/B0D1XD1ZV3",
        )
    )
    assert offer.availability == "out_of_stock"
    assert offer.available is False
    assert offer.currency == "BRL"


def test_amazon_br_details_variant_and_images() -> None:
    spider = AmazonBrazilSpider()
    response = response_from_fixture("br_available_pix_installment.html")
    details = spider.extract_details(response)
    assert details.product_id == "B09WNK39JN"
    assert "Echo Pop" in details.title
    assert details.brand == "Amazon"
    assert details.variant == "cor: Carvão"
    assert details.images == []
    images = spider.extract_images(response)
    assert images[0].startswith("https://m.media-amazon.com/images/")


def test_amazon_br_live_captured_buybox_fixtures() -> None:
    """Sanitized captures from live HTTP-first BR PDPs with Buy Box present."""
    spider = AmazonBrazilSpider()
    first = spider.extract_offer(
        response_from_fixture(
            "br_live_buybox_1.html",
            "https://www.amazon.com.br/dp/B0GY5SB1P3",
        )
    )
    assert first.country == "BR"
    assert first.currency == "BRL"
    assert first.product_id == "B0GY5SB1P3"
    assert first.price == Decimal("19.99")
    assert first.seller == "Danet Store"
    assert first.available is True
    assert first.canonical_url == "https://www.amazon.com.br/dp/B0GY5SB1P3"

    second = spider.extract_offer(
        response_from_fixture(
            "br_live_buybox_2.html",
            "https://www.amazon.com.br/dp/B0GN4S2ZSK",
        )
    )
    assert second.product_id == "B0GN4S2ZSK"
    assert second.price == Decimal("29.90")
    assert second.seller == "ALX Hub"
    assert second.available is True


def test_amazon_br_qualified_buybox_avista_pix() -> None:
    """#qualifiedBuybox + 'à vista no Pix' uses Buy Box total as pix_price."""
    offer = AmazonBrazilSpider().extract_offer(
        response_from_fixture(
            "br_qualified_buybox_avista_pix.html",
            "https://www.amazon.com.br/dp/B0FQHFY4RH",
        )
    )
    assert offer.price == Decimal("8999.09")
    assert offer.original_price == Decimal("9359.10")
    assert offer.pix_price == Decimal("8999.09")
    assert offer.metadata["source"]["price"] == "buybox-price"
    assert offer.metadata["source"]["pix_price"] in {"buybox-pix", "buybox-as-pix"}
    assert offer.seller == "Loja Teste BR"


def test_amazon_br_full_scrape_only_calls_gallery_when_requested(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    spider = AmazonBrazilSpider()
    images = MagicMock(wraps=spider.extract_images)
    spider.extract_images = images
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    class Fetcher:
        def fetch(self, _url: str) -> HtmlResponse:
            return response_from_fixture("br_available_pix_installment.html")

    service = ProductScrapeService(
        fetcher=Fetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=0,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=0,
        ),
    )
    service.scrape(URL, include_images=False)
    images.assert_not_called()
    service.scrape(URL, include_images=True)
    images.assert_called_once()


# --- Card cash discount page (B0HJ9YB5QN-like): no Pix, no strike price --------
MOBILE_URL = "https://www.amazon.com.br/gp/aw/d/B0HJ9YB5QN?th=1&ref_=sbx"


def _variants_response() -> HtmlResponse:
    return response_from_fixture("br_card_cash_discount_variants.html", MOBILE_URL)


def test_amazon_br_mobile_url_is_canonicalised_before_fetch() -> None:
    spider = AmazonBrazilSpider()
    assert (
        spider.prepare_fetch_url(MOBILE_URL)
        == "https://www.amazon.com.br/dp/B0HJ9YB5QN"
    )


def test_amazon_br_cash_discount_is_not_pix_and_keeps_payment_conditions() -> None:
    offer = AmazonBrazilSpider().extract_offer(_variants_response())

    assert offer.price == Decimal("10799.09")
    assert offer.pix_price is None  # "10% off à vista no cartão" is not Pix
    assert offer.installment_count == 12
    assert offer.installment_price == Decimal("999.99")
    assert offer.seller == "Amazon.com.br"
    assert offer.metadata["fulfilled_by"] == "Amazon.com.br"
    assert offer.availability == "available"
    pricing = offer.metadata["pricing"]
    assert pricing["cash_discount_percent"] == "10"
    assert pricing["cash_discount_method"] == "credit_card"
    assert pricing["installment_total"] == "11999.00"
    assert pricing["installment_interest_free"] is True
    # The installment total is the reference price only because 10% off of it
    # reproduces the displayed price.
    assert offer.original_price == Decimal("11999.00")
    assert offer.discount_percentage == Decimal("10.00")
    assert (
        offer.metadata["source"]["original_price"]
        == "installment-total-consistent-with-cash-discount"
    )
    assert offer.metadata["parent_asin"] == "B0HJK997VM"


def test_amazon_br_images_are_the_selected_variant_gallery_only() -> None:
    images = AmazonBrazilSpider().extract_images(_variants_response())

    assert len(images) == 11
    assert images[0].endswith("71E-J09sb6L._AC_SL1500_.jpg")
    ids = [u.split("/I/")[1].split(".")[0] for u in images]
    assert len(set(ids)) == len(ids)


def _buybox_page(
    *, price: str, body: str, gallery: str = "", landing: str = ""
) -> HtmlResponse:
    html = (
        '<html><head><title>x</title></head><body><input id="ASIN" value="B0AAAAAAA1">'
        '<span id="productTitle">Produto</span>'
        f'<div id="ppd"><div id="corePrice_feature_div"><span class="a-price">'
        f'<span class="a-offscreen">{price}</span></span></div>'
        f'<div id="buybox">{body}</div></div>{landing}{gallery}</body></html>'
    )
    return HtmlResponse(
        "https://www.amazon.com.br/dp/B0AAAAAAA1",
        body=html.encode(),
        encoding="utf-8",
        request=Request("https://www.amazon.com.br/dp/B0AAAAAAA1"),
    )


def test_amazon_br_incoherent_installment_total_gives_no_original_price() -> None:
    page = _buybox_page(
        price="R$ 9.000,00",
        body=(
            "<span>10% off à vista</span><span>no cartão de crédito</span>"
            "<span>ou em até 12x de R$ 1.000,00 sem juros</span>"
            "<span>(total parcelado R$ 12.000,00)</span>"
        ),
    )
    offer = AmazonBrazilSpider().extract_offer(page)
    assert offer.original_price is None
    assert offer.discount_percentage is None
    assert offer.metadata["pricing"]["installment_total"] == "12000.00"


def test_amazon_br_explicit_pix_discount_is_reported_as_pix() -> None:
    page = _buybox_page(
        price="R$ 900,00",
        body="<span>R$ 850,00 no Pix</span><span>10% off à vista no Pix</span>",
    )
    offer = AmazonBrazilSpider().extract_offer(page)
    assert offer.pix_price == Decimal("850.00")
    assert offer.metadata["pricing"]["cash_discount_method"] == "pix"


def test_amazon_br_gallery_for_another_asin_is_discarded() -> None:
    other = (
        "<script>var x = {'colorImages': { 'initial': A.$.parseJSON('"
        '[{"hiRes":"https://m.media-amazon.com/images/I/OTHERMAIN1._AC_SL1500_.jpg",'
        '"variant":"MAIN"},'
        '{"hiRes":"https://m.media-amazon.com/images/I/OTHERPT001._AC_SL1000_.jpg",'
        '"variant":"PT01"}]'
        "') }};</script>"
    )
    landing = (
        '<img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/'
        'MINEMAIN01._AC_SL1500_.jpg">'
    )
    images = AmazonBrazilSpider().extract_images(
        _buybox_page(price="R$ 10,00", body="", gallery=other, landing=landing)
    )
    assert images == ["https://m.media-amazon.com/images/I/MINEMAIN01._AC_SL1500_.jpg"]


def test_amazon_br_gallery_dedups_same_image_id_and_caps_size() -> None:
    items = ",".join(
        f'{{"hiRes":"https://m.media-amazon.com/images/I/IMG{i:07d}._AC_SL1000_.jpg"}}'
        for i in range(30)
    )
    dup = '{"hiRes":"https://m.media-amazon.com/images/I/IMG0000000._AC_SX342_.jpg"}'
    script = (
        "<script>var x = {'colorImages': { 'initial': A.$.parseJSON('"
        f"[{items},{dup}]') }};</script>"
    )
    images = AmazonBrazilSpider().extract_images(
        _buybox_page(price="R$ 10,00", body="", gallery=script)
    )
    assert len(images) == 15
    assert images[0].endswith("IMG0000000._AC_SL1000_.jpg")
