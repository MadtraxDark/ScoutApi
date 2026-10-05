from scrapy.http import HtmlResponse

from ..core.scrape_guard import ScrapeGuard
from ..core.scrape_purpose import ScrapePurpose
from ..models.product import ProductOffer, compose_product_price_item
from ..spiders.base import BaseStoreSpider
from .html_fetcher import HtmlFetcher
from .product_scrape_service import get_shared_html_fetcher, get_shared_scrape_guard
from .store_resolver import resolve_store_spider


class OfferScrapeService:
    """Fetch a product URL and extract the commercial offer snapshot.

    The live fetch also parses details (CPU-only, same HTML) and stores a full
    ``ProductPriceItem`` in the shared scrape cache so a subsequent Product Match
    reference scrape of the same URL reuses the result instead of hitting
    ``DUPLICATE_REQUEST`` cooldown.
    """

    def __init__(
        self,
        fetcher: HtmlFetcher | None = None,
        guard: ScrapeGuard | None = None,
    ) -> None:
        self._fetcher = fetcher or get_shared_html_fetcher()
        self._guard = guard or get_shared_scrape_guard()

    def scrape_offer(
        self,
        url: str,
        *,
        purpose: ScrapePurpose = ScrapePurpose.OFFER_REFRESH,
    ) -> ProductOffer:
        cached = self._guard.get_cached_offer(url)
        if cached is not None:
            return cached

        def _live() -> ProductOffer:
            again = self._guard.get_cached_offer(url)
            if again is not None:
                return again
            spider = self._spider_for(url)
            self._guard.acquire_for_live_fetch(url, purpose=purpose)
            response = self._fetch(spider.prepare_fetch_url(url))
            offer = spider.extract_offer(response)
            # Same HTML → details are free; populate product cache for Match.
            details = spider.extract_details(response)
            item = compose_product_price_item(offer, details)
            self._guard.store_success(url, item)
            return offer

        return self._guard.run_coalesced(url, _live, result_kind="offer")

    def _fetch(self, url: str) -> HtmlResponse:
        return self._fetcher.fetch(url)

    @staticmethod
    def _spider_for(url: str) -> BaseStoreSpider:
        return resolve_store_spider(url)
