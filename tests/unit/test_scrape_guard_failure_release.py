"""Failed live fetches must not leave our own cooldown behind (ADR 0054)."""

from __future__ import annotations

import pytest

from scout_api.modules.crawler.core.exceptions import (
    ParseError,
    RequestError,
    classify_failure,
)
from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard

URL = "https://www.magazineluiza.com.br/x/p/abc/?seller_id=loja"
OTHER = "https://www.kabum.com.br/produto/1"


def _guard() -> ScrapeGuard:
    return ScrapeGuard(
        url_cooldown_seconds=300,
        domain_min_interval_seconds=15,
        store_failure_cooldown_base_seconds=30,
        store_failure_cooldown_max_seconds=120,
    )


def _fail(guard: ScrapeGuard, url: str, exc: Exception) -> None:
    with pytest.raises(type(exc)):
        with guard.live_fetch(url):
            raise exc


def test_infra_failure_frees_url_and_domain_so_retry_is_not_throttled() -> None:
    guard = _guard()
    _fail(guard, URL, RequestError("launch", code="BROWSER_LAUNCH_ERROR"))
    with guard.live_fetch(URL):  # immediate retry must be allowed
        pass


def test_blocked_failure_frees_url_but_pauses_only_that_store() -> None:
    guard = _guard()
    _fail(guard, URL, RequestError("blocked", code="UPSTREAM_BLOCKED"))
    with pytest.raises(RequestError) as err:
        with guard.live_fetch(URL):
            pass
    assert err.value.code == "RATE_LIMITED"
    assert 0 < (err.value.retry_after or 0) <= 30
    with guard.live_fetch(OTHER):  # other stores unaffected
        pass


def test_store_pause_escalates_and_is_capped() -> None:
    guard = _guard()
    waits = []
    for _ in range(4):
        guard._store_blocked_until.clear()  # let the next attempt through
        guard._domain_next_ok.clear()
        _fail(guard, URL, RequestError("blocked", code="UPSTREAM_BLOCKED"))
        waits.append(guard.store_cooldown_remaining("www.magazineluiza.com.br"))
    assert waits[0] <= 30 < waits[1] <= 60
    assert max(waits) <= 120


def test_success_clears_store_pause() -> None:
    guard = _guard()
    _fail(guard, URL, RequestError("timeout", code="UPSTREAM_REQUEST_ERROR"))
    assert guard.store_cooldown_remaining("www.magazineluiza.com.br") > 0
    guard._clear_store_failures(URL)
    assert guard.store_cooldown_remaining("www.magazineluiza.com.br") == 0


def test_extraction_failure_does_not_pause_store() -> None:
    guard = _guard()
    _fail(guard, URL, ParseError("shape"))
    assert guard.store_cooldown_remaining("www.magazineluiza.com.br") == 0


def test_local_throttle_rejection_keeps_existing_reservation() -> None:
    guard = _guard()
    with guard.live_fetch(URL):
        pass
    with pytest.raises(RequestError) as err:
        with guard.live_fetch(URL):
            pass
    assert err.value.code == "DUPLICATE_REQUEST"
    with pytest.raises(RequestError):  # first call's reservation still held
        with guard.live_fetch(URL):
            pass


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (RequestError("x", code="DUPLICATE_REQUEST"), "local_throttle"),
        (RequestError("x", code="RATE_LIMITED"), "local_throttle"),
        (RequestError("x", code="BROWSER_QUEUE_SATURATED"), "infra"),
        (RequestError("x", code="UPSTREAM_BLOCKED"), "blocked"),
        (RequestError("navigation timeout", code="UPSTREAM_REQUEST_ERROR"), "timeout"),
        (ParseError("x"), "extraction"),
    ],
)
def test_classify_failure(exc: Exception, kind: str) -> None:
    assert classify_failure(exc) == kind


def test_not_found_does_not_pause_store() -> None:
    exc = RequestError("HTTP 404", code="UPSTREAM_HTTP_ERROR", upstream_status=404)
    assert classify_failure(exc) == "not_found"
    guard = _guard()
    _fail(guard, URL, exc)
    assert guard.store_cooldown_remaining("www.magazineluiza.com.br") == 0
