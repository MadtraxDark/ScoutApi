from datetime import UTC, datetime, timedelta
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: int = 300) -> None:
        self.threshold, self.cooldown = threshold, cooldown
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at: datetime | None = None

    def allow(self, now: datetime | None = None) -> bool:
        if self.state != CircuitState.OPEN:
            return True
        current = now or datetime.now(UTC)
        if self.opened_at and current >= self.opened_at + timedelta(
            seconds=self.cooldown
        ):
            self.state = CircuitState.HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        self.failures, self.state, self.opened_at = 0, CircuitState.CLOSED, None

    def record_failure(self, now: datetime | None = None) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now or datetime.now(UTC)
