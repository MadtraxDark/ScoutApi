from datetime import UTC, datetime

from ..models.crawl_state import CrawlState


class CrawlService:
    """State transition policy, ready to be backed by a repository."""

    def mark_success(
        self, state: CrawlState, changed: bool, available: bool
    ) -> CrawlState:
        state.consecutive_failures = 0
        state.consecutive_unchanged_scrapes = (
            0 if changed else state.consecutive_unchanged_scrapes + 1
        )
        ttl = self.adaptive_ttl(state, changed=changed, available=available)
        state.schedule(ttl)
        if changed:
            state.last_price_change_at = datetime.now(UTC)
        return state

    def mark_failure(self, state: CrawlState) -> CrawlState:
        state.consecutive_failures += 1
        state.schedule(
            min(86400, state.crawl_ttl * (2 ** min(state.consecutive_failures, 5)))
        )
        return state

    @staticmethod
    def adaptive_ttl(state: CrawlState, changed: bool, available: bool) -> int:
        if not available:
            return 86400 if state.consecutive_failures < 3 else 172800
        if changed:
            return max(900, state.crawl_ttl // 2)
        return min(
            86400,
            max(
                900,
                state.crawl_ttl * 2
                if state.consecutive_unchanged_scrapes >= 3
                else state.crawl_ttl,
            ),
        )
