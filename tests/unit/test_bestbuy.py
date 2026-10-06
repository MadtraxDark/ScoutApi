from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.services.store_resolver import resolve_store_spider
from scout_api.modules.crawler.spiders.usa.bestbuy import BestBuySpider
from scout_api.modules.matching.search_adapters.usa.bestbuy import BestBuySearchAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bestbuy"
URL = "https://www.bestbuy.com/product/apple-iphone-17-512gb-lavender-at-t/ABC123XYZ9"


def response_from_fixture(name: str) -> HtmlResponse:
    return HtmlResponse(
        URL, body=(FIXTURES / name).read_bytes(), encoding="utf-8", request=Request(URL)
    )


def test_bestbuy_domain_resolves_to_spider() -> None:
    spider = resolve_store_spider(URL)
    assert isinstance(spider, BestBuySpider)


def test_bestbuy_prepare_fetch_url_adds_intl_nosplash() -> None:
    spider = BestBuySpider()
    prepared = spider.prepare_fetch_url(URL)
    assert "intl=nosplash" in prepared
    assert spider.prepare_fetch_url(prepared) == prepared


def test_bestbuy_prepare_fetch_url_legacy_site_sku() -> None:
    spider = BestBuySpider()
    legacy = "https://www.bestbuy.com/site/logitech-mouse/6556754.p?skuId=6556754"
    prepared = spider.prepare_fetch_url(legacy)
    assert "intl=nosplash" in prepared
    assert "skuId=6556754" in prepared
    bare = "https://www.bestbuy.com/site/6556754.p"
    prepared_bare = spider.prepare_fetch_url(bare)
    assert "skuId=6556754" in prepared_bare
    assert "intl=nosplash" in prepared_bare


def test_bestbuy_canonical_offer_url_strips_intl() -> None:
    dirty = (
        "https://www.bestbuy.com/product/logitech-mouse/J7H7ZY8YS5"
        "?intl=nosplash&utm_source=x"
    )
    canon = BestBuySpider._canonical_offer_url(dirty)
    assert "intl=" not in canon
    assert "utm_source" not in canon
    assert canon.endswith("/J7H7ZY8YS5") or "/J7H7ZY8YS5" in canon


def test_bestbuy_product_id_strips_legacy_dot_p() -> None:
    assert (
        BestBuySpider._product_id({}, "https://www.bestbuy.com/site/x/6556754.p")
        == "6556754"
    )
    assert (
        BestBuySpider._requested_sku_id(
            "https://www.bestbuy.com/site/x/6556754.p?skuId=6556754"
        )
        == "6556754"
    )


def test_bestbuy_sku_mismatch_after_redirect_fails_closed() -> None:
    html = response_from_fixture("product_available.html")
    # Fixture SKU is 6418059; request claims a different legacy skuId.
    mismatched = HtmlResponse(
        html.url,
        body=html.body,
        encoding="utf-8",
        request=Request("https://www.bestbuy.com/site/other/9999999.p?skuId=9999999"),
    )
    try:
        BestBuySpider().extract_offer(mismatched)
        raise AssertionError("expected ParseError on SKU mismatch")
    except Exception as exc:  # noqa: BLE001 — assert type below
        from scout_api.modules.crawler.core.exceptions import ParseError

        assert isinstance(exc, ParseError)
        assert "SKU diferente" in str(exc)


def test_bestbuy_offer_uses_selected_offer_and_normalizes_fields() -> None:
    offer = BestBuySpider().extract_offer(
        response_from_fixture("product_available.html")
    )
    assert offer.product_id == "ABC123XYZ9"
    assert offer.sku == "6418059"
    assert offer.seller == "Best Buy"
    assert offer.price == Decimal("1129.99")
    assert offer.original_price == Decimal("1199.99")
    assert offer.discount_percentage == Decimal("5.83")
    # Carrier installment plans must not be treated as device financing.
    assert offer.installment_price is None
    assert offer.installment_count is None
    assert offer.metadata["carrier"] == "AT&T"
    assert offer.metadata["carrier_locked"] is True
    assert offer.available is True
    assert offer.availability == "available"


def test_bestbuy_details_identity_variants_and_images() -> None:
    spider = BestBuySpider()
    response = response_from_fixture("product_available.html")
    details = spider.extract_details(response)
    assert details.product_id == "ABC123XYZ9"
    assert details.sku == "6418059"
    assert details.gtin == "195950690200"
    assert details.brand == "Apple"
    assert details.model == "iPhone 17"
    assert details.metadata.get("structured_model") == "MG4J4LL/A"
    assert details.variant == "color: Lavender; storage: 512GB"
    assert "carrier" not in (details.variant or "").casefold()
    assert "at&t" not in (details.variant or "").casefold()
    assert details.metadata["carrier"] == "AT&T"
    assert details.metadata["carrier_locked"] is True
    assert details.metadata["variant"] == {
        "color": "Lavender",
        "storage": "512GB",
    }
    assert details.description == "iPhone 17 with a bright display."
    assert "self.__next_f" not in (details.description or "")
    assert len(details.variant or "") < 120
    assert spider.extract_images(response) == [
        "https://www.bestbuy.com/images/iphone-front.jpg",
        "https://www.bestbuy.com/images/iphone-side.jpg",
        "https://www.bestbuy.com/images/iphone-back.jpg",
    ]


def test_bestbuy_json_ld_offers_list_extracts_price() -> None:
    offer = BestBuySpider().extract_offer(
        response_from_fixture("product_jsonld_offers_list.html")
    )
    assert offer.price == Decimal("298.00")
    assert offer.sku == "6505727"
    assert offer.seller == "Best Buy"
    assert offer.available is True
    assert offer.availability == "available"


def test_bestbuy_out_of_stock_is_not_available() -> None:
    item = BestBuySpider().parse_product(
        response_from_fixture("product_out_of_stock.html")
    )
    assert item.available is False
    assert item.availability == "out_of_stock"
    assert item.price == Decimal("799.99")


def test_bestbuy_shipping_unavailable_still_available_with_price() -> None:
    """Intl/ZIP shipping gaps must not mark a priced US offer as unavailable."""
    offer = BestBuySpider().extract_offer(
        response_from_fixture("product_shipping_unavailable.html")
    )
    assert offer.price == Decimal("899.99")
    assert offer.original_price == Decimal("999.99")
    assert offer.currency == "USD"
    assert offer.available is True
    assert offer.availability == "available"
    assert offer.metadata["source"]["availability"] == "active-offer-price"
    assert offer.metadata["source"]["location_dependent"] is True


def test_bestbuy_pickup_only_offer_is_available() -> None:
    offer = BestBuySpider().extract_offer(
        response_from_fixture("product_pickup_only.html")
    )
    assert offer.price == Decimal("99.99")
    assert offer.available is True
    assert offer.availability == "available"
    assert offer.metadata["source"]["availability"] == "product-offer-state"


def test_bestbuy_sold_out_overrides_schema_instock() -> None:
    spider = BestBuySpider()
    response = response_from_fixture("product_sold_out_instock_schema.html")
    item = spider.parse_product(response)
    details = spider.extract_details(response)
    assert item.available is False
    assert item.availability == "out_of_stock"
    assert item.price == Decimal("1129.99")
    assert item.variant == "color: Lavender; storage: 512GB"
    assert item.metadata["carrier"] == "AT&T"
    assert item.metadata["carrier_locked"] is True
    assert details.description == (
        "Shop Apple iPhone 17 512GB Lavender (AT&T) products at Best Buy."
    )
    assert "Sponsored" not in (item.variant or "")
    assert "Built-in Storage" not in (item.variant or "")


def test_bestbuy_unlocked_is_not_carrier_locked() -> None:
    spider = BestBuySpider()
    response = response_from_fixture("product_unlocked.html")
    item = spider.parse_product(response)
    assert item.variant == "color: Lavender; storage: 512GB"
    assert "unlocked" not in (item.variant or "").casefold()
    assert item.metadata["carrier"] == "Unlocked"
    assert item.metadata["carrier_locked"] is False
    assert item.installment_price == Decimal("91.67")
    assert item.installment_count == 12
    assert item.price == Decimal("1099.99")


def test_bestbuy_apollo_ssr_customer_price() -> None:
    """Modern PDP embeds price in ApolloSSRDataTransport pushes, not bare JSON."""
    url = (
        "https://www.bestbuy.com/product/apple-iphone-15-128gb-unlocked-blue/JJGCQX68JS"
    )
    response = HtmlResponse(
        url,
        body=(FIXTURES / "product_apollo_ssr.html").read_bytes(),
        encoding="utf-8",
        request=Request(url),
    )
    spider = BestBuySpider()
    offer = spider.extract_offer(response)
    assert offer.currency == "USD"
    assert offer.price == Decimal("629.99")
    assert offer.product_id == "JJGCQX68JS"
    assert offer.sku == "6418031"
    assert offer.available is True
    details = spider.extract_details(response)
    assert "iPhone 15" in (details.title or "")


def test_bestbuy_full_scrape_only_calls_images_when_requested(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    spider = BestBuySpider()
    images = MagicMock(wraps=spider.extract_images)
    monkeypatch.setattr(spider, "extract_images", images)
    monkeypatch.setattr(
        (
            "scout_api.modules.crawler.services.product_sc"
            "rape_service.resolve_store_spider"
        ),
        lambda _url: spider,
    )

    class Fetcher:
        def fetch(self, _url: str) -> HtmlResponse:
            return response_from_fixture("product_available.html")

    service = ProductScrapeService(
        fetcher=Fetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=0,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=0,
        ),
    )
    assert service.scrape(URL, include_images=False).images == []
    images.assert_not_called()
    assert service.scrape(URL, include_images=True).images
    images.assert_called_once()


def test_bestbuy_search_derives_title_from_product_slug() -> None:
    body = (
        b'\n    <html><body>\n      <a href="/product/ap'
        b"ple-iphone-16-128gb-apple-intelligence-black"
        b'-verizon/JCQ6HRGR8C">x</a>\n      <a href="/p'
        b"roduct/apple-iphone-16e-128gb-apple-intellig"
        b'ence-black-at-t/JCQ6HRFZF3">y</a>\n    </body'
        b"></html>\n    "
    )
    response = HtmlResponse(
        "https://www.bestbuy.com/site/searchpage.jsp?st=iphone",
        body=body,
        encoding="utf-8",
        request=Request("https://www.bestbuy.com/site/searchpage.jsp?st=iphone"),
    )
    candidates = BestBuySearchAdapter().parse_candidates(response)
    assert len(candidates) == 2
    assert candidates[0].product_id == "JCQ6HRGR8C"
    assert candidates[0].title is not None
    assert "iphone 16 128gb" in candidates[0].title.casefold()
    assert "16e" in (candidates[1].title or "").casefold()
