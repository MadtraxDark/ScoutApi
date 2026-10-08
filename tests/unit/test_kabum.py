from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.spiders.brazil.kabum import KabumSpider

FIXTURE = Path(__file__).parents[1] / "fixtures" / "kabum" / "product_marketplace.html"
URL = "https://www.kabum.com.br/produto/1033699/placa-de-video-msi-rtx-5060-ti"


def response() -> HtmlResponse:
    return HtmlResponse(
        URL, body=FIXTURE.read_bytes(), encoding="utf-8", request=Request(URL)
    )


def test_kabum_offer_keeps_regular_pix_original_and_installment_semantics() -> None:
    offer = KabumSpider().extract_offer(response())
    assert offer.product_id == "1033699"
    assert offer.seller == "CASA DO PROVEDOR"
    assert offer.price == Decimal("4085.56")
    assert offer.pix_price == Decimal("3677.00")
    assert offer.original_price == Decimal("5000.00")
    assert offer.installment_count == 10
    assert offer.installment_price == Decimal("408.55")
    assert offer.available is True
    assert offer.metadata["source"]["price"] == "product-state"


def test_kabum_details_and_images_use_product_state_only() -> None:
    spider = KabumSpider()
    details = spider.extract_details(response())
    assert details.product_id == "1033699"
    assert details.title == "Placa De Vídeo MSI RTX 5060 Ti"
    assert details.brand == "MSI"
    assert details.model == "GeForce RTX 5060 Ti"
    assert details.gtin == "4711377341394"
    assert details.specifications["Memória"] == "8GB GDDR7"
    assert details.specifications.get("vram") == "8 GB"
    assert details.specifications.get("gpu_model") is not None
    assert details.metadata["source"]["category"] == "gpu"
    assert details.images == []
    assert spider.extract_images(response()) == [
        "https://images.kabum.com.br/produtos/fotos/1033699/xlarge/main.png",
        "https://images.kabum.com.br/produtos/fotos/1033699/xlarge/side.png",
        "https://images.kabum.com.br/produtos/fotos/1033699/large/back.png",
    ]


def test_kabum_description_labeled_fields_expose_model_and_mpn() -> None:
    fields = KabumSpider._description_specifications(
        "Fonte MSI MAG A650BNL 650W. Marca: MSI Linha: MAG "
        "Modelo: MAG A650BNL Part Number: 306-7ZPAX39-HH9 "
        "Potência nominal: 650 W Certificação de eficiência: 80 PLUS Bronze"
    )

    assert fields["model"] == "mag a650bnl"
    assert fields["Part Number"] == "306-7zpax39-hh9"
    assert fields["wattage"] == "650 w"
    assert fields["efficiency"] == "80 plus bronze"


def test_kabum_full_scrape_only_calls_gallery_when_requested(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    spider = KabumSpider()
    images = MagicMock(wraps=spider.extract_images)
    spider.extract_images = images
    monkeypatch.setattr(
        "scout_api.modules.crawler.services.product_scrape_service.resolve_store_spider",
        lambda _url: spider,
    )

    class Fetcher:
        def fetch(self, _url: str) -> HtmlResponse:
            return response()

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
