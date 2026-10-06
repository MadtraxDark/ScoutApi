"""Regression: match store resolution and scrape failures surface as ERROR."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.services.store_resolver import eligible_match_store_keys
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.search_candidate import SearchCandidate


def _item(**overrides: object) -> ProductPriceItem:
    base: dict[str, object] = {
        "store": "mercadolivre",
        "country": "BR",
        "product_id": "MLB1",
        "url": "https://www.mercadolivre.com.br/p/MLB1",
        "canonical_url": "https://www.mercadolivre.com.br/p/MLB1",
        "title": "MSI GeForce RTX 5070 Shadow 3X OC 12GB GDDR7",
        "brand": "MSI",
        "model": "RTX 5070",
        "currency": "BRL",
        "price": Decimal("5958.18"),
        "scraped_at": datetime(2026, 9, 19, tzinfo=UTC),
        "metadata": {"item_id": "MLB2", "catalog_product_id": "MLB1"},
    }
    base.update(overrides)
    return ProductPriceItem.model_validate(base)


def test_canonicalize_strips_ml_google_shopping_tracking() -> None:
    raw = (
        "https://www.mercadolivre.com.br/gpu/p/MLB47363706"
        "?pdp_filters=item_id%3AMLB6784630760"
        "&from=gshop&matt_tool=1&gclid=abc&cq_src=google_ads"
    )
    canon = canonicalize_url(raw)
    assert "pdp_filters=item_id%3AMLB6784630760" in canon
    assert "matt_tool" not in canon
    assert "gclid" not in canon
    assert "from=" not in canon
    assert "cq_src" not in canon


def test_eligible_match_excludes_disabled_and_non_search() -> None:
    eligible = set(eligible_match_store_keys())
    assert "mercadolivre" not in eligible
    assert "shopee" not in eligible
    assert STORE_CONFIGS["mercadolivre"].match_enabled is False
    assert STORE_CONFIGS["shopee"].match_enabled is False
    assert STORE_CONFIGS["visaovip"].implemented is True
    # Search-capable + match_enabled stay in (incl. Visão VIP term search).
    assert "visaovip" in eligible
    assert "kabum" in eligible
    assert "magazineluiza" in eligible
    assert "pichau" in eligible
    assert "terabyteshop" in eligible


def test_match_runs_only_eligible_stores() -> None:
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            persist=False,
        )
    )
    covered = (
        set(resp.unmatched_stores)
        | {m.store for m in resp.matches}
        | {e.store for e in resp.errors}
    )
    expected = set(eligible_match_store_keys()) - {"mercadolivre"}
    assert covered == expected
    assert "mercadolivre" not in covered
    assert "shopee" not in covered
    assert "visaovip" in covered
    assert not any(e.code == "SEARCH_UNSUPPORTED" for e in resp.errors)


def test_match_ignores_explicit_disabled_store_request() -> None:
    scrape = MagicMock()
    scrape.scrape.return_value = _item(store="kabum")
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.kabum.com.br/produto/1",
            stores=["mercadolivre", "shopee", "magazineluiza"],
            persist=False,
        )
    )
    searched = {call.args[0] for call in search.search.call_args_list}
    assert searched == {"magazineluiza"}
    assert "mercadolivre" not in resp.unmatched_stores
    assert "shopee" not in resp.unmatched_stores
    assert not any(e.store in {"mercadolivre", "shopee"} for e in resp.errors)


def test_match_reenable_via_match_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scout_api.modules.crawler import stores as stores_mod

    original = stores_mod.STORE_CONFIGS["shopee"]
    monkeypatch.setitem(
        stores_mod.STORE_CONFIGS,
        "shopee",
        stores_mod.StoreConfig(
            original.key,
            original.country,
            original.currency,
            original.domains,
            original.implemented,
            proxy_policy=original.proxy_policy,
            supports_images=original.supports_images,
            image_fetch_cost=original.image_fetch_cost,
            match_enabled=True,
            match_disabled_reason=None,
        ),
    )
    assert "shopee" in eligible_match_store_keys()

    scrape = MagicMock()
    scrape.scrape.return_value = _item(store="kabum")
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.kabum.com.br/produto/1",
            stores=["shopee"],
            persist=False,
        )
    )
    searched = {call.args[0] for call in search.search.call_args_list}
    assert searched == {"shopee"}


def test_match_excludes_reference_store_even_when_requested() -> None:
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["mercadolivre", "kabum"],
            persist=False,
        )
    )
    searched_stores = {call.args[0] for call in search.search.call_args_list}
    assert searched_stores == {"kabum"}
    assert "mercadolivre" not in resp.unmatched_stores
    assert "kabum" in resp.unmatched_stores


def test_scrape_upstream_error_is_not_silent_unmatched() -> None:
    scrape = MagicMock()

    def _scrape(
        url: str,
        *,
        include_images: bool = False,
        purpose: object = None,
    ) -> ProductPriceItem:
        del include_images, purpose
        if "mercadolivre" in url:
            return _item()
        raise RequestError(
            "blocked",
            code="UPSTREAM_BLOCKED",
            url=url,
            retryable=True,
        )

    scrape.scrape.side_effect = _scrape
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(
            url="https://www.kabum.com.br/produto/1",
            title="MSI RTX 5070",
            product_id="1",
        )
    ]

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    assert "kabum" in resp.unmatched_stores
    assert any(e.store == "kabum" and e.code == "UPSTREAM_BLOCKED" for e in resp.errors)
    assert resp.matches == []


def test_browser_infrastructure_error_fail_fast_not_no_match() -> None:
    """Structural browser failure must ERROR the store and stop query burn."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search_calls: list[str] = []

    def _search(
        store_key: str, query: str, *, limit: int = 5, **_kwargs: object
    ) -> list[SearchCandidate]:
        del limit
        search_calls.append(f"{store_key}:{query}")
        raise RequestError(
            "launch failed",
            code="BROWSER_LAUNCH_ERROR",
            url=f"https://www.{store_key}.com.br/busca",
            retryable=False,
        )

    search.search.side_effect = _search

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    # One query attempt then break — not N queries × launch.
    assert len(search_calls) == 1
    assert any(
        e.store == "kabum" and e.code == "BROWSER_LAUNCH_ERROR" for e in resp.errors
    )
    assert resp.errors
    assert resp.matches == []


def test_browser_scrape_failure_stops_remaining_candidates() -> None:
    scrape = MagicMock()

    def _scrape(
        url: str,
        *,
        include_images: bool = False,
        purpose: object = None,
    ) -> ProductPriceItem:
        del include_images, purpose
        if "mercadolivre" in url:
            return _item()
        raise RequestError(
            "circuit open",
            code="BROWSER_INFRASTRUCTURE_UNAVAILABLE",
            url=url,
            retryable=False,
        )

    scrape.scrape.side_effect = _scrape
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(
            url="https://www.kabum.com.br/produto/1",
            title="MSI GeForce RTX 5070 Shadow 3X OC 12GB GDDR7",
            product_id="1",
        ),
        SearchCandidate(
            url="https://www.kabum.com.br/produto/2",
            title="MSI GeForce RTX 5070 Shadow 3X OC 12GB GDDR7",
            product_id="2",
        ),
    ]

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    # Only first candidate scrape attempted after infra failure.
    candidate_calls = [
        c for c in scrape.scrape.call_args_list if "kabum.com.br/produto" in str(c)
    ]
    assert len(candidate_calls) == 1
    assert any(
        e.store == "kabum" and e.code == "BROWSER_INFRASTRUCTURE_UNAVAILABLE"
        for e in resp.errors
    )


def test_match_wave2_stores_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave-2 (non-GTIN-priority) stores overlap when MATCH_STORE_CONCURRENCY > 1."""
    import threading
    import time

    from scout_api.core.config import get_settings

    monkeypatch.setenv("MATCH_STORE_CONCURRENCY", "3")
    get_settings.cache_clear()

    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True

    active = 0
    peak = 0
    lock = threading.Lock()

    def _search(
        store_key: str, query: str, *, limit: int = 5, **_kwargs: object
    ) -> list[SearchCandidate]:
        del query, limit
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return []

    search.search.side_effect = _search

    t0 = time.perf_counter()
    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            # kabum = wave1; remaining three = wave2 (parallel).
            stores=["kabum", "pichau", "magazineluiza", "aliexpress"],
            persist=False,
        )
    )
    elapsed = time.perf_counter() - t0

    covered = set(resp.unmatched_stores)
    assert covered == {"kabum", "pichau", "magazineluiza", "aliexpress"}
    # Three wave-2 stores overlap (each may run 2 SERP queries × sleep).
    assert peak >= 2
    # Serial wall ≈ 4 stores × 2 queries × 0.2s = 1.6s; parallel wave2 ≈ 0.8s.
    assert elapsed < 1.2

    get_settings.cache_clear()
