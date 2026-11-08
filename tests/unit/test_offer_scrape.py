from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.crawler.services.offer_scrape_service import OfferScrapeService
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.spiders.brazil.magazineluiza import MagazineLuizaSpider

FIXTURES = Path(__file__).parents[1] / "fixtures" / "magazineluiza"


def _response_from_fixture(name: str) -> HtmlResponse:
    url = (
        "https://www.magazineluiza.com.br/p/240590700/ga/gap5/?seller_id=magazineluiza"
    )
    return HtmlResponse(
        url,
        body=(FIXTURES / name).read_bytes(),
        encoding="utf-8",
        request=Request(url),
    )


def test_magalu_extract_offer_returns_commercial_fields() -> None:
    offer = MagazineLuizaSpider().extract_offer(
        _response_from_fixture("product_structured_offer.html")
    )
    assert isinstance(offer, ProductOffer)
    assert offer.product_id == "aebh5a7a94"
    assert offer.sku == "989702"
    assert offer.seller == "kabum"
    assert offer.price == Decimal("5058.85")
    assert offer.pix_price == Decimal("4805.91")
    assert offer.original_price == Decimal("5288.85")
    assert offer.discount_percentage == Decimal("5.00")
    assert offer.installment_count == 10
    assert offer.installment_price == Decimal("505.89")
    assert offer.availability == "available"
    assert offer.available is True
    dumped = offer.model_dump()
    assert "title" not in dumped
    assert "brand" not in dumped
    assert "model" not in dumped
    assert "gtin" not in dumped
    assert "description" not in dumped
    assert "images" not in dumped


def test_magalu_extract_offer_does_not_require_details_fields() -> None:
    offer = MagazineLuizaSpider().extract_offer(
        _response_from_fixture("product_available.html")
    )
    assert offer.price == Decimal("4399.00")
    assert offer.pix_price == Decimal("4399.00")
    assert offer.seller == "magazineluiza"
    assert offer.available is True


def test_offer_scrape_service_populates_product_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Offer refresh must fill ProductPriceItem cache so Match reuses the URL."""
    url = "https://www.magazineluiza.com.br/p/240590700"
    html = _response_from_fixture("product_structured_offer.html")
    fetches: list[str] = []

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            return html

    spider = MagazineLuizaSpider()
    details_mock = MagicMock(wraps=spider.extract_details)
    monkeypatch.setattr(spider, "extract_details", details_mock)
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.offer_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    fetcher = FakeFetcher()
    offer = OfferScrapeService(fetcher=fetcher, guard=guard).scrape_offer(url)
    assert offer.price == Decimal("5058.85")
    details_mock.assert_called_once()

    item = ProductScrapeService(fetcher=fetcher, guard=guard).scrape(url)
    assert item.price == Decimal("5058.85")
    assert item.title
    assert item.metadata.get("cache_hit") is True
    assert len(fetches) == 1


def test_product_scrape_service_composes_offer_and_details(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = "https://www.magazineluiza.com.br/p/aebh5a7a94"
    html = _response_from_fixture("product_structured_offer.html")

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return html

    spider = MagazineLuizaSpider()
    offer_mock = MagicMock(wraps=spider.extract_offer)
    details_mock = MagicMock(wraps=spider.extract_details)
    images_mock = MagicMock(wraps=spider.extract_images)
    monkeypatch.setattr(spider, "extract_offer", offer_mock)
    monkeypatch.setattr(spider, "extract_details", details_mock)
    monkeypatch.setattr(spider, "extract_images", images_mock)
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    item = ProductScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape(url)

    offer_mock.assert_called_once()
    details_mock.assert_called_once()
    images_mock.assert_not_called()
    assert item.title  # from details
    assert item.brand == "Sony"
    assert item.price == Decimal("5058.85")
    assert item.seller == "kabum"
    assert item.images == []


def test_offer_scrape_service_stores_offer_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = "https://www.magazineluiza.com.br/p/240590700"
    html = _response_from_fixture("product_structured_offer.html")
    fetches: list[str] = []

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            return html

    spider = MagazineLuizaSpider()
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.offer_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )
    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    service = OfferScrapeService(fetcher=FakeFetcher(), guard=guard)
    first = service.scrape_offer(url)
    second = service.scrape_offer(url)
    assert first.price == Decimal("5058.85")
    assert second.metadata.get("cache_hit") is True
    assert len(fetches) == 1


def test_offer_scrape_service_never_calls_extract_images(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = "https://www.magazineluiza.com.br/p/240590700"
    html = _response_from_fixture("product_structured_offer.html")

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return html

    spider = MagazineLuizaSpider()
    images_mock = MagicMock(wraps=spider.extract_images)
    monkeypatch.setattr(spider, "extract_images", images_mock)
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.offer_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    OfferScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape_offer(url)

    images_mock.assert_not_called()


def test_product_scrape_include_images_false_skips_extract_images(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    url = "https://www.magazineluiza.com.br/p/aebh5a7a94"
    html = _response_from_fixture("product_structured_offer.html")

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return html

    spider = MagazineLuizaSpider()
    images_mock = MagicMock(wraps=spider.extract_images)
    monkeypatch.setattr(spider, "extract_images", images_mock)
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    item = ProductScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape(url, include_images=False)

    images_mock.assert_not_called()
    assert item.images == []


def test_product_scrape_include_images_true_extracts_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = "https://www.magazineluiza.com.br/p/aebh5a7a94"
    html = _response_from_fixture("product_structured_offer.html")

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            return html

    spider = MagazineLuizaSpider()
    offer_mock = MagicMock(wraps=spider.extract_offer)
    details_mock = MagicMock(wraps=spider.extract_details)
    images_mock = MagicMock(wraps=spider.extract_images)
    monkeypatch.setattr(spider, "extract_offer", offer_mock)
    monkeypatch.setattr(spider, "extract_details", details_mock)
    monkeypatch.setattr(spider, "extract_images", images_mock)
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    item = ProductScrapeService(
        fetcher=FakeFetcher(),
        guard=ScrapeGuard(
            url_cooldown_seconds=1,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=1,
        ),
    ).scrape(url, include_images=True)

    offer_mock.assert_called_once()
    details_mock.assert_called_once()
    images_mock.assert_called_once()
    assert item.images == [
        "https://a-static.mlcdn.com.br/ps5-front.jpg",
        "https://a-static.mlcdn.com.br/ps5-side.jpg",
        "https://www.magazineluiza.com.br/relative/ps5-back.jpg",
    ]


def test_magalu_seller_match_flags_requested_vs_fallback_offer() -> None:
    spider = MagazineLuizaSpider()
    body = (FIXTURES / "product_structured_offer.html").read_bytes()

    def _offer(seller_id: str) -> ProductOffer:
        url = f"https://www.magazineluiza.com.br/p/240590700/ga/gap5/?seller_id={seller_id}"
        return spider.extract_offer(
            HtmlResponse(url, body=body, encoding="utf-8", request=Request(url))
        )

    assert _offer("kabum").metadata["source"]["seller_match"] == "requested"
    flagged = _offer("loja-inexistente")
    assert flagged.metadata["source"]["seller_match"] == "fallback"
    assert flagged.seller == "kabum"  # real seller of the offer shown, never invented
