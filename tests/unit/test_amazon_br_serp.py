"""Amazon BR SERP classification + multi-source ASIN parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    AmazonHttpFirstHtmlFetcher,
    looks_like_amazon_search,
)
from scout_api.modules.crawler.services.html_fetcher import (
    is_amazon_soft_error_page,
)
from scout_api.modules.matching.search_adapters.amazon.parse import (
    classify_amazon_empty_result,
    classify_amazon_serp_response,
    parse_amazon_search_results,
)
from scout_api.modules.matching.search_adapters.brazil.amazon import (
    AmazonBrazilSearchAdapter,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "amazon"


def _load(name: str, *, url: str = "https://www.amazon.com.br/s?k=teste") -> HtmlResponse:
    body = (FIXTURES / name).read_text(encoding="utf-8")
    return HtmlResponse(
        url=url,
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=Request(url),
    )


class _RecordingFetcher:
    def __init__(self, responses: list[HtmlResponse] | HtmlResponse) -> None:
        if isinstance(responses, list):
            self._responses = list(responses)
        else:
            self._responses = [responses]
        self.calls: list[str] = []

    def fetch(self, url: str) -> HtmlResponse:
        self.calls.append(url)
        if not self._responses:
            raise AssertionError("no more responses")
        return self._responses.pop(0)


def test_soft_error_page_detected() -> None:
    html = (FIXTURES / "br_serp_soft_error.html").read_text(encoding="utf-8")
    assert is_amazon_soft_error_page(html, title="Amazon.com.br Algo deu errado")
    assert is_amazon_soft_error_page(html)
    assert not is_amazon_soft_error_page(
        (FIXTURES / "br_serp_valid.html").read_text(encoding="utf-8")
    )


def test_classify_valid_serp() -> None:
    resp = _load("br_serp_valid.html")
    assert classify_amazon_serp_response(resp) == "valid"
    assert looks_like_amazon_search(resp)
    adapter = AmazonBrazilSearchAdapter()
    cands = adapter.parse_candidates(resp)
    assert len(cands) == 2
    assert cands[0].product_id == "B0DSYJCY45"
    assert cands[1].product_id == "B0DSYC4V4V"


def test_classify_multi_product_serp() -> None:
    resp = _load("br_serp_valid.html")
    cands = parse_amazon_search_results(
        resp, host="amazon.com.br", source="amazon-br-search", limit=10
    )
    assert len(cands) >= 2
    asins = {c.product_id for c in cands}
    assert "B0DSYJCY45" in asins
    assert "" not in asins


def test_classify_zero_results_not_incomplete() -> None:
    resp = _load("br_serp_zero_results.html")
    assert classify_amazon_serp_response(resp) == "genuine_empty"
    assert classify_amazon_empty_result(resp) == "genuine_empty"
    assert AmazonBrazilSearchAdapter().parse_candidates(resp) == []


def test_classify_robot_check() -> None:
    resp = _load("br_serp_robot_check.html")
    assert classify_amazon_serp_response(resp) == "challenge"
    assert not looks_like_amazon_search(resp)


def test_classify_soft_error() -> None:
    resp = _load("br_serp_soft_error.html")
    assert classify_amazon_serp_response(resp) == "soft_error"
    assert classify_amazon_empty_result(resp) == "incomplete"
    assert not looks_like_amazon_search(resp)


def test_classify_incomplete_shell() -> None:
    resp = _load("br_serp_incomplete_shell.html")
    assert classify_amazon_serp_response(resp) == "incomplete"
    assert classify_amazon_empty_result(resp) == "incomplete"


def test_layout_variant_dp_and_data_asin_with_dedup() -> None:
    resp = _load("br_serp_layout_variant_dp_asin.html")
    assert looks_like_amazon_search(resp)
    cands = parse_amazon_search_results(
        resp, host="amazon.com.br", source="amazon-br-search", limit=10
    )
    asins = [c.product_id for c in cands]
    assert asins.count("B09VCHQHZ6") == 1
    assert "B0C3T4MFMM" in asins
    assert "B091J3NYVF" in asins
    assert classify_amazon_serp_response(resp) == "valid"


def test_store_search_soft_error_is_upstream_blocked_not_no_results() -> None:
    fetcher = _RecordingFetcher(_load("br_serp_soft_error.html"))
    service = StoreSearchService(fetcher=fetcher)
    with pytest.raises(RequestError) as exc:
        service.search("amazon_br", "teste")
    assert exc.value.code == "UPSTREAM_BLOCKED"
    assert "Algo deu errado" in str(exc.value) or "Dogs" in str(exc.value)


def test_store_search_incomplete_not_no_results() -> None:
    fetcher = _RecordingFetcher(_load("br_serp_incomplete_shell.html"))
    service = StoreSearchService(fetcher=fetcher)
    with pytest.raises(RequestError) as exc:
        service.search("amazon_br", "teste")
    assert exc.value.code == "SEARCH_INCOMPLETE_RESPONSE"


def test_store_search_genuine_empty_returns_empty_list() -> None:
    fetcher = _RecordingFetcher(_load("br_serp_zero_results.html"))
    service = StoreSearchService(fetcher=fetcher)
    assert service.search("amazon_br", "xyzxyzxyznonexistent999") == []


def test_store_search_robot_is_waf_not_no_results() -> None:
    fetcher = _RecordingFetcher(_load("br_serp_robot_check.html"))
    service = StoreSearchService(fetcher=fetcher)
    with pytest.raises(RequestError) as exc:
        service.search("amazon_br", "teste")
    assert exc.value.code == "UPSTREAM_WAF_BLOCKED"


def test_http_soft_error_falls_back_to_browser() -> None:
    url = "https://www.amazon.com.br/s?k=ssd"
    soft = _load("br_serp_soft_error.html", url=url)
    good = _load("br_serp_valid.html", url=url)
    http = _RecordingFetcher(soft)
    browser = _RecordingFetcher(good)
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == [url]
    assert looks_like_amazon_search(response)


def test_http_search_accepted_when_valid() -> None:
    url = "https://www.amazon.com.br/s?k=ssd"
    serp = _load("br_serp_valid.html", url=url)
    http = _RecordingFetcher(serp)
    browser = _RecordingFetcher(_load("br_serp_soft_error.html", url=url))
    response = AmazonHttpFirstHtmlFetcher(http=http, browser=browser).fetch(url)
    assert browser.calls == []
    assert response is serp
