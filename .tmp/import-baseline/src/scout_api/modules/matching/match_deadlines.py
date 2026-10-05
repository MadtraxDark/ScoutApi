"""Monotonic deadlines for store / run wall timeouts (Product Match)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MonotonicDeadline:
    """Absolute monotonic deadline. ``timeout_seconds <= 0`` means disabled."""

    deadline_monotonic: float | None
    timeout_seconds: float
    _monotonic: Callable[[], float]

    @classmethod
    def start(
        cls,
        *,
        timeout_seconds: float,
        monotonic: Callable[[], float] | None = None,
    ) -> MonotonicDeadline:
        mono = monotonic or time.monotonic
        timeout = float(timeout_seconds)
        if timeout <= 0:
            return cls(
                deadline_monotonic=None,
                timeout_seconds=0.0,
                _monotonic=mono,
            )
        now = mono()
        return cls(
            deadline_monotonic=now + timeout,
            timeout_seconds=timeout,
            _monotonic=mono,
        )

    @property
    def disabled(self) -> bool:
        return self.deadline_monotonic is None

    def remaining_seconds(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        left = self.deadline_monotonic - self._monotonic()
        return max(0.0, left)

    def expired(self) -> bool:
        if self.deadline_monotonic is None:
            return False
        return self._monotonic() >= self.deadline_monotonic


def capped_timeout_seconds(
    operation_timeout: float,
    *,
    remaining: float | None,
) -> float:
    """Return ``min(operation_timeout, remaining)`` when a budget remains."""
    op = max(0.0, float(operation_timeout))
    if remaining is None:
        return op
    return max(0.0, min(op, float(remaining)))


def nested_deadline(
    outer: MonotonicDeadline,
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float] | None = None,
) -> MonotonicDeadline:
    """Inner deadline capped by outer remaining budget."""
    mono = monotonic or outer._monotonic
    inner = MonotonicDeadline.start(timeout_seconds=timeout_seconds, monotonic=mono)
    if outer.disabled:
        return inner
    if inner.disabled:
        return outer
    assert outer.deadline_monotonic is not None
    assert inner.deadline_monotonic is not None
    return MonotonicDeadline(
        deadline_monotonic=min(outer.deadline_monotonic, inner.deadline_monotonic),
        timeout_seconds=min(outer.timeout_seconds, inner.timeout_seconds),
        _monotonic=mono,
    )
