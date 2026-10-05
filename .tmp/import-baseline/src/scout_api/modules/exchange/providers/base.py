"""Provider protocol for exchange-rate fetchers."""

from __future__ import annotations

from typing import Protocol

from scout_api.modules.exchange.domain import FetchedRate


class ExchangeRateProvider(Protocol):
    """Stateless fetcher; implementations must be thread-safe."""

    @property
    def source_id(self) -> str:
        """Short stable identifier used in observability logs and DB."""
        ...

    def fetch(self) -> list[FetchedRate]:
        """Fetch current rates. Raises on unrecoverable error.

        - Transient network errors (timeout, 5xx) may be retried by caller.
        - Parse errors should propagate so the caller can mark as failed.
        - Never return stale/cached data — each call hits the source.
        """
        ...
