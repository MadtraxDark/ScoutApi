import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def backoff_delay(
    attempt: int, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None
) -> float:
    return (rng or random).uniform(0, min(cap, base * (2**attempt)))


def retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            current = now or datetime.now(UTC)
            return max(0.0, (date - current).total_seconds())
        except (TypeError, ValueError):
            return None
