"""Politeness guard: block repeated upstream fetches that burn egress IP reputation."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import TypeVar
from urllib.parse import urlparse

from ..models.product import (
    ProductOffer,
    ProductPriceItem,
    product_offer_from_price_item,
)
from .cache import ResponseCache
from .distributed_cooldown import DistributedCooldown
from .distributed_single_flight import (
    DistributedSingleFlight,
    NullDistributedSingleFlight,
)
from .exceptions import RequestError
from .fingerprints import canonicalize_url
from .redis_keys import scrape_cache_key
from .scrape_purpose import ScrapePurpose
from .single_flight import SingleFlight

T = TypeVar("T")

_DistributedFlight = DistributedSingleFlight | NullDistributedSingleFlight


class ScrapeGuard:
    """URL cooldown, domain spacing, result cache, and single-flight.

    Protects Camoufox/egress IPs from bursty ``POST /crawl`` retries against the
    same product or store host (Cloudflare hard-blocks). Caches both full
    ``ProductPriceItem`` and lightweight ``ProductOffer`` under the same
    canonical key (full item wins when both exist).

    When Redis is configured, cache/locks/cooldowns are shared across processes
    with fail-open fallback to the in-process mechanisms.
    """

    def __init__(
        self,
        *,
        url_cooldown_seconds: int = 300,
        domain_min_interval_seconds: float = 15.0,
        result_cache_ttl_seconds: int = 300,
        cache: ResponseCache | None = None,
        single_flight: SingleFlight | None = None,
        distributed_flight: _DistributedFlight | None = None,
        distributed_cooldown: DistributedCooldown | None = None,
        distributed_lock_enabled: bool = False,
        distributed_cooldown_enabled: bool = False,
    ) -> None:
        self._url_cooldown_seconds = max(0, url_cooldown_seconds)
        self._domain_min_interval_seconds = max(0.0, domain_min_interval_seconds)
        self._result_cache_ttl_seconds = max(0, result_cache_ttl_seconds)
        self._cache = cache or ResponseCache()
        self._single_flight = single_flight or SingleFlight()
        self._distributed_flight: _DistributedFlight = (
            distributed_flight
            if distributed_flight is not None
            else NullDistributedSingleFlight()
        )
        self._distributed_cooldown = distributed_cooldown
        self._distributed_lock_enabled = distributed_lock_enabled
        self._distributed_cooldown_enabled = distributed_cooldown_enabled
        self._url_blocked_until: dict[str, float] = {}
        self._domain_next_ok: dict[str, float] = {}
        self._lock = Lock()

    def get_cached(self, url: str) -> ProductPriceItem | None:
        hit = self._cache.lookup(self._cache_key(url))
        if hit is None:
            return None
        if not isinstance(hit.value, ProductPriceItem):
            return None
        return hit.value.model_copy(
            update={
                "metadata": {
                    **hit.value.metadata,
                    "cache_hit": True,
                    "served_from": hit.served_from,
                }
            }
        )

    def get_cached_offer(self, url: str) -> ProductOffer | None:
        """Return a cached offer, projecting from a full item when available."""
        hit = self._cache.lookup(self._cache_key(url))
        if hit is None:
            return None
        if isinstance(hit.value, ProductOffer):
            return hit.value.model_copy(
                update={
                    "metadata": {
                        **hit.value.metadata,
                        "cache_hit": True,
                        "served_from": hit.served_from,
                    }
                }
            )
        if isinstance(hit.value, ProductPriceItem):
            offer = product_offer_from_price_item(hit.value)
            return offer.model_copy(
                update={
                    "metadata": {
                        **offer.metadata,
                        "cache_hit": True,
                        "served_from": hit.served_from,
                    }
                }
            )
        return None

    def acquire_for_live_fetch(
        self,
        url: str,
        *,
        purpose: ScrapePurpose = ScrapePurpose.UNSPECIFIED,
    ) -> None:
        """Reserve URL/domain for a live fetch or raise ``RequestError``.

        ``purpose`` is recorded for observability; cooldown remains URL-scoped so
        a successful PDP fetch (any purpose) populates the shared result cache and
        subsequent callers must reuse that cache instead of bumping the network.
        """
        del purpose  # reserved for metrics / purpose-scoped policy later
        key = self._cache_key(url)
        host = (urlparse(url).hostname or "").lower()

        if (
            self._distributed_cooldown_enabled
            and self._distributed_cooldown is not None
        ):
            remote = self._distributed_cooldown.try_acquire(
                url,
                host,
                url_cooldown_seconds=self._url_cooldown_seconds,
                domain_min_interval_seconds=self._domain_min_interval_seconds,
            )
            if remote.redis_ok:
                if not remote.allowed:
                    self._raise_cooldown(
                        url,
                        code=remote.code or "DUPLICATE_REQUEST",
                        retry_after=remote.retry_after or 1,
                    )
                # Mirror into local maps so Redis blips still protect this process.
                self._reserve_local(key, host)
                return
            # redis_ok False → fall through to local

        self._acquire_local(url, key, host)

    def store_success(self, url: str, item: ProductPriceItem) -> None:
        self._cache.set(self._cache_key(url), item, self._result_cache_ttl_seconds)

    def store_offer_success(self, url: str, offer: ProductOffer) -> None:
        """Cache an offer without downgrading an existing full product entry."""
        key = self._cache_key(url)
        existing = self._cache.get(key)
        if isinstance(existing, ProductPriceItem):
            return
        self._cache.set(key, offer, self._result_cache_ttl_seconds)

    def run_coalesced(
        self,
        url: str,
        fn: Callable[[], T],
        *,
        result_kind: str = "product",
    ) -> T:
        """Coalesce concurrent live work (local L1 + optional distributed lock).

        ``result_kind`` controls what followers wait for in the shared cache:
        ``"product"`` → ``ProductPriceItem``; ``"offer"`` → ``ProductOffer``
        (including projection from a full item).
        """

        def _with_distributed() -> T:
            if not self._distributed_lock_enabled or isinstance(
                self._distributed_flight, NullDistributedSingleFlight
            ):
                return fn()

            def _poll() -> T | None:
                if result_kind == "offer":
                    offer = self.get_cached_offer(url)
                    return offer if offer is not None else None  # type: ignore[return-value]
                product = self.get_cached(url)
                return product if product is not None else None  # type: ignore[return-value]

            return self._distributed_flight.run(url, fn, poll_result=_poll)

        return self._single_flight.do(self._logical_key(url), _with_distributed)

    @staticmethod
    def _cache_key(url: str) -> str:
        return scrape_cache_key(url)

    @staticmethod
    def _logical_key(url: str) -> str:
        """Stable in-process key (canonical URL) for local single-flight."""
        return canonicalize_url(url)

    def _acquire_local(self, url: str, key: str, host: str) -> None:
        now = time.monotonic()
        with self._lock:
            url_until = self._url_blocked_until.get(key, 0.0)
            if url_until > now:
                retry_after = max(1, int(url_until - now))
                self._raise_cooldown(
                    url, code="DUPLICATE_REQUEST", retry_after=retry_after
                )

            domain_until = self._domain_next_ok.get(host, 0.0)
            if host and domain_until > now:
                retry_after = max(1, int(domain_until - now))
                self._raise_cooldown(url, code="RATE_LIMITED", retry_after=retry_after)

            self._url_blocked_until[key] = now + self._url_cooldown_seconds
            if host:
                self._domain_next_ok[host] = now + self._domain_min_interval_seconds
            self._purge_expired_locked(now)

    def _reserve_local(self, key: str, host: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._url_blocked_until[key] = now + self._url_cooldown_seconds
            if host:
                self._domain_next_ok[host] = now + self._domain_min_interval_seconds
            self._purge_expired_locked(now)

    @staticmethod
    def _raise_cooldown(url: str, *, code: str, retry_after: int) -> None:
        if code == "RATE_LIMITED":
            raise RequestError(
                "Muitas requisições seguidas para a mesma loja; aguarde "
                f"({retry_after}s) para proteger o IP de saída",
                code="RATE_LIMITED",
                url=url,
                retryable=True,
                retry_after=retry_after,
            )
        raise RequestError(
            "Requisição repetida para a mesma URL; aguarde o cooldown "
            f"({retry_after}s) para proteger o IP de saída",
            code="DUPLICATE_REQUEST",
            url=url,
            retryable=True,
            retry_after=retry_after,
        )

    def _purge_expired_locked(self, now: float) -> None:
        expired_urls = [
            k for k, until in self._url_blocked_until.items() if until <= now
        ]
        for key in expired_urls:
            self._url_blocked_until.pop(key, None)
        expired_hosts = [
            host for host, until in self._domain_next_ok.items() if until <= now
        ]
        for host in expired_hosts:
            self._domain_next_ok.pop(host, None)
