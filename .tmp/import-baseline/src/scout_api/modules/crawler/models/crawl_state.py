from datetime import UTC, datetime, timedelta
from enum import IntEnum

from pydantic import BaseModel, Field


class CrawlPriority(IntEnum):
    ARCHIVE = 0
    LOW = 10
    NORMAL = 50
    HIGH = 80
    CRITICAL = 100


class CrawlState(BaseModel):
    store: str
    product_id: str
    variant: str | None = None
    last_scraped_at: datetime | None = None
    next_scrape_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_price_change_at: datetime | None = None
    last_status_change_at: datetime | None = None
    consecutive_unchanged_scrapes: int = 0
    consecutive_failures: int = 0
    crawl_priority: CrawlPriority = CrawlPriority.NORMAL
    crawl_ttl: int = 3600

    def due(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return current >= self.next_scrape_at

    def schedule(self, ttl: int, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        self.crawl_ttl = max(1, ttl)
        self.last_scraped_at = current
        self.next_scrape_at = current + timedelta(seconds=self.crawl_ttl)
