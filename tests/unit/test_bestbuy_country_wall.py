"""Best Buy's international chooser is not an empty product search."""

from scrapy.http import HtmlResponse

from scout_api.modules.matching.search_adapters.usa.bestbuy import (
    BestBuySearchAdapter,
)


def test_bestbuy_country_chooser_is_incomplete_search() -> None:
    response = HtmlResponse(
        url="https://www.bestbuy.com/site/searchpage.jsp?st=iphone",
        body=(
            b"<html><title>Best Buy International: Select your Country"
            b" - Best Buy</title><body><a href='/site/searchpage.jsp'>"
            b"Shop the United States</a></body></html>"
        ),
        encoding="utf-8",
    )
    adapter = BestBuySearchAdapter()

    assert adapter.parse_candidates(response) == []
    assert adapter.classify_empty_result(response) == "incomplete"


def test_bestbuy_search_skips_international_chooser() -> None:
    request = BestBuySearchAdapter().build_search_request(
        "iPhone 18 Pro 512GB Burgundy"
    )

    assert "st=iPhone+18+Pro+512GB+Burgundy" in request.url
    assert request.url.endswith("&intl=nosplash")


def test_bestbuy_search_deduplicates_modern_product_with_sku_suffix() -> None:
    response = HtmlResponse(
        url="https://www.bestbuy.com/site/searchpage.jsp?st=iphone",
        body=(
            b"<a href='/product/apple-iphone-18-pro/JCQ6HRFT7V/sku/6443252'>"
            b"first</a><a href='/product/apple-iphone-18-pro/JCQ6HRFT7V'>"
            b"second</a>"
        ),
        encoding="utf-8",
    )

    candidates = BestBuySearchAdapter().parse_candidates(response)

    assert len(candidates) == 1
    assert candidates[0].product_id == "JCQ6HRFT7V"
