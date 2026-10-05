"""Process-wide Camoufox/browser infrastructure health + circuit breaker.

Launch failures (missing binary, profile I/O, Playwright launch timeout) are
structural: once the browser cannot start, repeating the same launch for every
store/query wastes minutes. Page navigation timeouts must NOT open this circuit.

Half-open / single-flight (Phase 2):
  CLOSED   (HEALTHY)   → allow() True; normal operation
  OPEN     (UNAVAILABLE) → allow() False; fail-fast
  cooldown elapsed     → HALF_OPEN (DEGRADED); allow() still False
  claim_trial()        → returns TrialToken for exactly ONE caller; others get None
  non-holders          → fail-fast (do not wait for probe)
  complete_trial(success=True)  → CLOSED
  complete_trial(success=False) → OPEN + new cooldown
  trial TTL expired    → auto-reopen (probe crash/cancel cannot leave HALF_OPEN forever)
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from scout_api.modules.crawler.core.exceptions import RequestError

# Structural browser codes — never map to Product Match NO_MATCH.
# Queue infra codes are browser infra for Match fail-fast purposes.
BROWSER_INFRASTRUCTURE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "BROWSER_LAUNCH_ERROR",
        "BROWSER_INFRASTRUCTURE_UNAVAILABLE",
        "BROWSER_QUEUE_SATURATED",
        "BROWSER_QUEUE_TIMEOUT",
        "BROWSER_JOB_CANCELLED",
    }
)

# Maximum seconds a probe trial is allowed to run before it is considered
# crashed/cancelled and the circuit auto-reopens.
TRIAL_TTL_SECONDS: int = 120


@dataclass
class TrialToken:
    """Opaque probe-permit issued by claim_trial().

    Exactly one token is active at any time in HALF_OPEN state.
    If the holder does not call complete_trial() before ``expires_at``,
    snapshot() / claim_trial() will reap the expired trial and reopen
    the circuit (HALF_OPEN cannot persist forever).
    """

    token_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))

_LAUNCH_FAILURE_MARKERS: tuple[str, ...] = (
    "launch_persistent_context",
    "browsertype.launch",
    "failed to launch",
    "executable doesn't exist",
    "no such file or directory",
    "input/output error",
    "i/o error",
    "camoufox-bin",
)


class BrowserHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class BrowserCircuitBreaker:
    """Shared circuit for structural Camoufox launch failures.

    State machine:
      HEALTHY (CLOSED)     → allow() True
      UNAVAILABLE (OPEN)   → allow() False; fail-fast for all callers
      DEGRADED (HALF_OPEN) → allow() False; claim_trial() grants ONE probe token;
                             all other callers fail-fast without waiting
    """

    def __init__(
        self,
        *,
        threshold: int = 1,
        cooldown_seconds: int = 60,
        trial_ttl_seconds: int = TRIAL_TTL_SECONDS,
    ) -> None:
        self.threshold = max(1, threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._trial_ttl_seconds = max(1, trial_ttl_seconds)
        self._failures = 0
        self._state = BrowserHealthState.HEALTHY
        self._opened_at: datetime | None = None
        self._lock = threading.Lock()
        self.launch_failures = 0
        self.circuit_open_count = 0
        # Active probe token — None when no trial is in progress.
        self._active_trial: TrialToken | None = None

    @property
    def state(self) -> BrowserHealthState:
        with self._lock:
            self._maybe_half_open_unlocked()
            self._reap_expired_trial_unlocked()
            return self._state

    def allow(self, now: datetime | None = None) -> bool:
        """Return True only when circuit is CLOSED (HEALTHY).

        OPEN (UNAVAILABLE) and HALF_OPEN (DEGRADED) both return False.
        Callers that want to probe in HALF_OPEN must call claim_trial() instead.
        """
        with self._lock:
            self._maybe_half_open_unlocked(now)
            self._reap_expired_trial_unlocked(now)
            return self._state == BrowserHealthState.HEALTHY

    def claim_trial(self, *, now: datetime | None = None) -> TrialToken | None:
        """Atomically claim the probe slot in HALF_OPEN (DEGRADED) state.

        Returns a TrialToken for exactly ONE caller; all other concurrent
        callers receive None and must fail-fast (do not wait for the probe).

        Returns None if:
          - circuit is CLOSED or OPEN (not in HALF_OPEN)
          - another probe is already in progress
          - active trial has expired (circuit auto-reopened)
        """
        with self._lock:
            self._maybe_half_open_unlocked(now)
            self._reap_expired_trial_unlocked(now)
            if self._state != BrowserHealthState.DEGRADED:
                return None
            if self._active_trial is not None:
                # Another probe is in progress — fail-fast for this caller.
                return None
            current = now or datetime.now(UTC)
            token = TrialToken(
                token_id=str(uuid.uuid4()),
                expires_at=current + timedelta(seconds=self._trial_ttl_seconds),
            )
            self._active_trial = token
            return token

    def complete_trial(
        self,
        token: TrialToken,
        *,
        success: bool,
        now: datetime | None = None,
    ) -> None:
        """Close or reopen the circuit based on the probe result.

        Must be called exactly once per TrialToken, ideally in a ``finally``
        block so that crash / cancellation are also handled.

        If the token has already been reaped (expired TTL), this is a no-op.
        """
        with self._lock:
            # Guard: only apply if this token is still the active trial.
            if (
                self._active_trial is None
                or self._active_trial.token_id != token.token_id
            ):
                return  # Stale token (expired + reaped) — no-op.
            self._active_trial = None
            if success:
                self._failures = 0
                self._state = BrowserHealthState.HEALTHY
                self._opened_at = None
            else:
                # Reopen with a fresh cooldown.
                if self._state != BrowserHealthState.UNAVAILABLE:
                    self.circuit_open_count += 1
                self._state = BrowserHealthState.UNAVAILABLE
                self._opened_at = now or datetime.now(UTC)

    def record_success(self) -> None:
        """Record a successful operation when circuit is CLOSED (HEALTHY).

        Do not call this when you hold a TrialToken — use complete_trial() instead.
        """
        with self._lock:
            self._failures = 0
            self._state = BrowserHealthState.HEALTHY
            self._opened_at = None

    def record_launch_failure(self, now: datetime | None = None) -> None:
        """Record a structural launch failure when circuit is CLOSED (HEALTHY).

        Do not call this when you hold a TrialToken — use complete_trial() instead.
        """
        with self._lock:
            self.launch_failures += 1
            self._failures += 1
            if self._failures >= self.threshold:
                if self._state != BrowserHealthState.UNAVAILABLE:
                    self.circuit_open_count += 1
                self._state = BrowserHealthState.UNAVAILABLE
                self._opened_at = now or datetime.now(UTC)

    def _maybe_half_open_unlocked(self, now: datetime | None = None) -> None:
        """Transition OPEN → HALF_OPEN when cooldown has elapsed. Caller holds lock."""
        if self._state != BrowserHealthState.UNAVAILABLE or self._opened_at is None:
            return
        current = now or datetime.now(UTC)
        if current >= self._opened_at + timedelta(seconds=self.cooldown_seconds):
            # Probe window: one caller may claim_trial().
            self._state = BrowserHealthState.DEGRADED

    def _reap_expired_trial_unlocked(self, now: datetime | None = None) -> None:
        """Auto-reopen if the active trial's TTL has expired. Caller holds lock.

        This prevents HALF_OPEN from persisting forever when a probe crashes
        or is cancelled without calling complete_trial().
        """
        if self._active_trial is None:
            return
        current = now or datetime.now(UTC)
        if current >= self._active_trial.expires_at:
            self._active_trial = None
            if self._state == BrowserHealthState.DEGRADED:
                # Reopen with a fresh cooldown so the next probe can be attempted
                # after another full cooldown period.
                self._state = BrowserHealthState.UNAVAILABLE
                self._opened_at = current

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._maybe_half_open_unlocked()
            self._reap_expired_trial_unlocked()
            return {
                "state": self._state.value,
                "failures": self._failures,
                "launch_failures": self.launch_failures,
                "circuit_open_count": self.circuit_open_count,
                "opened_at": self._opened_at.isoformat() if self._opened_at else None,
                "active_trial": (
                    self._active_trial.token_id if self._active_trial else None
                ),
                "active_trial_expires_at": (
                    self._active_trial.expires_at.isoformat()
                    if self._active_trial
                    else None
                ),
            }


_breaker: BrowserCircuitBreaker | None = None
_breaker_lock = threading.Lock()


def get_browser_circuit(
    *,
    threshold: int | None = None,
    cooldown_seconds: int | None = None,
) -> BrowserCircuitBreaker:
    """Return the process-wide browser circuit (lazy singleton)."""
    global _breaker
    with _breaker_lock:
        if _breaker is None:
            fail_threshold = threshold
            cooldown = cooldown_seconds
            if fail_threshold is None or cooldown is None:
                try:
                    from scout_api.core.config import get_settings

                    settings = get_settings()
                    if fail_threshold is None:
                        fail_threshold = settings.browser_circuit_failure_threshold
                    if cooldown is None:
                        cooldown = settings.browser_circuit_cooldown_seconds
                except Exception:  # noqa: BLE001
                    fail_threshold = fail_threshold if fail_threshold is not None else 1
                    cooldown = cooldown if cooldown is not None else 60
            _breaker = BrowserCircuitBreaker(
                threshold=fail_threshold,
                cooldown_seconds=cooldown,
            )
        return _breaker


def reset_browser_circuit_for_tests() -> None:
    """Drop the singleton so unit tests start from a clean circuit."""
    global _breaker
    with _breaker_lock:
        _breaker = None


def is_browser_launch_failure(exc: BaseException) -> bool:
    """True when ``exc`` indicates Camoufox/Playwright failed to start."""
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    # Navigation / page timeouts are NOT launch failures.
    if "page.goto" in message or ("waiting for" in message and "navigat" in message):
        return False
    if any(marker in message for marker in _LAUNCH_FAILURE_MARKERS):
        return True
    if "timeouterror" in name and any(
        marker in message for marker in ("launch", "browser", "persistent", "browsertype")
    ):
        return True
    # Playwright launch TimeoutError is often just "Timeout 45000ms exceeded."
    if name == "timeouterror" and "exceeded" in message:
        if "page." in message or "waiting for" in message:
            return False
        return True
    return False


def classify_browser_error(exc: BaseException, *, url: str) -> RequestError:
    """Map a structural browser failure to a RequestError code."""
    if is_browser_launch_failure(exc):
        return RequestError(
            f"Falha ao iniciar o browser (Camoufox/Playwright): {exc}",
            code="BROWSER_LAUNCH_ERROR",
            url=url,
            retryable=False,
        )
    return RequestError(
        f"Infraestrutura de browser indisponível: {exc}",
        code="BROWSER_INFRASTRUCTURE_UNAVAILABLE",
        url=url,
        retryable=False,
    )


def browser_unavailable_error(*, url: str) -> RequestError:
    """Fast-fail when the browser circuit is open."""
    return RequestError(
        "Browser Camoufox indisponível (circuit open após falha estrutural de launch)",
        code="BROWSER_INFRASTRUCTURE_UNAVAILABLE",
        url=url,
        retryable=False,
    )


def is_browser_infrastructure_error(code: str | None) -> bool:
    return bool(code) and code in BROWSER_INFRASTRUCTURE_ERROR_CODES
