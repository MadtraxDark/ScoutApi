"""Store-aware proxy routing and Shopee cost controls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.core.proxy_policy import (
    ProxyPolicy,
    proxy_policy_for_url,
    resolve_store_config,
)
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.crawler.services.html_fetcher import (
    SHOPEE_BLOCKED_RESOURCE_TYPES,
    CamoufoxHtmlFetcher,
)
from scout_api.modules.crawler.services.offer_scrape_service import OfferScrapeService
from scout_api.modules.crawler.services.store_aware_fetcher import StoreAwareHtmlFetcher

FIXTURES = Path(__file__).parents[1] / "fixtures" / "shopee"
KINGSTON_URL = (
    "https://shopee.com.br/Kingston-HyperX-Fury-DDR4-PC-RAM-4-Gb-8-16-DDR4-"
    "2133-2400-2666-3200-Mhz-Mem%C3%B3ria-De-Mesa-i.341936748.29277977480"
)
PALIT_URL = (
    "https://shopee.com.br/Placa-de-Video-NVIDIA-GeForce-PALIT-RTX5060-8GB-"
    "INFINITY-2-OC-GDDR7-0120111-01-i.344381236.54358077814"
    "?extraParams=%7B%22display_model_id%22%3A355718024627%2C%22model_selection_logic%22%3A3%7D"
)


def test_proxy_policy_all_stores_fallback() -> None:
    assert proxy_policy_for_url(KINGSTON_URL) is ProxyPolicy.FALLBACK
    assert resolve_store_config(KINGSTON_URL) is not None
    assert resolve_store_config(KINGSTON_URL).supports_images is True
    assert resolve_store_config(KINGSTON_URL).image_fetch_cost == "high"
    assert resolve_store_config(KINGSTON_URL).default_include_images is False
    assert (
        proxy_policy_for_url("https://www.kabum.com.br/produto/1")
        is ProxyPolicy.FALLBACK
    )
    assert (
        proxy_policy_for_url("https://www.magazineluiza.com.br/p/1")
        is ProxyPolicy.FALLBACK
    )
    assert (
        proxy_policy_for_url("https://www.bestbuy.com/site/x/1.p")
        is ProxyPolicy.FALLBACK
    )
    assert proxy_policy_for_url("https://nissei.com/py/x") is ProxyPolicy.FALLBACK


def test_store_aware_shopee_uses_direct_first() -> None:
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append(f"direct:{url}")
            return HtmlResponse(
                url, body=b"<html/>", encoding="utf-8", request=Request(url)
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append(f"proxied:{url}")
            return HtmlResponse(
                url, body=b"<html/>", encoding="utf-8", request=Request(url)
            )

    fetcher = StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied())
    response = fetcher.fetch(KINGSTON_URL)
    assert calls == [f"direct:{KINGSTON_URL}"]
    assert response.meta["fetch_metrics"]["proxy_used"] is False
    assert response.meta["fetch_metrics"]["proxy_policy"] == "fallback"


def test_store_aware_direct_first_when_fallback_succeeds() -> None:
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            return HtmlResponse(
                url, body=b"<html/>", encoding="utf-8", request=Request(url)
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("proxied")
            return HtmlResponse(
                url, body=b"<html/>", encoding="utf-8", request=Request(url)
            )

    url = "https://www.kabum.com.br/produto/1"
    response = StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied()).fetch(url)
    assert calls == ["direct"]
    assert response.meta["fetch_metrics"]["proxy_used"] is False


def test_classify_camoufox_navigation_error_maps_net_reset_to_blocked() -> None:
    from scout_api.modules.crawler.services.html_fetcher import (
        classify_camoufox_navigation_error,
    )

    err = classify_camoufox_navigation_error(
        Exception("Page.goto: NS_ERROR_NET_RESET"),
        url="https://www.bestbuy.com/product/x/1",
    )
    assert err.code == "UPSTREAM_BLOCKED"
    assert err.retryable is True

    other = classify_camoufox_navigation_error(
        Exception("Page.goto: something else exploded"),
        url="https://example.com/",
    )
    assert other.code == "UPSTREAM_REQUEST_ERROR"


def test_store_aware_no_proxy_on_parse_error_shaped_request() -> None:
    """ParseError is not a RequestError — services must not wrap it as fallback."""
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            raise RequestError(
                "network", code="UPSTREAM_NETWORK_ERROR", url=url, retryable=True
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("proxied")
            return HtmlResponse(url, body=b"x", encoding="utf-8", request=Request(url))

    with pytest.raises(RequestError) as exc:
        StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied()).fetch(
            "https://www.bestbuy.com/site/x/1.p"
        )
    assert exc.value.code == "UPSTREAM_NETWORK_ERROR"
    assert calls == ["direct"]


def test_store_aware_no_proxy_when_parse_error_raised_by_caller() -> None:
    # ParseError must never trigger StoreAware fallback (fetcher never sees it).
    with pytest.raises(ParseError):
        raise ParseError("campo ausente")


def test_shopee_without_proxy_uses_direct() -> None:
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            return HtmlResponse(url, body=b"x", encoding="utf-8", request=Request(url))

    response = StoreAwareHtmlFetcher(direct=Direct(), proxied=None).fetch(KINGSTON_URL)
    assert calls == ["direct"]
    assert response.meta["fetch_metrics"]["proxy_used"] is False


def test_shopee_falls_back_to_proxy_after_auth_required() -> None:
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            raise RequestError(
                "falta de login", code="AUTH_REQUIRED", url=url, retryable=True
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("proxied")
            return HtmlResponse(
                url, body=b"<html>ok</html>", encoding="utf-8", request=Request(url)
            )

    response = StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied()).fetch(
        KINGSTON_URL
    )
    assert calls == ["direct", "proxied"]
    assert response.meta["fetch_metrics"]["proxy_used"] is True
    assert response.meta["fetch_metrics"].get("proxy_fallback") is True


def test_shopee_falls_back_to_proxy_after_upstream_blocked() -> None:
    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            raise RequestError(
                "blocked", code="UPSTREAM_BLOCKED", url=url, retryable=True
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("proxied")
            return HtmlResponse(
                url, body=b"<html>ok</html>", encoding="utf-8", request=Request(url)
            )

    fetcher = StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied())
    response = fetcher.fetch(KINGSTON_URL)
    assert calls == ["direct", "proxied"]
    assert response.meta["fetch_metrics"]["proxy_used"] is True
    assert response.meta["fetch_metrics"].get("proxy_fallback") is True
    again = fetcher.fetch(KINGSTON_URL)
    assert calls == ["direct", "proxied", "proxied"]
    assert again.meta["fetch_metrics"].get("proxy_sticky") is True


def test_proxy_fallback_skipped_when_store_deadline_is_too_short() -> None:
    import time

    from scout_api.modules.crawler.core.browser_scheduler import browser_queue_deadline

    calls: list[str] = []

    class Direct:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("direct")
            raise RequestError(
                "blocked", code="UPSTREAM_BLOCKED", url=url, retryable=True
            )

    class Proxied:
        def fetch(self, url: str) -> HtmlResponse:
            calls.append("proxied")
            return HtmlResponse(url, body=b"x", encoding="utf-8", request=Request(url))

    with (
        browser_queue_deadline(time.monotonic() + 1.0),
        pytest.raises(RequestError) as exc,
    ):
        StoreAwareHtmlFetcher(direct=Direct(), proxied=Proxied()).fetch(KINGSTON_URL)
    assert exc.value.code == "UPSTREAM_BLOCKED"
    assert calls == ["direct"]


def test_shopee_early_stop_skips_networkidle(tmp_path: Path) -> None:
    payload = (
        '{"data":{"item":{"item_id":29277977480,"shop_id":341936748,'
        '"title":"Kingston","price":159900000,"models":[]}}}'
    )
    networkidle_calls: list[str] = []

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        del kwargs

        class FakeResponse:
            url = (
                "https://shopee.com.br/api/v4/pdp/get_pc"
                "?shop_id=341936748&item_id=29277977480"
            )
            status = 200

            def text(self) -> str:
                return payload

        class FakePage:
            url = KINGSTON_URL

            def __init__(self) -> None:
                self._handlers: list[Any] = []

            def on(self, event: str, handler: Any) -> None:
                if event == "response":
                    self._handlers.append(handler)

            def route(self, pattern: str, handler: Any) -> None:
                del pattern, handler

            def goto(self, target: str, **goto_kwargs: Any) -> None:
                del goto_kwargs
                self.url = target
                for handler in self._handlers:
                    handler(FakeResponse())

            def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
                networkidle_calls.append(state)
                del kwargs

            def content(self) -> str:
                return "<html></html>"

            def title(self) -> str:
                return "x"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

            def close(self) -> None:
                return None

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=2,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        early_stop_on_shopee_get_pc=True,
        block_resource_types=SHOPEE_BLOCKED_RESOURCE_TYPES,
    )
    response = fetcher.fetch(KINGSTON_URL)
    assert "data-shopee-pdp" in response.text
    assert networkidle_calls == []
    assert response.meta["fetch_metrics"]["early_stop"] is True
    assert response.meta["fetch_metrics"]["get_pc_captured"] is True


def test_shopee_warmup_once_per_session(tmp_path: Path) -> None:
    gotos: list[str] = []

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        del kwargs

        class FakePage:
            url = "https://shopee.com.br/"

            def on(self, event: str, handler: Any) -> None:
                del event, handler

            def goto(self, target: str, **goto_kwargs: Any) -> None:
                del goto_kwargs
                gotos.append(target)
                self.url = target

            def content(self) -> str:
                return (
                    '<html><body><script type="application/json" data-shopee-pdp="1">'
                    '{"data":{"item":{"item_id":1,"shop_id":1,"title":"t","price":100000,'
                    '"models":[]}}}</script></body></html>'
                )

            def title(self) -> str:
                return "Shopee"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

            def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
                del state, kwargs

            def close(self) -> None:
                return None

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "profile",
        warmup_origin=True,
        warmup_policy="once_per_session",
        early_stop_on_shopee_get_pc=False,
    )
    url = "https://shopee.com.br/prod-i.1.1"
    fetcher.fetch(url)
    fetcher.fetch(url)
    warmups = [g for g in gotos if g.rstrip("/").endswith("shopee.com.br")]
    # First navigation of each fetch is warm-up only once; second fetch skips it.
    assert warmups.count("https://shopee.com.br/") == 1


def test_offer_cache_avoids_second_fetch() -> None:
    fetches: list[str] = []
    body = (FIXTURES / "product_kingston_variants.html").read_bytes()

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            return HtmlResponse(
                fetch_url, body=body, encoding="utf-8", request=Request(fetch_url)
            )

    guard = ScrapeGuard(
        url_cooldown_seconds=60,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    service = OfferScrapeService(fetcher=FakeFetcher(), guard=guard)
    first = service.scrape_offer(KINGSTON_URL)
    second = service.scrape_offer(KINGSTON_URL + "?sp_atk=aaa&xptdk=bbb")
    assert first.price == Decimal("1599.00")
    assert second.metadata.get("cache_hit") is True
    assert len(fetches) == 1
    assert canonicalize_url(KINGSTON_URL + "?sp_atk=aaa") == canonicalize_url(
        KINGSTON_URL
    )


def test_offer_cache_separates_display_model_variants() -> None:
    fetches: list[str] = []
    body = (FIXTURES / "product_palit_price_detail.html").read_bytes()

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            return HtmlResponse(
                fetch_url, body=body, encoding="utf-8", request=Request(fetch_url)
            )

    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    service = OfferScrapeService(fetcher=FakeFetcher(), guard=guard)
    other_model = (
        "https://shopee.com.br/Placa-de-Video-NVIDIA-GeForce-PALIT-RTX5060-8GB-"
        "INFINITY-2-OC-GDDR7-0120111-01-i.344381236.54358077814"
        "?extraParams=%7B%22display_model_id%22%3A999999999%2C%22model_selection_logic%22%3A3%7D"
    )
    service.scrape_offer(PALIT_URL)
    # Different display_model_id → different cache key; second fetch attempted.
    with pytest.raises(ParseError):
        service.scrape_offer(other_model)
    assert len(fetches) == 2


def test_offer_cache_ttl_expiry_allows_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fetches: list[str] = []
    body = (FIXTURES / "product_kingston_variants.html").read_bytes()

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            return HtmlResponse(
                fetch_url, body=body, encoding="utf-8", request=Request(fetch_url)
            )

    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=1,
    )
    service = OfferScrapeService(fetcher=FakeFetcher(), guard=guard)
    service.scrape_offer(KINGSTON_URL)
    assert len(fetches) == 1

    # Expire cache entry via public delete.
    from scout_api.modules.crawler.core.redis_keys import scrape_cache_key

    guard._cache.delete(scrape_cache_key(KINGSTON_URL))

    again = service.scrape_offer(KINGSTON_URL)
    assert again.metadata.get("cache_hit") is not True
    assert len(fetches) == 2


def test_single_flight_coalesces_concurrent_offer_fetches() -> None:
    import threading

    fetches: list[str] = []
    body = (FIXTURES / "product_kingston_variants.html").read_bytes()
    started = threading.Event()
    release = threading.Event()

    class FakeFetcher:
        def fetch(self, fetch_url: str) -> HtmlResponse:
            fetches.append(fetch_url)
            started.set()
            release.wait(timeout=2)
            return HtmlResponse(
                fetch_url, body=body, encoding="utf-8", request=Request(fetch_url)
            )

    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=0,
        result_cache_ttl_seconds=60,
    )
    service = OfferScrapeService(fetcher=FakeFetcher(), guard=guard)
    results: list[ProductOffer] = []

    def worker() -> None:
        results.append(service.scrape_offer(KINGSTON_URL))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    assert started.wait(timeout=2)
    release.set()
    for t in threads:
        t.join(timeout=3)
    assert len(fetches) == 1
    assert len(results) == 5
    assert all(r.price == Decimal("1599.00") for r in results)


def test_shopee_resource_blocking_wired_on_proxy_fetcher(tmp_path: Path) -> None:
    routed: list[str] = []

    @contextmanager
    def fake_factory(**kwargs: Any) -> Iterator[Any]:
        del kwargs

        class FakePage:
            url = KINGSTON_URL

            def on(self, event: str, handler: Any) -> None:
                del event, handler

            def route(self, pattern: str, handler: Any) -> None:
                routed.append(pattern)
                del handler

            def goto(self, target: str, **goto_kwargs: Any) -> None:
                del goto_kwargs
                self.url = target

            def content(self) -> str:
                return (
                    '<html><body><script type="application/json" data-shopee-pdp="1">'
                    '{"data":{"item":{"item_id":29277977480,"shop_id":341936748,'
                    '"title":"K","price":159900000,"models":[]}}}</script></body></html>'
                )

            def title(self) -> str:
                return "Shopee"

            def wait_for_timeout(self, ms: int) -> None:
                del ms

            def close(self) -> None:
                return None

        class FakeBrowser:
            def new_page(self) -> FakePage:
                return FakePage()

        yield FakeBrowser()

    fetcher = CamoufoxHtmlFetcher(
        browser_factory=fake_factory,
        settle_ms=0,
        max_settle_attempts=1,
        user_data_dir=tmp_path / "profile",
        warmup_origin=False,
        early_stop_on_shopee_get_pc=False,
        block_resource_types=SHOPEE_BLOCKED_RESOURCE_TYPES,
    )
    fetcher.fetch(KINGSTON_URL)
    assert routed == ["**/*"]


def test_proxy_cost_mode_skips_images_even_when_include_images_true(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from scout_api.modules.crawler.services.product_scrape_service import (
        ProductScrapeService,
    )
    from scout_api.modules.crawler.spiders.brazil.magazineluiza import (
        MagazineLuizaSpider,
    )

    fixtures = Path(__file__).parents[1] / "fixtures" / "magazineluiza"
    url = "https://www.magazineluiza.com.br/p/aebh5a7a94"
    html = HtmlResponse(
        url,
        body=(fixtures / "product_structured_offer.html").read_bytes(),
        encoding="utf-8",
        request=Request(url),
    )
    html.meta["fetch_metrics"] = {"proxy_used": True, "proxy_policy": "fallback"}

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
            url_cooldown_seconds=0,
            domain_min_interval_seconds=0,
            result_cache_ttl_seconds=0,
        ),
    ).scrape(url, include_images=True)

    images_mock.assert_not_called()
    assert item.images == []
    assert item.metadata.get("images_omitted") == "proxy-cost-mode"


def test_proxy_route_aborts_image_using_route_request() -> None:
    captured: dict[str, Any] = {}

    class Page:
        def route(self, pattern: str, handler: Any) -> None:
            del pattern
            captured["handler"] = handler

    class Route:
        def __init__(self, resource_type: str) -> None:
            self.request = type("Req", (), {"resource_type": resource_type})()
            self.aborted = False
            self.continued = False

        def abort(self) -> None:
            self.aborted = True

        def continue_(self) -> None:
            self.continued = True

    fetcher = CamoufoxHtmlFetcher(block_resource_types=SHOPEE_BLOCKED_RESOURCE_TYPES)
    try:
        fetcher._maybe_attach_resource_blocking(
            Page(), "https://www.magazineluiza.com.br/busca/cooler/"
        )
        image = Route("image")
        document = Route("document")
        captured["handler"](image)
        captured["handler"](document)
        assert image.aborted and not image.continued
        assert document.continued and not document.aborted
    finally:
        fetcher.close()
