"""Unit tests for Mercado Livre spider (JSON-LD / PDP fixtures)."""

from decimal import Decimal
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.spiders.brazil.mercadolivre import MercadoLivreSpider
from scout_api.modules.crawler.spiders.registry import resolve_store_spider
from scout_api.modules.matching.search_adapters.brazil.mercadolivre import (
    MercadoLivreSearchAdapter,
)

FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "mercadolivre" / "product_catalog_pdp.html"
)
URL = (
    "https://www.mercadolivre.com.br/"
    "placa-de-video-geforce-rtx-5070-12gb-msi-shadow-3x-oc-6/"
    "p/MLB47363706?pdp_filters=item_id%3AMLB6784630760"
)


def response() -> HtmlResponse:
    return HtmlResponse(
        URL, body=FIXTURE.read_bytes(), encoding="utf-8", request=Request(URL)
    )


def test_registry_resolves_mercadolivre_url() -> None:
    spider = resolve_store_spider(URL)
    assert isinstance(spider, MercadoLivreSpider)


def test_mercadolivre_offer_from_json_ld() -> None:
    offer = MercadoLivreSpider().extract_offer(response())
    assert offer.product_id == "MLB47363706"
    assert offer.sku == "MLB47363706"
    assert offer.price == Decimal("5958.18")
    assert offer.original_price == Decimal("6977.17")
    assert offer.currency == "BRL"
    assert offer.available is True
    assert offer.availability == "available"
    assert offer.installment_count == 10
    assert offer.installment_price == Decimal("595.82")
    assert offer.metadata["item_id"] == "MLB6784630760"
    assert offer.metadata["source"]["price"] == "json-ld"
    assert "matt_tool" not in offer.canonical_url
    assert "pdp_filters=item_id%3AMLB6784630760" in offer.canonical_url


def test_mercadolivre_catalog_vs_item_id_semantics() -> None:
    spider = MercadoLivreSpider()
    offer = spider.extract_offer(response())
    # Catalog /p/MLB… is product_id; selected listing lives in metadata.item_id.
    assert offer.product_id.startswith("MLB")
    assert offer.product_id != offer.metadata["item_id"]
    assert offer.metadata["catalog_product_id"] == offer.product_id
    assert offer.metadata["item_id"].startswith("MLB")


def test_mercadolivre_search_url_and_parse() -> None:
    adapter = MercadoLivreSearchAdapter()
    assert (
        "lista.mercadolivre.com.br" in adapter.build_search_request("msi rtx 5070").url
    )
    serp = HtmlResponse(
        "https://lista.mercadolivre.com.br/msi-rtx-5070",
        body=(
            b"<html><a class='ui-search-link' "
            b"href='https://www.mercadolivre.com.br/gpu/p/MLB111' "
            b"title='MSI RTX 5070'>card</a>"
            b"<a href='https://www.mercadolivre.com.br/norton/p/MLB999"
            b"#intervention_type=digital_goods'>Norton</a>"
            b"<a class='poly-component__title' "
            b"href='https://www.mercadolivre.com.br/"
            b"placa-de-video-gigabyte-geforce-rtx-5060/p/MLB222'>"
            b"Placa De Video Gigabyte Geforce Rtx 5060</a>"
            b"</html>"
        ),
        encoding="utf-8",
        request=Request("https://lista.mercadolivre.com.br/msi-rtx-5070"),
    )
    candidates = adapter.parse_candidates(serp)
    assert candidates
    assert candidates[0].product_id == "MLB111"
    assert candidates[0].title and "MSI" in candidates[0].title
    ids = {c.product_id for c in candidates}
    assert "MLB999" not in ids  # intervention carousel skipped
    assert "MLB222" in ids
    gigabyte = next(c for c in candidates if c.product_id == "MLB222")
    assert gigabyte.title and "Gigabyte" in gigabyte.title


def test_mercadolivre_search_rejects_account_verification() -> None:
    adapter = MercadoLivreSearchAdapter()
    verify = HtmlResponse(
        "https://www.mercadolivre.com.br/gz/account-verification?go=x",
        body=b"<html><body>Verificacao</body></html>",
        encoding="utf-8",
        request=Request("https://lista.mercadolivre.com.br/rtx"),
    )
    with pytest.raises(RequestError) as exc:
        adapter.parse_candidates(verify)
    assert exc.value.code == "AUTH_REQUIRED"


def test_mercadolivre_details_and_images() -> None:
    spider = MercadoLivreSpider()
    details = spider.extract_details(response())
    assert details.product_id == "MLB47363706"
    assert "5070" in details.title
    assert details.brand == "MSI"
    assert details.model == "GeForce RTX 5070"
    assert details.variant == "Shadow 3X OC"
    assert details.images == []
    images = spider.extract_images(response())
    assert images
    assert images[0].startswith("https://http2.mlstatic.com/")


def test_mercadolivre_rejects_snoopy_challenge_html() -> None:
    body = (
        "<html><body><button id='continue-button' disabled>"
        "Continuar</button>"
        "<script>function verifyChallenge(){}</script>"
        "<script src='https://http2.mlstatic.com/frontend-assets/"
        "snoopy-generation-web/latest/snoopy-script.js'></script>"
        "</body></html>"
    )
    challenge = HtmlResponse(
        URL, body=body.encode("utf-8"), encoding="utf-8", request=Request(URL)
    )
    try:
        MercadoLivreSpider().extract_offer(challenge)
        raise AssertionError("expected RequestError UPSTREAM_BLOCKED")
    except RequestError as exc:
        assert exc.code == "UPSTREAM_BLOCKED"
        assert "Snoopy" in str(exc) or "challenge" in str(exc).casefold()
