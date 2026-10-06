"""Tests for BrowserCircuitBreaker half-open single-flight (Task 2 / Phase 2).

State machine under test:
  HEALTHY (CLOSED)     → allow() True
  UNAVAILABLE (OPEN)   → allow() False
  DEGRADED (HALF_OPEN) → allow() False; claim_trial() → exactly ONE token;
                         non-holders → None → fail-fast

Concurrency test: 10 threads claim_trial() → exactly 1 token.
Trial TTL test: expired token → circuit auto-reopens (OPEN again).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from scout_api.modules.crawler.core.browser_health import (
    BrowserCircuitBreaker,
    BrowserHealthState,
    TrialToken,
    reset_browser_circuit_for_tests,
)


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_browser_circuit_for_tests()
    yield
    reset_browser_circuit_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Use a future anchor so that real datetime.now(UTC) is always BEFORE any
# computed cooldown/TTL deadline, avoiding spurious state transitions when
# state/snapshot() are called without an explicit `now` argument.
T0 = datetime(2030, 6, 1, 10, 0, 0, tzinfo=UTC)


def _open_circuit(cb: BrowserCircuitBreaker, *, now: datetime = T0) -> None:
    """Drive circuit to UNAVAILABLE."""
    cb.record_launch_failure(now=now)


def _half_open_circuit(cb: BrowserCircuitBreaker, *, now: datetime = T0) -> datetime:
    """Drive circuit to DEGRADED (half-open) by opening then advancing past cooldown."""
    _open_circuit(cb, now=now)
    half_open_at = now + timedelta(seconds=cb.cooldown_seconds + 1)
    # Touch state to trigger transition.
    cb.allow(now=half_open_at)
    return half_open_at


# ---------------------------------------------------------------------------
# Basic semantics
# ---------------------------------------------------------------------------


def test_closed_allow_true() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    assert cb.allow() is True
    assert cb.state == BrowserHealthState.HEALTHY


def test_open_allow_false() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    _open_circuit(cb)
    assert cb.allow(now=T0) is False
    assert cb.state == BrowserHealthState.UNAVAILABLE


def test_half_open_allow_false() -> None:
    """After cooldown, allow() must still return False (callers need claim_trial)."""
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    half_open_at = _half_open_circuit(cb)
    assert cb.allow(now=half_open_at) is False
    assert cb.state == BrowserHealthState.DEGRADED


def test_claim_trial_returns_none_when_healthy() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    assert cb.claim_trial(now=T0) is None


def test_claim_trial_returns_none_when_open() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    _open_circuit(cb)
    assert cb.claim_trial(now=T0) is None


# ---------------------------------------------------------------------------
# test_success_closes_circuit
# ---------------------------------------------------------------------------


def test_success_closes_circuit() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    half_open_at = _half_open_circuit(cb)

    token = cb.claim_trial(now=half_open_at)
    assert token is not None, "Must receive a TrialToken in HALF_OPEN state"
    assert isinstance(token, TrialToken)

    cb.complete_trial(token, success=True, now=half_open_at)

    assert cb.allow() is True
    assert cb.state == BrowserHealthState.HEALTHY
    snap = cb.snapshot()
    assert snap["active_trial"] is None


# ---------------------------------------------------------------------------
# test_failure_reopens_and_resets_cooldown
# ---------------------------------------------------------------------------


def test_failure_reopens_and_resets_cooldown() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    half_open_at = _half_open_circuit(cb)

    token = cb.claim_trial(now=half_open_at)
    assert token is not None

    # complete_trial with success=False → must reopen with new cooldown
    cb.complete_trial(token, success=False, now=half_open_at)

    assert cb.state == BrowserHealthState.UNAVAILABLE
    assert cb.allow(now=half_open_at) is False

    # New cooldown restarted from half_open_at; original cooldown not yet elapsed.
    still_open_at = half_open_at + timedelta(seconds=29)
    assert cb.allow(now=still_open_at) is False

    # After new cooldown elapses, half-open again.
    new_half_open = half_open_at + timedelta(seconds=31)
    assert cb.allow(now=new_half_open) is False  # allow False even in half-open
    assert cb.state == BrowserHealthState.DEGRADED


# ---------------------------------------------------------------------------
# test_non_probe_fail_fast_does_not_wait
# ---------------------------------------------------------------------------


def test_non_probe_fail_fast_does_not_wait() -> None:
    """In HALF_OPEN, second claim_trial() returns None immediately (no blocking)."""
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    half_open_at = _half_open_circuit(cb)

    # First caller gets the probe token.
    token1 = cb.claim_trial(now=half_open_at)
    assert token1 is not None

    # Second caller must receive None immediately — no waiting.
    token2 = cb.claim_trial(now=half_open_at)
    assert token2 is None

    # Third caller also None.
    token3 = cb.claim_trial(now=half_open_at)
    assert token3 is None

    # Clean up probe.
    cb.complete_trial(token1, success=True)


# ---------------------------------------------------------------------------
# test_probe_crash_does_not_stick_half_open  (fake clock)
# ---------------------------------------------------------------------------


def test_probe_crash_does_not_stick_half_open() -> None:
    """A TrialToken that is never completed must not leave HALF_OPEN forever.

    When the trial TTL expires, snapshot() / claim_trial() / allow() must
    reap the stale token and reopen the circuit (UNAVAILABLE).
    """
    trial_ttl = 60
    cb = BrowserCircuitBreaker(
        threshold=1, cooldown_seconds=30, trial_ttl_seconds=trial_ttl
    )
    half_open_at = _half_open_circuit(cb)

    # Claim probe token but never call complete_trial (simulates crash).
    token = cb.claim_trial(now=half_open_at)
    assert token is not None

    # Before TTL expires: still DEGRADED with active trial.
    _before_expiry = half_open_at + timedelta(seconds=trial_ttl - 1)
    assert cb.state == BrowserHealthState.DEGRADED
    snap = cb.snapshot()
    assert snap["active_trial"] is not None

    # After TTL expires: circuit must auto-reopen to UNAVAILABLE.
    after_expiry = half_open_at + timedelta(seconds=trial_ttl + 1)
    # Trigger reap via allow() with fake clock.
    assert cb.allow(now=after_expiry) is False
    assert cb.state == BrowserHealthState.UNAVAILABLE
    snap2 = cb.snapshot()
    assert snap2["active_trial"] is None

    # complete_trial with expired token is a no-op (already reaped).
    cb.complete_trial(token, success=True, now=after_expiry)
    assert cb.state == BrowserHealthState.UNAVAILABLE


# ---------------------------------------------------------------------------
# test_ten_callers_one_probe  (concurrency)
# ---------------------------------------------------------------------------


def test_ten_callers_one_probe() -> None:
    """10 concurrent threads in HALF_OPEN → exactly 1 receives a TrialToken."""
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)

    # Open circuit then advance past cooldown using a shared fake time.
    cb.record_launch_failure(now=T0)
    half_open_at = T0 + timedelta(seconds=31)

    tokens: list[TrialToken | None] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def _try_claim() -> None:
        barrier.wait()  # All threads start simultaneously.
        tok = cb.claim_trial(now=half_open_at)
        with lock:
            tokens.append(tok)

    threads = [threading.Thread(target=_try_claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    granted = [t for t in tokens if t is not None]
    assert len(granted) == 1, (
        f"Expected exactly 1 probe token; got {len(granted)}: {granted}"
    )
    assert len(tokens) == 10
    assert tokens.count(None) == 9

    # Clean up.
    cb.complete_trial(granted[0], success=True)
    assert cb.state == BrowserHealthState.HEALTHY


# ---------------------------------------------------------------------------
# Snapshot / complete_trial idempotency
# ---------------------------------------------------------------------------


def test_complete_trial_stale_token_is_noop() -> None:
    """Calling complete_trial with a token that has already been reaped is a no-op."""
    trial_ttl = 10
    cb = BrowserCircuitBreaker(
        threshold=1, cooldown_seconds=30, trial_ttl_seconds=trial_ttl
    )
    half_open_at = _half_open_circuit(cb)
    token = cb.claim_trial(now=half_open_at)
    assert token is not None

    # Force reap by advancing past TTL.
    after_expiry = half_open_at + timedelta(seconds=trial_ttl + 1)
    cb.allow(now=after_expiry)  # triggers reap
    assert cb.state == BrowserHealthState.UNAVAILABLE

    # complete_trial with stale token: must be no-op.
    cb.complete_trial(token, success=True, now=after_expiry)
    assert cb.state == BrowserHealthState.UNAVAILABLE  # not re-closed


def test_snapshot_includes_trial_fields() -> None:
    cb = BrowserCircuitBreaker(threshold=1, cooldown_seconds=30)
    snap_healthy = cb.snapshot()
    assert "active_trial" in snap_healthy
    assert "active_trial_expires_at" in snap_healthy
    assert snap_healthy["active_trial"] is None

    half_open_at = _half_open_circuit(cb)
    token = cb.claim_trial(now=half_open_at)
    assert token is not None

    snap_probe = cb.snapshot()
    assert snap_probe["active_trial"] == token.token_id
    assert snap_probe["active_trial_expires_at"] is not None

    cb.complete_trial(token, success=True)


def test_queue_codes_in_infrastructure_error_codes() -> None:
    """Queue infra codes must be in BROWSER_INFRASTRUCTURE_ERROR_CODES."""
    from scout_api.modules.crawler.core.browser_health import (
        BROWSER_INFRASTRUCTURE_ERROR_CODES,
    )

    for code in (
        "BROWSER_QUEUE_SATURATED",
        "BROWSER_QUEUE_TIMEOUT",
        "BROWSER_JOB_CANCELLED",
    ):
        assert code in BROWSER_INFRASTRUCTURE_ERROR_CODES, (
            f"{code} missing from BROWSER_INFRASTRUCTURE_ERROR_CODES"
        )
