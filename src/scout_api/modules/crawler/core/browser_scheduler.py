"""BrowserScheduler — bounded FIFO queue for Camoufox browser slots.

Replaces the implicit unbounded serialization of _PlaywrightOwnerLoop with:
- Explicit capacity (default 1)
- Bounded queue with configurable max waiters
- Per-waiter timeout (CAMOUFOX_QUEUE_TIMEOUT_MS)
- FIFO slot assignment
- Cancellation via threading.Event
- Structured metrics: depth, active, wait_ms, timeout_count

Design constraints (Phase 1 / C1):
- capacity=1 is the only validated value; do NOT increase without benchmark.
- slot-{id} profile directories are reserved for future C2+; C1 uses slot-0.
- cancel_event must cause the job to leave the queue and never navigate.
- Queue saturated raises RequestError(code="BROWSER_QUEUE_SATURATED") — not a hang.

Fairness: FIFO.  No priority queue. Budget controls (Match) handle monopolisation.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Re-export so callers can import BrowserSlotLease from this module.
from scout_api.modules.crawler.core.browser_slot import BrowserSlotLease  # noqa: F401
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.failure_domains import FailureDomain, log_failure

if TYPE_CHECKING:
    from scout_api.modules.crawler.core.profile_lock import ProfileLock

logger = logging.getLogger(__name__)

# Match workers can temporarily cap a browser queue wait to their own absolute
# deadline. Context-local state keeps unrelated crawler/browser calls on the
# configured queue timeout and avoids changing global C1 scheduler settings.
_queue_deadline: ContextVar[float | None] = ContextVar(
    "browser_queue_deadline", default=None
)


@contextmanager
def browser_queue_deadline(deadline_monotonic: float | None) -> Iterator[None]:
    """Bound browser queue waiting to an operation's monotonic deadline."""
    token: Token[float | None] = _queue_deadline.set(deadline_monotonic)
    try:
        yield
    finally:
        _queue_deadline.reset(token)


def operation_remaining_seconds() -> float | None:
    """Seconds left on the current Match deadline, or ``None`` when unset.

    Negative values mean the deadline already passed. Callers outside a Match
    store (no context deadline) keep their normal timeouts.
    """
    deadline = _queue_deadline.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()


@dataclass
class _Waiter:
    """Internal queue entry for a blocked acquire() call.

    Invariant: slot_id is set (under self._lock) by _wake_next_unlocked()
    BEFORE event.set() is called. This guarantees that when event.is_set()
    is True, slot_id is already readable by the waiter thread.
    """

    event: threading.Event
    slot_id: int | None = None  # None until pre-granted by _wake_next_unlocked()
    enqueue_time: float = 0.0  # time.monotonic() when the waiter was enqueued


class BrowserScheduler:
    """Bounded FIFO scheduler for Camoufox browser slots.

    Thread-safe. All state mutations are protected by self._lock.

    Slot-ID invariant:
        _active + len(_available_slots) == capacity at all times.
    A slot_id is in exactly one of:
        - active (in use by a caller holding a BrowserSlotLease)
        - _available_slots (free pool)
        - pre-granted to a _Waiter (slot_id set, event not yet consumed)
    A _Waiter is in exactly one of:
        - _waiters deque (not yet granted)
        - removed from deque (pre-granted by release, or cancelled/timed-out)

    Args:
        capacity: Maximum concurrent browser slots (default 1; do not increase
                  without benchmark evidence).
        queue_capacity: Maximum number of callers waiting for a slot.
                        0 means no queuing (immediate failure if all slots busy).
        queue_timeout_ms: Milliseconds a caller waits before queue timeout.
    """

    _POLL_INTERVAL_S: float = 0.05  # 50 ms cancel/timeout resolution

    def __init__(
        self,
        *,
        capacity: int,
        queue_capacity: int,
        queue_timeout_ms: int,
        # Phase 4: cross-process profile ownership lock.
        # When provided, BrowserScheduler acquires the profile lock for a slot's
        # profile directory before returning the BrowserSlotLease to the caller.
        # This prevents two processes (api / monitor / match-runner) from
        # simultaneously opening the same Firefox profile on the shared bind mount.
        profile_lock: ProfileLock | None = None,
        profile_base_path: Path | None = None,
        profile_lock_timeout_ms: int = 30_000,
    ) -> None:
        if capacity < 1:
            raise ValueError("BrowserScheduler capacity must be >= 1")
        self._capacity = capacity
        self._queue_capacity = max(0, queue_capacity)
        self._timeout_ms = max(0, queue_timeout_ms)

        # Profile lock (Phase 4 — cross-process ownership)
        self._profile_lock: ProfileLock | None = profile_lock
        self._profile_base_path: Path | None = profile_base_path
        self._profile_lock_timeout_ms = max(1_000, int(profile_lock_timeout_ms))

        self._lock = threading.Lock()
        self._active = 0
        self._waiters: deque[_Waiter] = deque()
        # Pool of available slot IDs (0 … capacity-1).
        self._available_slots: deque[int] = deque(range(capacity))

        # Metrics (protected by _lock)
        self._queue_timeout_count = 0
        self._total_acquisitions = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _profile_path_for_slot(self, slot_id: int) -> Path | None:
        """Return the profile directory for ``slot_id``, or None if not configured."""
        if self._profile_base_path is None:
            return None
        return self._profile_base_path / f"slot-{slot_id}"

    @property
    def profile_lock_timeout_ms(self) -> int:
        """Configured maximum waiter time, used to bound warm-session idle ownership."""
        return self._profile_lock_timeout_ms

    def _wrap_with_profile_lock(self, slot_id: int) -> BrowserSlotLease:
        """Acquire profile lock for ``slot_id`` and return a BrowserSlotLease.

        Called *after* the scheduler slot has been granted (scheduler lock not held).
        If profile lock acquisition fails, the slot is returned to the pool.
        """
        if self._profile_lock is None:
            return BrowserSlotLease(slot_id=slot_id)

        profile_path = self._profile_path_for_slot(slot_id)
        if profile_path is None:
            return BrowserSlotLease(slot_id=slot_id)

        try:
            profile_lease = self._profile_lock.acquire(
                profile_path,
                timeout_ms=self._profile_lock_timeout_ms,
            )
        except RequestError:
            # Could not acquire profile lock — return slot to pool and re-raise.
            with self._lock:
                self._active -= 1
                self._available_slots.append(slot_id)
                self._wake_next_unlocked()
            raise

        return BrowserSlotLease(slot_id=slot_id, _profile_lock_lease=profile_lease)

    def acquire(
        self,
        *,
        cancel_event: threading.Event | None = None,
    ) -> BrowserSlotLease:
        """Acquire a browser slot.

        Blocks until a slot is available, the queue times out, or
        cancel_event is set. The first available slot is always granted to
        the longest-waiting caller (FIFO).

        When a profile_lock and profile_base_path are configured, also acquires
        the cross-process profile lock for the slot's profile directory before
        returning. This prevents two processes (api / monitor / match-runner)
        from simultaneously opening the same Firefox profile on the shared bind mount.

        Args:
            cancel_event: When set, the caller is removed from the queue
                          immediately and RequestError(BROWSER_JOB_CANCELLED)
                          is raised. The caller will never receive a slot after
                          cancellation (even if one becomes available during the
                          window between cancel and queue-removal).

        Returns:
            BrowserSlotLease with the assigned slot_id. Caller MUST pass this
            to release() exactly once when the browser operation finishes.

        Raises:
            RequestError(BROWSER_QUEUE_SATURATED, retryable=False):
                Queue is full; fails fast without waiting.
            RequestError(BROWSER_QUEUE_TIMEOUT, retryable=True):
                Waited longer than queue_timeout_ms without obtaining a slot.
            RequestError(BROWSER_JOB_CANCELLED, retryable=False):
                cancel_event was set while waiting.
            RequestError(PROFILE_LOCK_TIMEOUT, retryable=True):
                Slot was granted but profile lock could not be acquired within
                profile_lock_timeout_ms. Slot is returned to the pool.
        """
        # --- Fast path: slot available immediately (no queuing) ---
        # NOTE: _wrap_with_profile_lock must NOT be called while self._lock is held
        # because its rollback path re-acquires self._lock (threading.Lock is not
        # reentrant). We capture the slot_id inside the lock and call the profile
        # lock acquisition AFTER releasing self._lock.
        _immediate_slot_id: int | None = None
        depth: int = 0
        operation_deadline = _queue_deadline.get()
        with self._lock:
            # Check under the scheduler lock as well, so an expired Match
            # deadline cannot take the immediate slot or leave a queued waiter.
            if (
                operation_deadline is not None
                and time.monotonic() >= operation_deadline
            ):
                raise RequestError(
                    "Prazo da loja esgotado antes de adquirir o browser.",
                    code="BROWSER_QUEUE_TIMEOUT",
                    retryable=True,
                )
            slot_id = self._try_immediate_unlocked()
            if slot_id is not None:
                self._total_acquisitions += 1
                logger.debug(
                    "browser_slot_acquired_immediate",
                    extra={
                        "slot_id": slot_id,
                        "active": self._active,
                        "capacity": self._capacity,
                    },
                )
                _immediate_slot_id = slot_id
            else:
                # --- Enqueue or fail fast ---
                if len(self._waiters) >= self._queue_capacity:
                    log_failure(
                        FailureDomain.BROWSER_INFRASTRUCTURE,
                        event="browser_queue_saturated",
                        extra={
                            "browser_queue_depth": len(self._waiters),
                            "browser_queue_capacity": self._queue_capacity,
                            "browser_active_jobs": self._active,
                            "browser_capacity": self._capacity,
                        },
                    )
                    raise RequestError(
                        "Fila do browser saturada: todos os slots ocupados e a "
                        "fila de espera atingiu a capacidade máxima.",
                        code="BROWSER_QUEUE_SATURATED",
                        retryable=False,
                    )

                waiter = _Waiter(event=threading.Event(), enqueue_time=time.monotonic())
                self._waiters.append(waiter)
                depth = len(self._waiters)

        # Fast path: self._lock is now released — safe to acquire profile lock.
        if _immediate_slot_id is not None:
            return self._wrap_with_profile_lock(_immediate_slot_id)

        logger.debug(
            "browser_slot_queued",
            extra={
                "depth": depth,
                "queue_capacity": self._queue_capacity,
                "active": self._active,
            },
        )

        # --- Wait loop (no lock held here) ---
        now = time.monotonic()
        timeout_s = self._timeout_ms / 1000.0
        if operation_deadline is not None:
            # The configured queue timeout remains the default. For a Match
            # store, waiting may use its remaining wall budget, but never past
            # that deadline.
            timeout_s = max(timeout_s, operation_deadline - now)
        deadline = now + timeout_s

        while True:
            remaining = deadline - time.monotonic()

            if operation_deadline is not None:
                remaining = min(remaining, operation_deadline - time.monotonic())

            # Check cancel before potentially blocking
            if cancel_event is not None and cancel_event.is_set():
                self._dequeue_on_cancel(waiter)
                wait_ms = round((time.monotonic() - waiter.enqueue_time) * 1000, 1)
                log_failure(
                    FailureDomain.BROWSER_INFRASTRUCTURE,
                    event="browser_job_cancelled",
                    extra={
                        "browser_queue_wait_ms": wait_ms,
                        "browser_capacity": self._capacity,
                    },
                )
                raise RequestError(
                    "Job de browser cancelado enquanto aguardava na fila.",
                    code="BROWSER_JOB_CANCELLED",
                    retryable=False,
                )

            # Past deadline: try to dequeue ourselves
            if remaining <= 0:
                _timeout_count = 0
                _active_snapshot = 0
                _race_slot_id: int | None = None
                _race_wait_ms: float = 0.0
                with self._lock:
                    try:
                        self._waiters.remove(waiter)
                        # Successfully removed — genuine timeout
                        self._queue_timeout_count += 1
                        _timeout_count = self._queue_timeout_count
                        _active_snapshot = self._active
                    except ValueError:
                        # Race: _wake_next_unlocked() dequeued and pre-granted us
                        # just before we checked. slot_id is set (under lock).
                        slot_id = waiter.slot_id
                        assert slot_id is not None  # Invariant: set before event
                        self._total_acquisitions += 1
                        _race_slot_id = slot_id
                        _race_wait_ms = round(
                            (time.monotonic() - waiter.enqueue_time) * 1000, 1
                        )

                # Race win: self._lock released — safe to acquire profile lock.
                if _race_slot_id is not None:
                    if (
                        operation_deadline is not None
                        and time.monotonic() >= operation_deadline
                    ):
                        self.release(BrowserSlotLease(slot_id=_race_slot_id))
                        raise RequestError(
                            "Prazo da loja esgotado aguardando o browser.",
                            code="BROWSER_QUEUE_TIMEOUT",
                            retryable=True,
                        )
                    logger.debug(
                        "browser_slot_acquired_race",
                        extra={
                            "slot_id": _race_slot_id,
                            "browser_queue_wait_ms": _race_wait_ms,
                        },
                    )
                    return self._wrap_with_profile_lock(_race_slot_id)

                wait_ms = round((time.monotonic() - waiter.enqueue_time) * 1000, 1)
                log_failure(
                    FailureDomain.BROWSER_INFRASTRUCTURE,
                    event="browser_queue_timeout",
                    extra={
                        "browser_queue_wait_ms": wait_ms,
                        "browser_queue_timeout_count": _timeout_count,
                        "browser_queue_capacity": self._queue_capacity,
                        "browser_active_jobs": _active_snapshot,
                        "browser_capacity": self._capacity,
                    },
                )
                raise RequestError(
                    f"Timeout aguardando slot do browser "
                    f"({self._timeout_ms} ms esgotado).",
                    code="BROWSER_QUEUE_TIMEOUT",
                    retryable=True,
                )

            # Block for up to POLL_INTERVAL_S or until the event fires
            slice_s = min(self._POLL_INTERVAL_S, remaining)
            if waiter.event.wait(slice_s):
                # Slot was pre-granted by _wake_next_unlocked().
                # slot_id is set (was set before event.set(), both under lock).
                slot_id = waiter.slot_id
                assert slot_id is not None, (
                    "BrowserScheduler invariant violated: "
                    "slot_id must be set before event.set()"
                )
                if (
                    operation_deadline is not None
                    and time.monotonic() >= operation_deadline
                ):
                    self.release(BrowserSlotLease(slot_id=slot_id))
                    raise RequestError(
                        "Prazo da loja esgotado aguardando o browser.",
                        code="BROWSER_QUEUE_TIMEOUT",
                        retryable=True,
                    )
                self._total_acquisitions += 1
                wait_ms = round((time.monotonic() - waiter.enqueue_time) * 1000, 1)
                logger.debug(
                    "browser_slot_acquired_from_queue",
                    extra={
                        "slot_id": slot_id,
                        "active": self._active,
                        "capacity": self._capacity,
                        "browser_queue_wait_ms": wait_ms,
                    },
                )
                # Profile lock acquired OUTSIDE scheduler lock.
                return self._wrap_with_profile_lock(slot_id)

    def release(self, lease: BrowserSlotLease, *, poison: bool = False) -> None:
        """Release a browser slot back to the scheduler.

        Must be called exactly once per successful acquire(), even on error.
        Also releases the cross-process profile lock if one was acquired.

        Args:
            lease: The BrowserSlotLease returned by acquire().
            poison: When True, marks the session as unhealthy (informational
                    in C1; future: triggers browser session recycle).
        """
        # Release profile lock BEFORE returning slot to pool.
        # This ensures the next owner can acquire the profile lock immediately
        # after this release without racing against a profile lock still held.
        if (
            not lease._profile_lock_retained
            and self._profile_lock is not None
            and lease._profile_lock_lease is not None
        ):
            try:
                self._profile_lock.release(lease._profile_lock_lease)
            except Exception:
                logger.warning(
                    "profile_lock_release_error",
                    extra={"slot_id": lease.slot_id},
                    exc_info=True,
                )
            lease._profile_lock_lease = None

        with self._lock:
            self._active -= 1
            self._available_slots.append(lease.slot_id)
            logger.debug(
                "browser_slot_released",
                extra={
                    "slot_id": lease.slot_id,
                    "poison": poison,
                    "active": self._active,
                    "capacity": self._capacity,
                    "queue_depth": len(self._waiters),
                },
            )
            self._wake_next_unlocked()

    def retain_profile_lock(self, lease: BrowserSlotLease) -> bool:
        """Retain profile ownership after releasing the short-lived scheduler slot."""
        if self._profile_lock is None or lease._profile_lock_lease is None:
            return False
        try:
            retained = self._profile_lock.retain(lease._profile_lock_lease)
        except Exception:
            logger.warning(
                "profile_lock_retain_error",
                extra={"slot_id": lease.slot_id},
                exc_info=True,
            )
            return False
        lease._profile_lock_retained = retained
        return retained

    def release_profile_lock(self, lease: BrowserSlotLease) -> None:
        """Release a retained profile lease without changing scheduler capacity."""
        if self._profile_lock is None or lease._profile_lock_lease is None:
            lease._profile_lock_retained = False
            return
        try:
            self._profile_lock.release(lease._profile_lock_lease)
        except Exception:
            logger.warning(
                "profile_lock_release_error",
                extra={"slot_id": lease.slot_id},
                exc_info=True,
            )
        finally:
            lease._profile_lock_lease = None
            lease._profile_lock_retained = False

    def snapshot(self) -> dict[str, object]:
        """Return a point-in-time view of scheduler state for logging/metrics.

        Includes canonical metric keys (browser_queue_*) alongside legacy keys
        so structured logs can be filtered by either naming convention.
        """
        with self._lock:
            depth = len(self._waiters)
            return {
                # Legacy keys (kept for backwards compatibility)
                "depth": depth,
                "capacity": self._capacity,
                "active": self._active,
                "waits": depth,
                "queue_capacity": self._queue_capacity,
                "queue_timeout_ms": self._timeout_ms,
                "queue_timeout_count": self._queue_timeout_count,
                "total_acquisitions": self._total_acquisitions,
                # Canonical metric names (aligned with plan and log events)
                "browser_queue_depth": depth,
                "browser_queue_capacity": self._queue_capacity,
                "browser_active_jobs": self._active,
                "browser_capacity": self._capacity,
                "browser_queue_timeout_count": self._queue_timeout_count,
            }

    # ------------------------------------------------------------------
    # Internal helpers (all assume or acquire self._lock)
    # ------------------------------------------------------------------

    def _try_immediate_unlocked(self) -> int | None:
        """Attempt an immediate slot acquisition. Caller must hold self._lock.

        Returns the granted slot_id, or None if all slots are busy.
        """
        if self._active < self._capacity and self._available_slots:
            self._active += 1
            return self._available_slots.popleft()
        return None

    def _wake_next_unlocked(self) -> None:
        """Signal the next waiting caller (FIFO). Caller must hold self._lock.

        Pre-grants a slot: increments _active, pops a slot_id, and assigns
        it to the waiter BEFORE setting the event. This preserves the
        slot_id invariant (slot_id readable when event fires).
        """
        while self._waiters:
            if not (self._available_slots and self._active < self._capacity):
                break
            w = self._waiters.popleft()
            if w.event.is_set():
                # Stale waiter (already cancelled/timed out but event fired
                # spuriously — defensive guard, should not normally occur).
                continue
            slot_id = self._available_slots.popleft()
            self._active += 1
            # CRITICAL: set slot_id BEFORE event.set() so the waiter thread
            # can read slot_id as soon as event.is_set() returns True.
            w.slot_id = slot_id
            w.event.set()
            return  # Signal exactly one waiter per release (FIFO)

    def _dequeue_on_cancel(self, waiter: _Waiter) -> None:
        """Remove waiter from queue on cancellation.

        If the slot was already pre-granted (race: release ran first), the
        pre-granted slot is returned to the pool so the next waiter can use it.
        """
        with self._lock:
            try:
                self._waiters.remove(waiter)
                # Still in queue: not yet granted. Nothing extra to release.
            except ValueError:
                # Already dequeued by _wake_next_unlocked() (pre-granted).
                # We must release the slot we are not going to consume.
                if waiter.slot_id is not None:
                    self._active -= 1
                    self._available_slots.append(waiter.slot_id)
                    self._wake_next_unlocked()
