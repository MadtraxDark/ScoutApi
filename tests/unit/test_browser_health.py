"""Browser launch classification + process-wide circuit breaker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scout_api.modules.crawler.core.browser_health import (
    BrowserHealthState,
    TrialToken,
    browser_unavailable_error,
    classify_browser_error,
    get_browser_circuit,
    is_browser_infrastructure_error,
    is_browser_launch_failure,
    reset_browser_circuit_for_tests,
)
from scout_api.modules.crawler.services.html_fetcher import (
    classify_camoufox_navigation_error,
)


@pytest.fixture(autouse=True)
def _clean_circuit() -> None:
    reset_browser_circuit_for_tests()
    yield
    reset_browser_circuit_for_tests()


def test_launch_timeout_is_browser_launch_error() -> None:
    exc = TimeoutError("Timeout 45000ms exceeded.")
    assert is_browser_launch_failure(exc)
    err = classify_camoufox_navigation_error(exc, url="https://example.com/p/1")
    assert err.code == "BROWSER_LAUNCH_ERROR"
    assert not err.retryable


def test_page_goto_timeout_is_not_launch_failure() -> None:
    exc = TimeoutError("Page.goto: Timeout 90000ms exceeded.")
    assert not is_browser_launch_failure(exc)
    err = classify_camoufox_navigation_error(exc, url="https://example.com/p/1")
    assert err.code == "UPSTREAM_REQUEST_ERROR"


def test_profile_io_error_is_launch_failure() -> None:
    exc = OSError("Input/output error reading profile cache2")
    assert is_browser_launch_failure(exc)
    err = classify_browser_error(exc, url="https://example.com/")
    assert err.code == "BROWSER_LAUNCH_ERROR"


def test_circuit_opens_on_launch_failure_not_page_timeout() -> None:
    circuit = get_browser_circuit(threshold=1, cooldown_seconds=60)
    assert circuit.allow()
    now = datetime.now(UTC)
    circuit.record_launch_failure(now=now)
    assert not circuit.allow(now=now)
    assert circuit.snapshot()["state"] == BrowserHealthState.UNAVAILABLE.value
    # Half-open after cooldown: allow() is False; claim_trial() grants one probe token.
    half = now + timedelta(seconds=61)
    assert not circuit.allow(now=half)  # HALF_OPEN → allow False (use claim_trial)
    assert circuit.snapshot()["state"] == BrowserHealthState.DEGRADED.value
    token = circuit.claim_trial(now=half)
    assert isinstance(token, TrialToken), "Must receive probe token in HALF_OPEN"
    circuit.complete_trial(token, success=True)
    assert circuit.snapshot()["state"] == BrowserHealthState.HEALTHY.value


def test_circuit_open_raises_infrastructure_unavailable() -> None:
    circuit = get_browser_circuit(threshold=1, cooldown_seconds=3600)
    circuit.record_launch_failure()
    err = browser_unavailable_error(url="https://www.kabum.com.br/x")
    assert err.code == "BROWSER_INFRASTRUCTURE_UNAVAILABLE"
    assert is_browser_infrastructure_error(err.code)


def test_cooldown_probe_window() -> None:
    circuit = get_browser_circuit(threshold=1, cooldown_seconds=30)
    t0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)
    circuit.record_launch_failure(now=t0)
    assert not circuit.allow(now=t0 + timedelta(seconds=10))
    # After cooldown: HALF_OPEN — allow() is False; a probe must use claim_trial().
    half_open_at = t0 + timedelta(seconds=31)
    assert not circuit.allow(now=half_open_at)
    assert circuit.state == BrowserHealthState.DEGRADED
    token = circuit.claim_trial(now=half_open_at)
    assert token is not None, "Must receive probe token when HALF_OPEN"
