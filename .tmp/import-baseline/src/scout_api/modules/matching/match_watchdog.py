"""Process hang watchdog for the Product Match worker.

Observes real progress only (not lease heartbeat). On confirmed stale hang,
best-effort expires the active lease, then calls
``exit_func(MATCH_WORKER_HANG_EXIT_CODE)`` once — production uses ``os._exit``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from scout_api.modules.matching.match_hang_constants import MATCH_WORKER_HANG_EXIT_CODE
from scout_api.modules.matching.match_progress import MatchProgressTracker

logger = logging.getLogger(__name__)


class MatchHangWatchdog:
    """Daemon-thread-friendly hang detector outside the blocked call stack."""

    def __init__(
        self,
        *,
        tracker: MatchProgressTracker,
        stale_seconds: float,
        check_interval_seconds: float = 5.0,
        enabled: bool = True,
        monotonic: Callable[[], float] | None = None,
        exit_func: Callable[[int], None] | None = None,
        sleep_func: Callable[[float], None] | None = None,
        expire_lease_func: Callable[[str], None] | None = None,
    ) -> None:
        self._tracker = tracker
        self._stale_seconds = max(0.0, float(stale_seconds))
        self._check_interval = max(0.5, float(check_interval_seconds))
        self._enabled = bool(enabled)
        self._monotonic = monotonic or time.monotonic
        self._exit_func = exit_func or (lambda code: os._exit(code))
        self._sleep = sleep_func or time.sleep
        self._expire_lease_func = expire_lease_func or _expire_active_run_lease
        self._stop = threading.Event()
        self._shutdown = False
        self._fired = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def request_shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
        self._stop.set()

    def start(self) -> None:
        if not self._enabled or self._stale_seconds <= 0:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="match-hang-watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "match_run_watchdog_started",
            extra={
                "stale_seconds": self._stale_seconds,
                "check_interval_seconds": self._check_interval,
            },
        )

    def stop(self, *, timeout: float = 2.0) -> None:
        self.request_shutdown()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._check_interval):
            try:
                self.check_once()
            except Exception:  # noqa: BLE001
                logger.exception("match_run_watchdog_check_failed")

    def check_once(self) -> bool:
        """Return True if hard-exit was triggered."""
        with self._lock:
            if self._fired or self._shutdown:
                return False
            if not self._enabled or self._stale_seconds <= 0:
                return False

        snap = self._tracker.snapshot()
        if snap is None:
            return False
        if snap.progress_age_seconds < self._stale_seconds:
            return False

        with self._lock:
            if self._fired or self._shutdown:
                return False
            snap2 = self._tracker.snapshot()
            if snap2 is None:
                return False
            if snap2.progress_age_seconds < self._stale_seconds:
                return False
            self._fired = True
            payload = {
                "run_id": snap2.run_id,
                "worker_id": snap2.worker_id,
                "store": snap2.store,
                "phase": str(snap2.phase),
                "last_progress_age_seconds": round(snap2.progress_age_seconds, 3),
                "run_elapsed_seconds": round(snap2.run_elapsed_seconds, 3),
                "stale_seconds": self._stale_seconds,
                "exit_code": MATCH_WORKER_HANG_EXIT_CODE,
            }
            logger.critical("match_run_watchdog_stale", extra=payload)
            try:
                self._expire_lease_func(snap2.run_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "match_run_watchdog_expire_lease_failed run_id=%s", snap2.run_id
                )
            logger.critical("match_run_watchdog_exit", extra=payload)
            try:
                logging.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._exit_func(MATCH_WORKER_HANG_EXIT_CODE)
            return True
        return False


def _expire_active_run_lease(run_id: str) -> None:
    """Best-effort: expire lease so reclaim can happen immediately after restart."""
    from scout_api.core.database import get_session_factory
    from scout_api.modules.matching.models import ProductMatchRun

    try:
        run_uuid = uuid.UUID(str(run_id))
    except ValueError:
        return
    factory = get_session_factory()
    with factory() as session:
        row = session.get(ProductMatchRun, run_uuid)
        if row is None or (row.status or "").lower() != "running":
            return
        row.claim_expires_at = datetime.now(UTC)
        session.commit()
        logger.info(
            "match_run_watchdog_lease_expired",
            extra={"run_id": str(run_id)},
        )
