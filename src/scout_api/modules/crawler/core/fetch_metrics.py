"""Fetch-cost telemetry (bytes / requests / proxy) — no secrets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class FetchCostMetrics:
    store: str | None = None
    canonical_url: str | None = None
    proxy_used: bool = False
    proxy_policy: str | None = None
    fetch_strategy: str | None = None
    cache_hit: bool = False
    warmup_used: bool = False
    get_pc_captured: bool = False
    network_request_count: int = 0
    requests_by_resource_type: dict[str, int] = field(default_factory=dict)
    estimated_transferred_bytes: int = 0
    duration_ms: float = 0.0
    retry_count: int = 0
    result: str = "success"
    blocked_resource_types: tuple[str, ...] = ()
    early_stop: bool = False
    stage_timings_ms: dict[str, float] = field(default_factory=dict)

    def measure[T](self, stage: str, operation: Callable[[], T]) -> T:
        started = perf_counter()
        try:
            return operation()
        finally:
            duration = (perf_counter() - started) * 1000
            self.stage_timings_ms[stage] = round(
                self.stage_timings_ms.get(stage, 0) + duration, 2
            )

    def as_log_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocked_resource_types"] = list(self.blocked_resource_types)
        return payload
