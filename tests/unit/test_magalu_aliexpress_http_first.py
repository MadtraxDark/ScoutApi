"""HTTP-first gates for Magalu and AliExpress search."""

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.services.aliexpress_http_first_fetcher import (
    AliExpressHttpFirstHtmlFetcher,
)
from scout_api.modules.crawler.services.magalu_http_first_fetcher import (
    MagaluHttpFirstHtmlFetcher,
)

AE_SEARCH = "https://pt.aliexpress.com/w/wholesale-cooler.html"
MAGALU_SEARCH = "https://www.magazineluiza.com.br/busca/cooler/"
MAGALU_PDP = "https://www.magazineluiza.com.br/cooler/p/abc123/"


def _response(url: str, body: str) -> HtmlResponse:
    return HtmlResponse(url, body=body.encode(), encoding="utf-8", request=Request(url))


class _Http:
    def __init__(self, response: HtmlResponse | Exception) -> None:
        self._response = response
        self.calls = 0

    def fetch(self, url: str) -> HtmlResponse:
        del url
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _Browser:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url: str) -> HtmlResponse:
        self.calls += 1
        return _response(url, "<html>browser</html>")


def test_aliexpress_search_http_skips_browser() -> None:
    body = (
        '<html><script>{"itemList":{"content":[{"productId":"1",'
        '"title":{"displayTitle":"Cooler"}}]}}</script></html>'
    )
    http = _Http(_response(AE_SEARCH, body))
    browser = _Browser()
    response = AliExpressHttpFirstHtmlFetcher(http=http, browser=browser).fetch(
        AE_SEARCH
    )
    assert http.calls == 1
    assert browser.calls == 0
    assert response.meta["fetch_metrics"]["fetch_strategy"] == "http-direct"


def test_aliexpress_pdp_stays_on_browser() -> None:
    http = _Http(_response(AE_SEARCH, "<html></html>"))
    browser = _Browser()
    AliExpressHttpFirstHtmlFetcher(http=http, browser=browser).fetch(
        "https://pt.aliexpress.com/item/100501.html"
    )
    assert http.calls == 0
    assert browser.calls == 1


def test_aliexpress_block_page_falls_back_to_browser() -> None:
    http = _Http(_response(AE_SEARCH, "<html>RGV587 FAIL_SYS_USER_VALIDATE</html>"))
    browser = _Browser()
    AliExpressHttpFirstHtmlFetcher(http=http, browser=browser).fetch(AE_SEARCH)
    assert http.calls == 1
    assert browser.calls == 1


def test_magalu_sec_cpt_falls_back_to_browser() -> None:
    body = (
        "<html><div id='sec-if-cpt-container' class='behavioral-content'></div></html>"
    )
    http = _Http(_response(MAGALU_SEARCH, body))
    browser = _Browser()
    MagaluHttpFirstHtmlFetcher(http=http, browser=browser).fetch(MAGALU_SEARCH)
    assert browser.calls == 1


def test_magalu_product_cards_skip_browser() -> None:
    body = (
        "<html><body><a data-testid='product-card-link' "
        "href='/cooler/p/abc123/'>Cooler</a></body></html>"
    )
    http = _Http(_response(MAGALU_SEARCH, body))
    browser = _Browser()
    MagaluHttpFirstHtmlFetcher(http=http, browser=browser).fetch(MAGALU_SEARCH)
    assert browser.calls == 0


def test_magalu_http_error_falls_back_to_browser() -> None:
    http = _Http(RequestError("reset", code="UPSTREAM_NETWORK_ERROR", url=MAGALU_PDP))
    browser = _Browser()
    MagaluHttpFirstHtmlFetcher(http=http, browser=browser).fetch(MAGALU_PDP)
    assert browser.calls == 1
