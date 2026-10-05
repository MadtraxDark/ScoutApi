from datetime import UTC, datetime
from heapq import heappop, heappush
from threading import Lock
from uuid import uuid4

from ..models.crawl_state import CrawlState
from .deduplication import DeduplicationStore


class CrawlScheduler:
    """Priority queue with atomic local claims; Redis is the production adapter seam."""

    def __init__(self, dedupe: DeduplicationStore | None = None) -> None:
        self._queue: list[tuple[int, float, str, CrawlState]] = []
        self._dedupe = dedupe or DeduplicationStore()
        self._lock = Lock()

    def enqueue(self, state: CrawlState, url: str) -> bool:
        if not state.due():
            return False
        key = f"{state.store}|{state.product_id}|{state.variant or ''}|{url}"
        if not self._dedupe.claim(key):
            return False
        with self._lock:
            heappush(
                self._queue,
                (
                    -int(state.crawl_priority),
                    datetime.now(UTC).timestamp(),
                    uuid4().hex,
                    state,
                ),
            )
        return True

    def pop(self) -> CrawlState | None:
        with self._lock:
            return heappop(self._queue)[3] if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)
