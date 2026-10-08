import json
from unittest.mock import MagicMock

import pytest

from scout_api.modules.crawler.services.html_fetcher import CamoufoxHtmlFetcher
from scout_api.modules.crawler.services.magalu_readiness import magalu_document_ready


def test_magalu_ready_pdp_does_not_wait_for_analytics_network_idle() -> None:
    state = {
        "props": {
            "pageProps": {
                "data": {
                    "item": {
                        "id": "238922200",
                        "title": "Phone",
                        "offers": [{"price": "6443"}],
                    }
                }
            }
        }
    }
    page = MagicMock()
    page.content.return_value = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(state)
        + "</script>"
    )
    fetcher = CamoufoxHtmlFetcher()
    try:
        fetcher._goto(page, "https://www.magazineluiza.com.br/p/238922200/")
        page.goto.assert_called_once()
        page.wait_for_load_state.assert_not_called()
    finally:
        fetcher.close()


def test_magalu_sec_cpt_does_not_wait_for_network_idle() -> None:
    page = MagicMock()
    page.content.return_value = (
        "<html><body><div id='sec-if-cpt-container'>"
        "<div class='behavioral-content'></div></div></body></html>"
    )
    fetcher = CamoufoxHtmlFetcher()
    try:
        fetcher._goto(page, "https://www.magazineluiza.com.br/busca/cooler/")
        page.wait_for_load_state.assert_not_called()
    finally:
        fetcher.close()


def test_magalu_incomplete_document_keeps_existing_wait() -> None:
    page = MagicMock()
    page.content.return_value = "<html>loading</html>"
    fetcher = CamoufoxHtmlFetcher()
    try:
        fetcher._goto(page, "https://www.magazineluiza.com.br/p/238922200/")
        page.wait_for_load_state.assert_called_once()
    finally:
        fetcher.close()


def test_magalu_origin_warmup_does_not_wait_for_analytics() -> None:
    page = MagicMock()
    page.content.return_value = "<html><title>Magalu</title></html>"
    fetcher = CamoufoxHtmlFetcher()
    try:
        fetcher._goto(page, "https://www.magazineluiza.com.br/")
        page.wait_for_load_state.assert_not_called()
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"props": []},
        {
            "props": {
                "pageProps": {
                    "data": {
                        "item": {
                            "id": "1",
                            "title": "Phone",
                            "offers": "loading",
                        }
                    }
                }
            },
        },
    ],
)
def test_readiness_rejects_incomplete_or_malformed_payload(payload) -> None:
    html = '<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>"
    assert not magalu_document_ready(html, "https://www.magazineluiza.com.br/p/1/")
