"""Unit tests for store_capability_health.py — Phase 11.

Coverage:
 - CLOSED/OPEN/HALF_OPEN state transitions
 - claim_trial semantics (single-flight, TTL reap)
 - is_store_search_trip_failure classification
 - NO_RESULTS / ParseError / matcher codes MUST NOT trip
 - WAF / SEARCH_INCOMPLETE_RESPONSE / OSError MAY trip
 - Search circuit OPEN does NOT affect product_scrape circuit
 - store_search_service wiring respects STORE_CAPABILITY_CIRCUIT_ENABLED
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.store_capability_health import (
    StoreCapabilityCircuit,
    StoreCapabilityState,
    get_store_capability_circuit,
    is_store_search_trip_failure,
    reset_store_capability_circuits_for_tests,
    store_capability_unavailable_error,
)


@pytest.fixture(autouse=True)
def _clean_circuits() -> None:
    reset_store_capability_circuits_for_tests()
    yield
    reset_store_capability_circuits_for_tests()


# ---------------------------------------------------------------------------
# is_store_search_trip_failure — classification
# ---------------------------------------------------------------------------


def test_waf_code_trips() -> None:
    exc = RequestError("blocked", code="UPSTREAM_WAF_BLOCKED")
    assert is_store_search_trip_failure(exc)


def test_upstream_blocked_trips() -> None:
    exc = RequestError("blocked", code="UPSTREAM_BLOCKED")
    assert is_store_search_trip_failure(exc)


def test_search_incomplete_response_trips() -> None:
    exc = RequestError("incomplete", code="SEARCH_INCOMPLETE_RESPONSE")
    assert is_store_search_trip_failure(exc)


def test_os_error_trips() -> None:
    exc = OSError("Connection reset by peer")
    assert is_store_search_trip_failure(exc)


def test_connection_error_trips() -> None:
    exc = ConnectionError("Connection refused")
    assert is_store_search_trip_failure(exc)


def test_parse_error_must_not_trip() -> None:
    """ParseError isolation — no upstream evidence — MUST NOT trip."""
    exc = ParseError("unexpected shape")
    assert not is_store_search_trip_failure(exc)


def test_generic_exception_must_not_trip() -> None:
    exc = ValueError("matcher rejected")
    assert not is_store_search_trip_failure(exc)


def test_auth_required_must_not_trip() -> None:
    """AUTH_REQUIRED is an auth-wall error, not a general WAF trip code."""
    exc = RequestError("auth wall", code="AUTH_REQUIRED")
    assert not is_store_search_trip_failure(exc)


def test_search_unsupported_must_not_trip() -> None:
    exc = RequestError("unsupported", code="SEARCH_UNSUPPORTED")
    assert not is_store_search_trip_failure(exc)


# ---------------------------------------------------------------------------
# Basic state machine
# ---------------------------------------------------------------------------


def test_circuit_starts_closed() -> None:
    c = StoreCapabilityCircuit("amazon", "search", threshold=3, cooldown_seconds=60)
    assert c.state == StoreCapabilityState.HEALTHY
    assert c.allow()


def test_circuit_opens_after_threshold() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=3, cooldown_seconds=60)
    c.record_failure(now=now)
    assert c.allow(now=now)  # threshold not reached yet
    c.record_failure(now=now)
    assert c.allow(now=now)
    c.record_failure(now=now)
    assert not c.allow(now=now)
    assert c.state == StoreCapabilityState.UNAVAILABLE


def test_circuit_half_open_after_cooldown() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    c.record_failure(now=now)
    assert c.state == StoreCapabilityState.UNAVAILABLE
    # Before cooldown
    assert c.allow(now=now + timedelta(seconds=30)) is False
    # After cooldown — HALF_OPEN
    future = now + timedelta(seconds=61)
    assert c.allow(now=future) is False
    assert c.state == StoreCapabilityState.DEGRADED


def test_record_success_resets_closed() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    c.record_failure(now=now)
    c.record_success()
    assert c.state == StoreCapabilityState.HEALTHY
    assert c.allow()


# ---------------------------------------------------------------------------
# claim_trial / complete_trial
# ---------------------------------------------------------------------------


def test_claim_trial_only_in_half_open() -> None:
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    now = datetime.now(UTC)
    # CLOSED: no trial
    assert c.claim_trial(now=now) is None
    # OPEN: no trial
    c.record_failure(now=now)
    assert c.claim_trial(now=now) is None


def test_claim_trial_single_flight() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    c.record_failure(now=now)
    future = now + timedelta(seconds=61)
    token1 = c.claim_trial(now=future)
    assert token1 is not None
    # Second caller must get None (another probe in progress)
    token2 = c.claim_trial(now=future)
    assert token2 is None


def test_complete_trial_success_closes_circuit() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    c.record_failure(now=now)
    future = now + timedelta(seconds=61)
    token = c.claim_trial(now=future)
    assert token is not None
    c.complete_trial(token, success=True)
    assert c.state == StoreCapabilityState.HEALTHY
    assert c.allow()


def test_complete_trial_failure_reopens_circuit() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit("amazon", "search", threshold=1, cooldown_seconds=60)
    c.record_failure(now=now)
    future = now + timedelta(seconds=61)
    token = c.claim_trial(now=future)
    assert token is not None
    c.complete_trial(token, success=False, now=future)
    assert c.state == StoreCapabilityState.UNAVAILABLE


def test_expired_trial_auto_reopens() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit(
        "amazon", "search", threshold=1, cooldown_seconds=60, trial_ttl_seconds=10
    )
    c.record_failure(now=now)
    future_half = now + timedelta(seconds=61)
    token = c.claim_trial(now=future_half)
    assert token is not None
    # TTL expired (10s after claim)
    expired = future_half + timedelta(seconds=11)
    assert c.state == StoreCapabilityState.DEGRADED  # before expiry check
    # Trigger reap via allow()
    assert c.allow(now=expired) is False
    # State should be UNAVAILABLE again after reap
    assert c.state == StoreCapabilityState.UNAVAILABLE


def test_stale_token_complete_trial_is_noop() -> None:
    now = datetime.now(UTC)
    c = StoreCapabilityCircuit(
        "amazon", "search", threshold=1, cooldown_seconds=60, trial_ttl_seconds=10
    )
    c.record_failure(now=now)
    future = now + timedelta(seconds=61)
    token = c.claim_trial(now=future)
    # Expire the token
    expired = future + timedelta(seconds=11)
    c.allow(now=expired)  # trigger reap
    # Now complete_trial with the stale token — should be no-op
    c.complete_trial(token, success=True)
    assert c.state == StoreCapabilityState.UNAVAILABLE


# ---------------------------------------------------------------------------
# Search circuit isolation from product_scrape
# ---------------------------------------------------------------------------


def test_search_circuit_open_does_not_affect_product_scrape() -> None:
    """Opening the search circuit MUST NOT affect the product_scrape circuit."""
    now = datetime.now(UTC)
    search_c = get_store_capability_circuit("amazon", "search", threshold=1)
    pdp_c = get_store_capability_circuit("amazon", "product_scrape", threshold=1)

    search_c.record_failure(now=now)
    assert not search_c.allow(now=now)
    # PDP circuit untouched
    assert pdp_c.allow(now=now)


def test_different_stores_are_independent() -> None:
    now = datetime.now(UTC)
    c1 = get_store_capability_circuit("amazon", "search", threshold=1)
    c2 = get_store_capability_circuit("shopee", "search", threshold=1)

    c1.record_failure(now=now)
    assert not c1.allow(now=now)
    assert c2.allow(now=now)


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------


def test_registry_returns_same_instance() -> None:
    a = get_store_capability_circuit("amazon", "search", threshold=3)
    b = get_store_capability_circuit("amazon", "search", threshold=3)
    assert a is b


def test_registry_reset_creates_new_instance() -> None:
    a = get_store_capability_circuit("amazon", "search")
    reset_store_capability_circuits_for_tests()
    b = get_store_capability_circuit("amazon", "search")
    assert a is not b


# ---------------------------------------------------------------------------
# store_capability_unavailable_error helper
# ---------------------------------------------------------------------------


def test_unavailable_error_code() -> None:
    err = store_capability_unavailable_error("amazon", "search", url="https://x.com")
    assert err.code == "STORE_CAPABILITY_UNAVAILABLE"
    assert not err.retryable
    assert err.url == "https://x.com"


# ---------------------------------------------------------------------------
# StoreSearchService wiring
# ---------------------------------------------------------------------------


def _make_search_service(fetcher: MagicMock | None = None) -> object:
    from scout_api.modules.matching.store_search_service import StoreSearchService

    return StoreSearchService(fetcher=fetcher)


def test_search_service_raises_unavailable_when_circuit_open() -> None:
    """When circuit is OPEN, search() must raise STORE_CAPABILITY_UNAVAILABLE."""
    from scout_api.modules.matching.store_search_service import StoreSearchService

    # Pre-open the circuit
    circuit = get_store_capability_circuit("amazon", "search", threshold=1)
    circuit.record_failure()

    # Mock adapter resolution so the service reaches the circuit check
    mock_adapter = MagicMock()
    mock_request = MagicMock()
    mock_request.url = "https://www.amazon.com/s?k=test"
    mock_request.prefer_browser = False
    mock_request.method = "GET"
    mock_adapter.build_search_request.return_value = mock_request

    svc = StoreSearchService(fetcher=MagicMock())
    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=mock_adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
        ) as mock_settings,
    ):
        mock_settings.return_value.store_capability_circuit_enabled = True
        with pytest.raises(RequestError) as exc_info:
            svc.search("amazon", "laptop")
    assert exc_info.value.code == "STORE_CAPABILITY_UNAVAILABLE"


def test_search_service_circuit_disabled_bypasses_check() -> None:
    """When STORE_CAPABILITY_CIRCUIT_ENABLED=false, circuit check must be skipped."""
    from scout_api.modules.matching.store_search_service import StoreSearchService

    # Pre-open the circuit
    circuit = get_store_capability_circuit("amazon", "search", threshold=1)
    circuit.record_failure()

    mock_adapter = MagicMock()
    mock_request = MagicMock()
    mock_request.url = "https://www.amazon.com/s?k=test"
    mock_request.prefer_browser = False
    mock_request.method = "GET"
    mock_adapter.build_search_request.return_value = mock_request
    mock_adapter.parse_candidates.return_value = []
    mock_adapter.classify_empty_result.return_value = "no_results"

    mock_fetcher = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "<html>results</html>"
    mock_response.url = "https://www.amazon.com/s?k=test"
    mock_fetcher.fetch.return_value = mock_response

    svc = StoreSearchService(fetcher=mock_fetcher)
    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=mock_adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
        ) as mock_settings,
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        settings_obj = MagicMock()
        settings_obj.store_capability_circuit_enabled = False
        settings_obj.visaovip_search_action_enabled = False
        mock_settings.return_value = settings_obj
        # Should NOT raise, even though the circuit is OPEN
        result = svc.search("amazon", "laptop")
    assert result == []


def test_search_service_records_failure_on_waf() -> None:
    """WAF error must record a circuit failure."""
    from scout_api.modules.matching.store_search_service import StoreSearchService

    circuit = get_store_capability_circuit("amazon", "search", threshold=5)
    assert circuit.failure_count == 0

    mock_adapter = MagicMock()
    mock_request = MagicMock()
    mock_request.url = "https://www.amazon.com/s?k=test"
    mock_request.prefer_browser = False
    mock_request.method = "GET"
    mock_adapter.build_search_request.return_value = mock_request

    mock_fetcher = MagicMock()
    mock_fetcher.fetch.side_effect = RequestError(
        "WAF blocked", code="UPSTREAM_WAF_BLOCKED"
    )

    svc = StoreSearchService(fetcher=mock_fetcher)
    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=mock_adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
        ) as mock_settings,
    ):
        settings_obj = MagicMock()
        settings_obj.store_capability_circuit_enabled = True
        settings_obj.visaovip_search_action_enabled = False
        mock_settings.return_value = settings_obj
        with pytest.raises(RequestError):
            svc.search("amazon", "laptop")

    assert circuit.failure_count == 1
    assert circuit.allow()  # threshold=5, only 1 failure → still CLOSED


def test_search_service_no_results_must_not_trip_circuit() -> None:
    """Empty result list (NO_RESULTS) MUST NOT trip the circuit."""
    from scout_api.modules.matching.store_search_service import StoreSearchService

    circuit = get_store_capability_circuit("amazon", "search", threshold=3)

    mock_adapter = MagicMock()
    mock_request = MagicMock()
    mock_request.url = "https://www.amazon.com/s?k=test"
    mock_request.prefer_browser = False
    mock_request.method = "GET"
    mock_adapter.build_search_request.return_value = mock_request
    mock_adapter.parse_candidates.return_value = []
    mock_adapter.classify_empty_result.return_value = "no_results"

    mock_fetcher = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "<html>nothing</html>"
    mock_response.url = "https://www.amazon.com/s?k=test"
    mock_fetcher.fetch.return_value = mock_response

    svc = StoreSearchService(fetcher=mock_fetcher)
    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=mock_adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
        ) as mock_settings,
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        settings_obj = MagicMock()
        settings_obj.store_capability_circuit_enabled = True
        settings_obj.visaovip_search_action_enabled = False
        mock_settings.return_value = settings_obj
        result = svc.search("amazon", "xyz-nonexistent")

    assert result == []
    # Circuit must still be healthy — empty results are NOT circuit failures
    assert circuit.failure_count == 0
    assert circuit.state == StoreCapabilityState.HEALTHY
