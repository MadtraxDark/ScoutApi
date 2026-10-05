from decimal import Decimal

from fastapi.testclient import TestClient

from scout_api.main import app
from scout_api.modules.crawler.models.product import ProductOffer, ProductPriceItem
from scout_api.modules.crawler.router import (
    get_offer_scrape_service,
    get_product_scrape_service,
)


class FakeScrapeService:
    def scrape(self, url: str, *, include_images: bool = False) -> ProductPriceItem:
        images = (
            [
                "https://a-static.mlcdn.com.br/ps5-front.jpg",
                "https://a-static.mlcdn.com.br/ps5-side.jpg",
            ]
            if include_images
            else []
        )
        return ProductPriceItem(
            store="magazineluiza",
            country="BR",
            product_id="240590700",
            sku="240590700",
            title="Produto de teste",
            brand="Sony",
            model="PS5",
            seller="magazineluiza",
            url=url,
            canonical_url=url,
            currency="BRL",
            price=Decimal("4399.00"),
            pix_price=Decimal("4399.00"),
            available=True,
            availability="available",
            images=images,
        )


class FakeOfferScrapeService:
    def scrape_offer(self, url: str) -> ProductOffer:
        return ProductOffer(
            store="magazineluiza",
            country="BR",
            product_id="240590700",
            sku="240590700",
            seller="magazineluiza",
            url=url,
            canonical_url=url,
            currency="BRL",
            price=Decimal("4399.00"),
            pix_price=Decimal("4199.00"),
            original_price=Decimal("4999.00"),
            installment_price=Decimal("439.90"),
            installment_count=10,
            available=True,
            availability="available",
        )


def test_preview_does_not_register_or_process_catalog_images() -> None:
    from unittest.mock import patch

    from scout_api.modules.images.pipeline import ImagePipeline
    from scout_api.modules.matching.product_registration_service import (
        ProductRegistrationService,
    )

    app.dependency_overrides[get_product_scrape_service] = FakeScrapeService
    try:
        with (
            patch.object(ImagePipeline, "persist_approved") as persist,
            patch.object(ImagePipeline, "register_references") as register,
            patch.object(ProductRegistrationService, "register_saved") as save,
        ):
            response = TestClient(app).post(
                "/crawl",
                json={
                    "url": "https://www.magazineluiza.com.br/p/240590700",
                    "include_images": True,
                },
            )
        assert response.status_code == 200
        persist.assert_not_called()
        register.assert_not_called()
        save.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_product_scrape_service, None)


def test_crawl_endpoint_returns_normalized_product() -> None:
    app.dependency_overrides[get_product_scrape_service] = FakeScrapeService
    try:
        response = TestClient(app).post(
            "/crawl",
            json={"url": "https://www.magazineluiza.com.br/p/240590700"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_id"] == "240590700"
    assert payload["pix_price"] == "4399.00"
    assert payload["title"] == "Produto de teste"
    assert payload["brand"] == "Sony"
    assert payload["model"] == "PS5"
    assert payload["images"] == []


def test_crawl_endpoint_include_images_true_returns_gallery() -> None:
    app.dependency_overrides[get_product_scrape_service] = FakeScrapeService
    try:
        response = TestClient(app).post(
            "/crawl",
            json={
                "url": "https://www.magazineluiza.com.br/p/240590700",
                "include_images": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["images"] == [
        "https://a-static.mlcdn.com.br/ps5-front.jpg",
        "https://a-static.mlcdn.com.br/ps5-side.jpg",
    ]


def test_crawl_offer_endpoint_returns_offer_contract_only() -> None:
    app.dependency_overrides[get_offer_scrape_service] = FakeOfferScrapeService
    try:
        response = TestClient(app).post(
            "/crawl/offer",
            json={"url": "https://www.magazineluiza.com.br/p/240590700"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_id"] == "240590700"
    assert payload["price"] == "4399.00"
    assert payload["pix_price"] == "4199.00"
    assert payload["original_price"] == "4999.00"
    assert payload["installment_price"] == "439.90"
    assert payload["installment_count"] == 10
    assert payload["seller"] == "magazineluiza"
    assert payload["availability"] == "available"
    assert payload["available"] is True
    assert "title" not in payload
    assert "brand" not in payload
    assert "model" not in payload
    assert "description" not in payload
    assert "images" not in payload
