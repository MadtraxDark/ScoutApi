"""Schedule helpers for the exchange-rate refresh worker."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta


def compute_next_refresh_at(
    *,
    now: datetime | None = None,
    refresh_interval_seconds: int = 1800,
    jitter_fraction: float = 0.1,
) -> datetime:
    """Compute next_refresh_at = now + interval + small random jitter.

    Jitter is [0, interval * jitter_fraction] to spread refreshes across
    multiple instances if ever deployed in parallel.

    Args:
        now: Override current time (tests).
        refresh_interval_seconds: Base interval (default 30 minutes).
        jitter_fraction: Fraction of interval to use for max jitter.
    """
    base = now or datetime.now(UTC)
    if jitter_fraction <= 0:
        return base + timedelta(seconds=refresh_interval_seconds)
    jitter_max = max(1, int(refresh_interval_seconds * jitter_fraction))
    jitter = random.randint(0, jitter_max)
    return base + timedelta(seconds=refresh_interval_seconds + jitter)


def is_refresh_due(
    next_refresh_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if a refresh is due (next_refresh_at is None or in the past)."""
    if next_refresh_at is None:
        return True
    reference = now or datetime.now(UTC)
    return reference >= next_refresh_at
