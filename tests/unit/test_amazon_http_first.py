"""Tests for Amazon HTTP-first progressive fetcher."""

from __future__ import annotations

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    AmazonHttpFirstHtmlFetcher,
    has_buybox_price_signal,
    is_amazon_store_url,
    looks_like_amazon_pdp,
    looks_like_clear_oos,
)
from scout_api.modules.crawler.spiders.brazil.amazon import AmazonBrazilSpider
from scout_api.modules.crawler.spiders.usa.amazon import AmazonUSSpider


def _html_response(url: str, body: str) -> HtmlResponse:
    raw = body.encode("utf-8")
    return HtmlResponse(url, body=raw, encoding="utf-8", request=Request(url))


class _RecordingFetcher:
    def __init__(
        self, response: HtmlResponse | None = None, error: Exception | None = None
    ):
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def fetch(self, url: str) -> HtmlResponse:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def test_is_amazon_store_url_br_and_us_only() -> None:
    assert is_amazon_store_url("https://www.amazon.com/dp/B09V9Z1WLN")
    assert is_amazon_store_url("https://amazon.com.br/dp/B0GVTB7BGQ")
    assert not is_amazon_store_url("https://www.amazon.com.mx/dp/B09V9Z1WLN")
    assert not is_amazon_store_url("https://www.kabum.com.br/produto/1")


def test_buybox_signal_and_oos_helpers() -> None:
    priced = _html_response(
        "https://www.amazon.com/dp/B09V9Z1WLN",
        '<span id="productTitle">Phone</span>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">$190.63</span></span></div>',
    )
    assert looks_like_amazon_pdp(priced)
    assert has_buybox_price_signal(priced)

    oos = _html_response(
        "https://www.amazon.com/dp/B09V9Z1WLN",
        '<span id="productTitle">Phone</span>'
        '<div id="availability"><span>Currently unavailable.</span></div>',
    )
    assert looks_like_clear_oos(oos)
    assert not has_buybox_price_signal(oos)


def test_http_first_uses_http_when_buybox_present() -> None:
    url = "https://www.amazon.com/dp/B09V9Z1WLN"
    http_body = (
        '<span id="productTitle">Phone</span>'
        '<input id="ASIN" value="B09V9Z1WLN"/>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">$190.63</span></span></div>'
    )
    http = _RecordingFetcher(_html_response(url, http_body))
    browser = _RecordingFetcher(_html_response(url, "<html>browser</html>"))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert http.calls == [url]
    assert browser.calls == []
    assert response.meta["fetch_metrics"]["fetch_strategy"] == "http-direct"
    assert response.meta["fetch_metrics"]["proxy_used"] is False
    assert response.meta["fetch_metrics"]["browser_used"] is False


def test_http_first_falls_back_to_browser_on_challenge() -> None:
    url = "https://www.amazon.com/dp/B09V9Z1WLN"
    challenge = _html_response(
        url,
        '<form action="/errors/validateCaptcha"><h4>Enter the characters</h4></form>',
    )
    browser_body = (
        '<span id="productTitle">Phone</span>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">$10.00</span></span></div>'
    )
    http = _RecordingFetcher(challenge)
    browser = _RecordingFetcher(_html_response(url, browser_body))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert http.calls == [url]
    assert browser.calls == [url]
    assert "Phone" in response.text


def test_http_first_resolves_incomplete_buybox_with_browser() -> None:
    url = "https://www.amazon.com.br/dp/B0GVTB7BGQ"
    incomplete = _html_response(
        url,
        '<span id="productTitle">Phone</span>'
        '<input id="ASIN" value="B0GVTB7BGQ"/>'
        '<a id="aod-ingress-link">outras ofertas a partir de R$2.554,44</a>',
    )
    http = _RecordingFetcher(incomplete)
    browser = _RecordingFetcher(_html_response(url, "<html>browser-hydrated</html>"))
    response = AmazonHttpFirstHtmlFetcher(
        http=http, browser=browser, empty_buybox_retry_seconds=0
    ).fetch(url)
    assert browser.calls == [url]
    assert http.calls == [url]
    assert "browser-hydrated" in response.text


def test_http_first_exhausted_empty_retry_uses_browser() -> None:
    url = "https://www.amazon.com.br/dp/B0GVTB7BGQ"
    empty = _html_response(url, '<span id="productTitle">Phone</span>')
    http = _RecordingFetcher(empty)
    browser = _RecordingFetcher(_html_response(url, "browser-hydrated"))
    response = AmazonHttpFirstHtmlFetcher(
        http=http, browser=browser, empty_buybox_retry_seconds=0.001
    ).fetch(url)
    assert http.calls == [url, url]
    assert browser.calls == [url]
    assert response is browser.response


def test_http_first_explicit_out_of_stock_does_not_open_browser() -> None:
    url = "https://www.amazon.com.br/dp/B0GVTB7BGQ"
    oos = _html_response(
        url,
        '<span id="productTitle">Phone</span>'
        '<div id="availability"><span>Atualmente indisponível.</span></div>',
    )
    http = _RecordingFetcher(oos)
    browser = _RecordingFetcher()
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert response is oos
    assert browser.calls == []


def test_http_first_retries_once_when_buybox_missing() -> None:
    url = "https://www.amazon.com.br/dp/B0GY5SB1P3"
    empty = _html_response(
        url,
        '<span id="productTitle">Pelicula</span><input id="ASIN" value="B0GY5SB1P3"/>',
    )
    priced = _html_response(
        url,
        '<span id="productTitle">Pelicula</span>'
        '<input id="ASIN" value="B0GY5SB1P3"/>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">R$19,99</span></span></div>',
    )

    class FlipFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, fetch_url: str) -> HtmlResponse:
            self.calls.append(fetch_url)
            return empty if len(self.calls) == 1 else priced

    http = FlipFetcher()
    browser = _RecordingFetcher(_html_response(url, "browser"))
    response = AmazonHttpFirstHtmlFetcher(
        http=http, browser=browser, empty_buybox_retry_seconds=0.01
    ).fetch(url)
    assert browser.calls == []
    assert len(http.calls) == 2
    assert has_buybox_price_signal(response)


def test_http_first_falls_back_on_http_error() -> None:
    url = "https://www.amazon.com.br/dp/B09WNK39JN"
    http = _RecordingFetcher(
        error=RequestError(
            "500",
            code="UPSTREAM_HTTP_ERROR",
            url=url,
            upstream_status=500,
        )
    )
    browser = _RecordingFetcher(_html_response(url, "<html>ok</html>"))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == [url]
    assert response.text == "<html>ok</html>"


def test_non_amazon_skips_http() -> None:
    url = "https://www.kabum.com.br/produto/1"
    http = _RecordingFetcher(_html_response(url, "http"))
    browser = _RecordingFetcher(_html_response(url, "browser"))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert http.calls == []
    assert browser.calls == [url]
    assert response.text == "browser"


def test_prepare_fetch_url_canonicalizes_asin() -> None:
    messy = (
        "https://www.amazon.com.br/Echo-Pop-Carvao/dp/B09WNK39JN"
        "?pd_rd_w=abc&keywords=echo&ref=xyz"
    )
    assert (
        AmazonBrazilSpider().prepare_fetch_url(messy)
        == "https://www.amazon.com.br/dp/B09WNK39JN"
    )
    assert (
        AmazonUSSpider().prepare_fetch_url(
            "https://www.amazon.com/gp/product/B09V9Z1WLN?psc=1"
        )
        == "https://www.amazon.com/dp/B09V9Z1WLN"
    )


def test_http_oos_accepted_without_browser() -> None:
    url = "https://www.amazon.com/dp/B000OOS000"
    oos = _html_response(
        url,
        '<span id="productTitle">Phone</span>'
        '<div id="availability"><span>Currently unavailable.</span></div>',
    )
    http = _RecordingFetcher(oos)
    browser = _RecordingFetcher(_html_response(url, "browser"))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == []
    assert response.meta["fetch_metrics"]["fetch_strategy"] == "http-direct"


def test_http_search_empty_shell_falls_back_to_browser() -> None:
    """Bare /s without data-asin cards must not short-circuit as HTTP success."""
    from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
        looks_like_amazon_search,
    )

    url = "https://www.amazon.com.br/s?k=rtx+5060"
    empty_shell = _html_response(
        url, "<html><body><div id='search'>shell</div></body></html>"
    )
    assert not looks_like_amazon_search(empty_shell)
    browser_serp = _html_response(
        url,
        '<div data-component-type="s-search-result" data-asin="B0TESTASIN">'
        "<h2><a href='/dp/B0TESTASIN'><span>GPU</span></a></h2></div>",
    )
    http = _RecordingFetcher(empty_shell)
    browser = _RecordingFetcher(browser_serp)
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == [url]
    assert response is browser_serp


def test_http_search_accepted_without_browser() -> None:
    from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
        looks_like_amazon_search,
    )

    url = "https://www.amazon.com.br/s?k=rtx+5060"
    serp = _html_response(
        url,
        '<div data-component-type="s-search-result" data-asin="B0TESTASIN">'
        "<h2><a href='/dp/B0TESTASIN'><span>GPU</span></a></h2></div>",
    )
    assert looks_like_amazon_search(serp)
    http = _RecordingFetcher(serp)
    browser = _RecordingFetcher(_html_response(url, "browser"))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == []
    assert response.meta["fetch_metrics"]["browser_used"] is False


def test_pdp_with_related_asins_is_not_treated_as_search() -> None:
    """PDP related-product data-asin must not short-circuit Buy Box checks."""
    from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
        looks_like_amazon_search,
    )

    url = "https://www.amazon.com.br/dp/B0HJBCQ9B7"
    pdp_no_buybox = _html_response(
        url,
        '<span id="productTitle">Apple iPhone 18 Pro Max</span>'
        '<input id="ASIN" value="B0HJBCQ9B7"/>'
        '<div data-asin="B0RELATED01"><a href="/dp/B0RELATED01">related</a></div>',
    )
    assert looks_like_amazon_pdp(pdp_no_buybox)
    assert not looks_like_amazon_search(pdp_no_buybox)
    assert not has_buybox_price_signal(pdp_no_buybox)

    browser_body = (
        '<span id="productTitle">Apple iPhone 18 Pro Max</span>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">R$21.999,00</span></span></div>'
    )
    http = _RecordingFetcher(pdp_no_buybox)
    browser = _RecordingFetcher(_html_response(url, browser_body))
    response = AmazonHttpFirstHtmlFetcher(
        http=http, browser=browser, empty_buybox_retry_seconds=0
    ).fetch(url)
    assert browser.calls == [url]
    assert has_buybox_price_signal(response)


_CONTINUE_HTML = (
    __import__("pathlib")
    .Path(__file__)
    .parents[1]
    .joinpath("fixtures", "amazon", "continue_shopping_interstitial.html")
    .read_text(encoding="utf-8")
)


def test_continue_interstitial_url_builds_browser_like_get() -> None:
    from scout_api.modules.crawler.services.amazon_continue import (
        continue_interstitial_url,
    )

    target = continue_interstitial_url(
        _CONTINUE_HTML, base_url="https://www.amazon.com/dp/B09V9Z1WLN"
    )
    assert target is not None
    assert target.startswith("https://www.amazon.com/errors_page/validateCaptcha?")
    assert "amzn=J%2Fi2FMQ0P833mDdGKox18w%3D%3D" in target
    assert "field-keywords=YJTKEN" in target
    # Real image CAPTCHAs are not "continue" pages.
    captcha = (
        '<form action="/errors/validateCaptcha"><img src="https://x/captcha.jpg">'
        '<input type="hidden" name="amzn" value="a">'
        '<input id="captchacharacters" name="field-keywords" type="text"></form>'
    )
    assert (
        continue_interstitial_url(captcha, base_url="https://www.amazon.com/") is None
    )
    assert continue_interstitial_url("<html>ok</html>", base_url="https://x/") is None


def test_http_first_submits_continue_interstitial_then_uses_pdp() -> None:
    url = "https://www.amazon.com/dp/B09V9Z1WLN"
    pdp = _html_response(
        url,
        '<span id="productTitle">Phone</span><input id="ASIN" value="B09V9Z1WLN"/>'
        '<div id="ppd"><span class="priceToPay">'
        '<span class="a-offscreen">$190.63</span></span></div>',
    )

    class _Http:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, u: str) -> HtmlResponse:
            self.calls.append(u)
            if len(self.calls) == 1:
                return _html_response(u, _CONTINUE_HTML)
            if "validateCaptcha" in u:
                return _html_response(u, "<html></html>")
            return pdp

    http = _Http()
    browser = _RecordingFetcher(error=AssertionError("browser must not run"))
    result = AmazonHttpFirstHtmlFetcher(
        http=http, browser=browser, empty_buybox_retry_seconds=0
    ).fetch(url)

    assert result is pdp
    assert len(http.calls) == 3 and "validateCaptcha" in http.calls[1]
    assert browser.calls == []


def test_http_404_is_not_escalated_to_browser() -> None:
    url = "https://www.amazon.com/dp/B0DGHMQ2F4"
    err = RequestError(
        "HTTP 404", code="UPSTREAM_HTTP_ERROR", upstream_status=404, url=url
    )
    browser = _RecordingFetcher(error=AssertionError("browser must not run"))
    fetcher = AmazonHttpFirstHtmlFetcher(
        http=_RecordingFetcher(error=err), browser=browser
    )
    try:
        fetcher.fetch(url)
    except RequestError as exc:
        assert exc.upstream_status == 404
    else:
        raise AssertionError("expected RequestError")
    assert browser.calls == []


def test_http_404_after_continue_form_is_not_escalated_to_browser() -> None:
    url = "https://www.amazon.com/dp/B0DGHMQ2F4"

    class _Http:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, u: str) -> HtmlResponse:
            self.calls.append(u)
            if len(self.calls) == 1:
                return _html_response(u, _CONTINUE_HTML)
            if "validateCaptcha" in u:
                return _html_response(u, "<html></html>")
            raise RequestError(
                "HTTP 404", code="UPSTREAM_HTTP_ERROR", upstream_status=404, url=u
            )

    browser = _RecordingFetcher(error=AssertionError("browser must not run"))
    fetcher = AmazonHttpFirstHtmlFetcher(http=_Http(), browser=browser)
    try:
        fetcher.fetch(url)
    except RequestError as exc:
        assert exc.upstream_status == 404
    else:
        raise AssertionError("expected RequestError")
    assert browser.calls == []
