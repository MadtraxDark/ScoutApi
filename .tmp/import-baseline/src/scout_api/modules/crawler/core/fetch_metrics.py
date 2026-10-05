"""Fetch-cost telemetry (bytes / requests / proxy) — no secrets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    def as_log_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocked_resource_types"] = list(self.blocked_resource_types)
        return payload
