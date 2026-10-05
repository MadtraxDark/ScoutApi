from decimal import Decimal
from typing import Any

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)


def _item(url: str) -> ProductPriceItem:
    return ProductPriceItem(
        store="nissei",
        country="PY",
        product_id="1",
        title="Produto",
        url=url,
        canonical_url=url,
        currency="PYG",
        price=Decimal("1000"),
    )


def test_scrape_guard_serves_cache_instead_of_second_upstream_hit() -> None:
    url = "https://nissei.com/py/produto-a"
    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    fetches: list[str] = []

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            body = (
                b"<html><body><h1>Produto</h1>"
                b'<meta itemprop="price" content="1000">'
                b"<span>SKU 1</span><span>En stock</span></body></html>"
            )
            return HtmlResponse(
                fetch_url, body=body, encoding="utf-8", request=Request(fetch_url)
            )

    service = ProductScrapeService(fetcher=FakeFetcher(), guard=guard)
    first = service.scrape(url)
    second = service.scrape(url)

    assert first.price == Decimal("1000")
    assert second.metadata.get("cache_hit") is True
    assert fetches == [url]


def test_scrape_guard_blocks_duplicate_url_without_cache() -> None:
    url = "https://nissei.com/py/produto-b"
    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=1,
    )
    guard.acquire_for_live_fetch(url)
    with pytest.raises(RequestError) as exc:
        guard.acquire_for_live_fetch(url)
    assert exc.value.code == "DUPLICATE_REQUEST"
    assert exc.value.retryable is True
    assert exc.value.retry_after is not None and exc.value.retry_after >= 1


def test_scrape_guard_blocks_domain_burst() -> None:
    guard = ScrapeGuard(
        url_cooldown_seconds=1,
        domain_min_interval_seconds=30,
        result_cache_ttl_seconds=1,
    )
    guard.acquire_for_live_fetch("https://nissei.com/py/a")
    with pytest.raises(RequestError) as exc:
        guard.acquire_for_live_fetch("https://nissei.com/py/b")
    assert exc.value.code == "RATE_LIMITED"


def test_crawl_endpoint_maps_duplicate_to_429() -> None:
    from fastapi.testclient import TestClient

    from scout_api.main import app
    from scout_api.modules.crawler.router import get_product_scrape_service

    class BlockingService:
        def scrape(self, url: str, *, include_images: bool = False) -> Any:
            raise RequestError(
                "cooldown",
                code="DUPLICATE_REQUEST",
                url=url,
                retryable=True,
                retry_after=42,
            )

    app.dependency_overrides[get_product_scrape_service] = BlockingService
    try:
        response = TestClient(app).post(
            "/crawl",
            json={"url": "https://nissei.com/py/produto"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "DUPLICATE_REQUEST"
    assert detail["retry_after"] == 42
    assert detail["rate_limit_policy"] == "upstream_cooldown"
    assert "rate_limit_scope" not in detail


def test_scrape_guard_cache_key_ignores_tracking_params() -> None:
    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    item = _item("https://nissei.com/py/x")
    guard.store_success("https://nissei.com/py/x?utm_source=ads", item)
    cached = guard.get_cached("https://nissei.com/py/x")
    assert cached is not None
    assert cached.metadata["cache_hit"] is True


def test_scrape_guard_offer_cache_and_projection() -> None:
    from scout_api.modules.crawler.models.product import ProductOffer

    url = "https://nissei.com/py/produto-offer"
    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    offer = ProductOffer(
        store="nissei",
        country="PY",
        product_id="1",
        url=url,
        canonical_url=url,
        currency="PYG",
        price=Decimal("1000"),
    )
    guard.store_offer_success(url, offer)
    cached_offer = guard.get_cached_offer(url)
    assert cached_offer is not None
    assert cached_offer.metadata["cache_hit"] is True
    assert guard.get_cached(url) is None

    # Full item upgrades and serves both paths.
    guard.store_success(url, _item(url))
    assert guard.get_cached(url) is not None
    assert guard.get_cached_offer(url) is not None
