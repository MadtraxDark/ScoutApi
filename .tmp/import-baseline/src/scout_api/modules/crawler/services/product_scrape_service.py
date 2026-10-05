from functools import lru_cache
from typing import Any

from scrapy.http import HtmlResponse

from ....core.config import get_settings
from ..core.cache import ResponseCache, build_cache_backend
from ..core.distributed_cooldown import DistributedCooldown
from ..core.distributed_single_flight import DistributedSingleFlight
from ..core.redis_client import build_redis_gateway
from ..core.scrape_guard import ScrapeGuard
from ..core.scrape_purpose import ScrapePurpose
from ..models.product import (
    ProductPriceItem,
    candidates_from_urls,
    compose_product_price_item,
)
from ..spiders.base import BaseStoreSpider
from ..utils.image_pipeline import ImagePipelineStats
from .html_fetcher import HtmlFetcher, build_html_fetcher
from .store_resolver import resolve_store_spider


@lru_cache
def get_shared_html_fetcher() -> HtmlFetcher:
    settings = get_settings()
    redis_gateway = build_redis_gateway(settings)
    return build_html_fetcher(
        camoufox_enabled=settings.camoufox_enabled,
        user_agent=settings.scraper_user_agent,
        urllib_timeout=settings.scraper_http_timeout,
        camoufox_headless=settings.camoufox_headless,
        camoufox_humanize=settings.camoufox_humanize,
        camoufox_timeout_ms=settings.camoufox_timeout_ms,
        camoufox_launch_timeout_ms=settings.camoufox_launch_timeout_ms,
        camoufox_settle_ms=settings.camoufox_settle_ms,
        camoufox_max_settle_attempts=settings.camoufox_max_settle_attempts,
        camoufox_proxy_url=settings.camoufox_proxy_url,
        camoufox_user_data_dir=settings.camoufox_user_data_dir,
        camoufox_disable_coop=settings.camoufox_disable_coop,
        camoufox_warmup_origin=settings.camoufox_warmup_origin,
        camoufox_warm_reuse=settings.camoufox_warm_reuse,
        camoufox_warm_max_fetches=settings.camoufox_warm_max_fetches,
        shopee_warmup_policy=settings.shopee_warmup_policy,
        shopee_resource_blocking_enabled=settings.shopee_resource_blocking_enabled,
        captcha_solver_enabled=settings.captcha_solver_enabled,
        captcha_solver_provider=settings.captcha_solver_provider,
        captcha_solver_max_attempts=settings.captcha_solver_max_attempts,
        auth_bypass_enabled=settings.auth_bypass_enabled,
        auth_bypass_max_attempts=settings.auth_bypass_max_attempts,
        amazon_auth_email=settings.amazon_auth_email,
        amazon_auth_password=settings.amazon_auth_password,
        shopee_auth_email=settings.shopee_auth_email,
        shopee_auth_password=settings.shopee_auth_password,
        camoufox_browser_scheduler_enabled=settings.camoufox_browser_scheduler_enabled,
        camoufox_browser_capacity=settings.camoufox_browser_capacity,
        camoufox_browser_queue_capacity=settings.camoufox_browser_queue_capacity,
        camoufox_queue_timeout_ms=settings.camoufox_queue_timeout_ms,
        camoufox_profile_lock=settings.camoufox_profile_lock,
        camoufox_profile_lock_ttl_ms=settings.camoufox_profile_lock_ttl_ms,
        camoufox_profile_lock_timeout_ms=settings.camoufox_profile_lock_timeout_ms,
        redis_gateway=redis_gateway,
    )


@lru_cache
def get_shared_scrape_guard() -> ScrapeGuard:
    settings = get_settings()
    gateway = build_redis_gateway(settings)
    cache = ResponseCache(backend=build_cache_backend(redis_gateway=gateway))
    distributed_flight = (
        DistributedSingleFlight(
            gateway,
            lock_ttl_seconds=settings.scrape_single_flight_lock_ttl_seconds,
            wait_seconds=settings.scrape_single_flight_wait_seconds,
        )
        if gateway is not None and settings.scrape_distributed_lock_enabled
        else None
    )
    distributed_cooldown = (
        DistributedCooldown(gateway)
        if gateway is not None and settings.scrape_distributed_cooldown_enabled
        else None
    )
    return ScrapeGuard(
        url_cooldown_seconds=settings.scrape_url_cooldown_seconds,
        domain_min_interval_seconds=settings.scrape_domain_min_interval_seconds,
        result_cache_ttl_seconds=settings.scrape_result_cache_ttl_seconds,
        cache=cache,
        distributed_flight=distributed_flight,
        distributed_cooldown=distributed_cooldown,
        distributed_lock_enabled=bool(
            gateway is not None and settings.scrape_distributed_lock_enabled
        ),
        distributed_cooldown_enabled=bool(
            gateway is not None and settings.scrape_distributed_cooldown_enabled
        ),
    )


def _proxy_used(response: HtmlResponse) -> bool:
    metrics: dict[str, Any] = response.meta.get("fetch_metrics") or {}
    return bool(metrics.get("proxy_used"))


def _merge_image_metadata(
    base: dict[str, Any],
    *,
    stats: ImagePipelineStats | None = None,
    omitted: str | None = None,
) -> dict[str, Any]:
    metadata = dict(base)
    if omitted:
        metadata["images_omitted"] = omitted
        metadata["image_status"] = "omitted"
        metadata["image_error"] = None
    if stats is not None:
        metadata.update(stats.to_metadata())
    return metadata


def extract_images_for_response(
    spider: BaseStoreSpider,
    response: HtmlResponse,
    *,
    include_images: bool,
    proxy_used: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Run gallery extraction with partial-success + observability.

    Preview returns URLs only — never downloads image binaries here.
    Proxy Cost Mode still skips gallery parsing on paid egress.
    """
    if not include_images:
        return [], {}

    if not spider.supports_images:
        stats = ImagePipelineStats(
            store=spider.store, status="omitted", source="store-cost-policy"
        )
        stats.log()
        return [], _merge_image_metadata({}, stats=stats, omitted="store-cost-policy")

    if proxy_used:
        stats = ImagePipelineStats(
            store=spider.store, status="omitted", source="proxy-cost-mode"
        )
        stats.log()
        return [], _merge_image_metadata({}, stats=stats, omitted="proxy-cost-mode")

    try:
        urls = spider.extract_images(response)
        maybe_stats = response.meta.get("image_pipeline")
        if isinstance(maybe_stats, ImagePipelineStats):
            stats = maybe_stats
        else:
            stats = ImagePipelineStats(store=spider.store, source="extract_images")
            stats.raw = len(urls)
            stats.after_filter = len(urls)
            stats.after_dedup = len(urls)
            stats.mark_returned(urls)
            stats.log()
        return urls, _merge_image_metadata({}, stats=stats)
    except Exception as exc:  # noqa: BLE001 — gallery must not fail the product
        stats = ImagePipelineStats(
            store=spider.store,
            status="error",
            error=str(exc)[:300],
            source="extract_images",
        )
        stats.log()
        return [], _merge_image_metadata({}, stats=stats)


class ProductScrapeService:
    """Orchestrate a full product scrape from offer + details extractors."""

    def __init__(
        self,
        fetcher: HtmlFetcher | None = None,
        guard: ScrapeGuard | None = None,
    ) -> None:
        self._fetcher = fetcher or get_shared_html_fetcher()
        self._guard = guard or get_shared_scrape_guard()

    def scrape(
        self,
        url: str,
        *,
        include_images: bool = False,
        purpose: ScrapePurpose = ScrapePurpose.MANUAL_CRAWL,
    ) -> ProductPriceItem:
        cached = self._guard.get_cached(url)
        if cached is not None:
            if not include_images and cached.images:
                return cached.model_copy(
                    update={"images": [], "image_candidates": []}
                )
            if include_images and cached.images:
                return cached
            if not include_images:
                return cached
            # Cached without images but caller wants them: only re-fetch when
            # the store supports gallery extraction.
            spider_probe = self._spider_for(url)
            if not spider_probe.supports_images:
                return cached.model_copy(
                    update={
                        "images": [],
                        "image_candidates": [],
                        "metadata": _merge_image_metadata(
                            cached.metadata,
                            omitted="store-cost-policy",
                            stats=ImagePipelineStats(
                                store=spider_probe.store,
                                status="omitted",
                                source="store-cost-policy",
                            ),
                        ),
                    }
                )

        def _live() -> ProductPriceItem:
            again = self._guard.get_cached(url)
            if again is not None and (not include_images or again.images):
                if not include_images and again.images:
                    return again.model_copy(
                        update={"images": [], "image_candidates": []}
                    )
                return again

            spider = self._spider_for(url)
            fetch_url = spider.prepare_fetch_url(url)
            self._guard.acquire_for_live_fetch(url, purpose=purpose)
            response = self._fetch(fetch_url)
            offer = spider.extract_offer(response)
            details = spider.extract_details(response)
            proxy_used = _proxy_used(response)
            urls, image_meta = extract_images_for_response(
                spider,
                response,
                include_images=include_images,
                proxy_used=proxy_used,
            )
            if include_images:
                details = details.model_copy(
                    update={
                        "images": urls,
                        "image_candidates": candidates_from_urls(urls),
                        "metadata": {**details.metadata, **image_meta},
                    }
                )
            item = compose_product_price_item(offer, details)
            if include_images and image_meta:
                item = item.model_copy(
                    update={"metadata": {**item.metadata, **image_meta}}
                )
            self._guard.store_success(url, item)
            return item

        return self._guard.run_coalesced(url, _live, result_kind="product")

    def _fetch(self, url: str) -> HtmlResponse:
        return self._fetcher.fetch(url)

    @staticmethod
    def _spider_for(url: str) -> BaseStoreSpider:
        return resolve_store_spider(url)
