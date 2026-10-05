"""Thread-safe real-progress tracker for MatchRun hang detection.

Lease heartbeat is intentionally NOT progress — see ``note_lease_heartbeat``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class MatchProgressPhase(StrEnum):
    """Observable operation phases (not frontend copy / chain-of-thought)."""

    ARM = "arm"
    STARTING_STORE = "starting_store"
    SEARCHING = "searching"
    EVALUATING = "evaluating_candidates"
    FETCHING_CANDIDATE = "fetching_candidate"
    MATCHING = "matching"
    PERSISTING = "persisting_result"
    FINALIZING_STORE = "finalizing_store"
    FINALIZING_RUN = "finalizing_run"


@dataclass(frozen=True, slots=True)
class MatchProgressSnapshot:
    run_id: str
    worker_id: str
    store: str | None
    phase: MatchProgressPhase
    last_progress_monotonic: float
    armed_monotonic: float
    progress_age_seconds: float
    run_elapsed_seconds: float


class MatchProgressTracker:
    """In-memory progress for the single active run on this worker process."""

    def __init__(self, *, monotonic: Callable[[], float] | None = None) -> None:
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._run_id: str | None = None
        self._worker_id: str | None = None
        self._store: str | None = None
        self._phase: MatchProgressPhase | None = None
        self._last_progress_monotonic: float | None = None
        self._armed_monotonic: float | None = None

    def arm(self, *, run_id: str, worker_id: str) -> None:
        now = self._monotonic()
        with self._lock:
            self._run_id = str(run_id)
            self._worker_id = str(worker_id)
            self._store = None
            self._phase = MatchProgressPhase.ARM
            self._last_progress_monotonic = now
            self._armed_monotonic = now

    def disarm(self, *, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is not None and self._run_id is not None:
                if str(run_id) != self._run_id:
                    return
            self._run_id = None
            self._worker_id = None
            self._store = None
            self._phase = None
            self._last_progress_monotonic = None
            self._armed_monotonic = None

    def mark_progress(
        self,
        *,
        run_id: str,
        phase: MatchProgressPhase,
        store: str | None = None,
    ) -> None:
        now = self._monotonic()
        with self._lock:
            if self._run_id is None or str(run_id) != self._run_id:
                return
            self._phase = phase
            if store is not None:
                self._store = store
            self._last_progress_monotonic = now

    def note_lease_heartbeat(self) -> None:
        """Explicit no-op: lease renewal must not reset the stale clock."""
        return None

    def snapshot(self) -> MatchProgressSnapshot | None:
        now = self._monotonic()
        with self._lock:
            if (
                self._run_id is None
                or self._worker_id is None
                or self._phase is None
                or self._last_progress_monotonic is None
                or self._armed_monotonic is None
            ):
                return None
            return MatchProgressSnapshot(
                run_id=self._run_id,
                worker_id=self._worker_id,
                store=self._store,
                phase=self._phase,
                last_progress_monotonic=self._last_progress_monotonic,
                armed_monotonic=self._armed_monotonic,
                progress_age_seconds=max(0.0, now - self._last_progress_monotonic),
                run_elapsed_seconds=max(0.0, now - self._armed_monotonic),
            )


# Process-wide tracker used by the match-run worker + ProductMatchService.
GLOBAL_MATCH_PROGRESS = MatchProgressTracker()
