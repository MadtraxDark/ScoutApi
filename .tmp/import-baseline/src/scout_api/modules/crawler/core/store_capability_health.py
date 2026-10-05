"""Process-wide per-(store_key, capability) circuit breaker.

Tracks upstream failure evidence per (store_key, capability) where capability
∈ {"search", "product_scrape"}.  Keys are fully independent: an OPEN search
circuit does NOT affect product_scrape / PDP paths.

State machine (same semantics as BrowserCircuitBreaker):
  CLOSED   (HEALTHY)     → allow() True; normal operation
  OPEN     (UNAVAILABLE) → allow() False; fail-fast for all callers
    cooldown elapsed     → HALF_OPEN (DEGRADED); allow() still False
  claim_trial()          → returns TrialToken for exactly ONE caller
  non-holders            → fail-fast (do not wait for probe)
  complete_trial(True)   → CLOSED
  complete_trial(False)  → OPEN + new cooldown
  trial TTL expired      → auto-reopen (probe crash cannot leave HALF_OPEN forever)

Failure codes that MAY trip the circuit (upstream blocking evidence):
  UPSTREAM_WAF_BLOCKED, UPSTREAM_BLOCKED, SEARCH_INCOMPLETE_RESPONSE
  + connection-level errors (OSError, ConnectionError)

MUST NOT trip:
  ParseError in isolation (no upstream evidence)
  Empty result / NO_RESULTS
  Matcher rejection
  BROWSER_* infrastructure errors (separate circuit)

Guard flag: STORE_CAPABILITY_CIRCUIT_ENABLED (default true).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError

StoreCapability = Literal["search", "product_scrape"]

# Error codes that carry upstream blocking evidence → trip the circuit.
STORE_SEARCH_TRIP_CODES: frozenset[str] = frozenset(
    {
        "UPSTREAM_WAF_BLOCKED",
        "UPSTREAM_BLOCKED",
        "SEARCH_INCOMPLETE_RESPONSE",
    }
)

# Maximum seconds a probe trial may run before auto-reopen.
CAPABILITY_TRIAL_TTL_SECONDS: int = 90


class StoreCapabilityState(StrEnum):
    HEALTHY = "healthy"          # CLOSED — normal operation
    DEGRADED = "degraded"        # HALF_OPEN — one probe allowed
    UNAVAILABLE = "unavailable"  # OPEN — fail-fast


@dataclass
class CapabilityTrialToken:
    """Opaque probe-permit issued by claim_trial()."""

    token_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class StoreCapabilityCircuit:
    """Per-(store_key, capability) circuit breaker with claim_trial semantics."""

    def __init__(
        self,
        store_key: str,
        capability: str,
        *,
        threshold: int = 3,
        cooldown_seconds: int = 120,
        trial_ttl_seconds: int = CAPABILITY_TRIAL_TTL_SECONDS,
    ) -> None:
        self.store_key = store_key
        self.capability = capability
        self.threshold = max(1, threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._trial_ttl_seconds = max(1, trial_ttl_seconds)
        self._failures = 0
        self._state = StoreCapabilityState.HEALTHY
        self._opened_at: datetime | None = None
        self._lock = threading.Lock()
        # Observability counters (never reset by state transitions).
        self.failure_count = 0
        self.circuit_open_count = 0
        self._active_trial: CapabilityTrialToken | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> StoreCapabilityState:
        with self._lock:
            self._maybe_half_open_unlocked()
            self._reap_expired_trial_unlocked()
            return self._state

    def allow(self, now: datetime | None = None) -> bool:
        """Return True only when circuit is CLOSED (HEALTHY)."""
        with self._lock:
            self._maybe_half_open_unlocked(now)
            self._reap_expired_trial_unlocked(now)
            return self._state == StoreCapabilityState.HEALTHY

    def claim_trial(
        self, *, now: datetime | None = None
    ) -> CapabilityTrialToken | None:
        """Atomically claim the probe slot in HALF_OPEN (DEGRADED) state.

        Returns a CapabilityTrialToken for exactly ONE caller; all other
        concurrent callers receive None and must fail-fast.
        """
        with self._lock:
            self._maybe_half_open_unlocked(now)
            self._reap_expired_trial_unlocked(now)
            if self._state != StoreCapabilityState.DEGRADED:
                return None
            if self._active_trial is not None:
                return None  # Another probe in progress — fail-fast.
            current = now or datetime.now(UTC)
            token = CapabilityTrialToken(
                token_id=str(uuid.uuid4()),
                expires_at=current + timedelta(seconds=self._trial_ttl_seconds),
            )
            self._active_trial = token
            return token

    def complete_trial(
        self,
        token: CapabilityTrialToken,
        *,
        success: bool,
        now: datetime | None = None,
    ) -> None:
        """Close or reopen the circuit based on probe result.

        Must be called exactly once per CapabilityTrialToken (in a finally
        block so crash/cancellation are also handled).
        """
        with self._lock:
            if (
                self._active_trial is None
                or self._active_trial.token_id != token.token_id
            ):
                return  # Stale token (expired + reaped) — no-op.
            self._active_trial = None
            if success:
                self._failures = 0
                self._state = StoreCapabilityState.HEALTHY
                self._opened_at = None
            else:
                if self._state != StoreCapabilityState.UNAVAILABLE:
                    self.circuit_open_count += 1
                self._state = StoreCapabilityState.UNAVAILABLE
                self._opened_at = now or datetime.now(UTC)

    def record_success(self) -> None:
        """Record a successful operation when circuit is CLOSED (HEALTHY)."""
        with self._lock:
            self._failures = 0
            self._state = StoreCapabilityState.HEALTHY
            self._opened_at = None

    def record_failure(self, now: datetime | None = None) -> None:
        """Record an upstream-blocking failure (WAF/connection/incomplete).

        Call only for codes in STORE_SEARCH_TRIP_CODES or connection errors.
        MUST NOT be called for ParseError, empty results, or matcher rejection.
        """
        with self._lock:
            self.failure_count += 1
            self._failures += 1
            if self._failures >= self.threshold:
                if self._state != StoreCapabilityState.UNAVAILABLE:
                    self.circuit_open_count += 1
                self._state = StoreCapabilityState.UNAVAILABLE
                self._opened_at = now or datetime.now(UTC)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._maybe_half_open_unlocked()
            self._reap_expired_trial_unlocked()
            return {
                "store_key": self.store_key,
                "capability": self.capability,
                "state": self._state.value,
                "failures": self._failures,
                "failure_count": self.failure_count,
                "circuit_open_count": self.circuit_open_count,
                "opened_at": (
                    self._opened_at.isoformat() if self._opened_at else None
                ),
                "active_trial": (
                    self._active_trial.token_id if self._active_trial else None
                ),
                "active_trial_expires_at": (
                    self._active_trial.expires_at.isoformat()
                    if self._active_trial
                    else None
                ),
            }

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold lock)
    # ------------------------------------------------------------------

    def _maybe_half_open_unlocked(self, now: datetime | None = None) -> None:
        """Transition OPEN → HALF_OPEN when cooldown has elapsed."""
        if self._state != StoreCapabilityState.UNAVAILABLE or self._opened_at is None:
            return
        current = now or datetime.now(UTC)
        if current >= self._opened_at + timedelta(seconds=self.cooldown_seconds):
            self._state = StoreCapabilityState.DEGRADED

    def _reap_expired_trial_unlocked(self, now: datetime | None = None) -> None:
        """Auto-reopen if the active trial's TTL has expired."""
        if self._active_trial is None:
            return
        current = now or datetime.now(UTC)
        if current >= self._active_trial.expires_at:
            self._active_trial = None
            if self._state == StoreCapabilityState.DEGRADED:
                self._state = StoreCapabilityState.UNAVAILABLE
                self._opened_at = current


# ---------------------------------------------------------------------------
# Failure classification helpers
# ---------------------------------------------------------------------------


def is_store_search_trip_failure(exc: BaseException) -> bool:
    """Return True when *exc* carries upstream blocking evidence for the search circuit.

    MAY trip:   WAF codes, connection reset, SEARCH_INCOMPLETE_RESPONSE.
    MUST NOT:   ParseError (isolated), empty results, matcher rejection,
                BROWSER_* infrastructure errors.
    """
    if isinstance(exc, RequestError):
        return exc.code in STORE_SEARCH_TRIP_CODES
    # ParseError alone is NOT upstream evidence — do not trip the circuit.
    if isinstance(exc, ParseError):
        return False
    # Low-level connection errors (WAF TCP reset, refused connection).
    if isinstance(exc, (OSError, ConnectionError)):
        return True
    # Any other exception class: conservative — do not trip.
    return False


# ---------------------------------------------------------------------------
# Process-local registry (lazy singletons per key)
# ---------------------------------------------------------------------------

_circuits: dict[tuple[str, str], StoreCapabilityCircuit] = {}
_registry_lock = threading.Lock()


def get_store_capability_circuit(
    store_key: str,
    capability: str,
    *,
    threshold: int | None = None,
    cooldown_seconds: int | None = None,
) -> StoreCapabilityCircuit:
    """Return the process-wide circuit for (store_key, capability)."""
    key = (store_key, capability)
    with _registry_lock:
        if key not in _circuits:
            if threshold is None or cooldown_seconds is None:
                try:
                    from scout_api.core.config import get_settings  # noqa: PLC0415

                    settings = get_settings()
                    if threshold is None:
                        threshold = settings.store_capability_circuit_failure_threshold
                    if cooldown_seconds is None:
                        cooldown_seconds = (
                            settings.store_capability_circuit_cooldown_seconds
                        )
                except Exception:  # noqa: BLE001
                    threshold = threshold if threshold is not None else 3
                    cooldown_seconds = (
                        cooldown_seconds if cooldown_seconds is not None else 120
                    )
            _circuits[key] = StoreCapabilityCircuit(
                store_key,
                capability,
                threshold=threshold,
                cooldown_seconds=cooldown_seconds,
            )
        return _circuits[key]


def reset_store_capability_circuits_for_tests() -> None:
    """Drop all singletons so unit tests start from a clean state."""
    global _circuits
    with _registry_lock:
        _circuits = {}


def store_capability_unavailable_error(
    store_key: str,
    capability: str,
    *,
    url: str | None = None,
) -> RequestError:
    """Fast-fail RequestError when the store capability circuit is OPEN."""
    return RequestError(
        f"Capacidade '{capability}' da loja '{store_key}' indisponível (circuit open)",
        code="STORE_CAPABILITY_UNAVAILABLE",
        url=url,
        retryable=False,
    )
