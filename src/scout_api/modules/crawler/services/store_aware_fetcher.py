"""Select direct vs proxied Camoufox fetchers using store ProxyPolicy."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from scrapy.http import HtmlResponse

from ..core.exceptions import PROXY_FALLBACK_ERROR_CODES, RequestError
from ..core.proxy_policy import ProxyPolicy, proxy_policy_for_url
from .html_fetcher import CamoufoxHtmlFetcher, HtmlFetcher

logger = logging.getLogger(__name__)

# After a classified direct block, the same host keeps failing direct for a
# while (Magalu Akamai on this egress). Repeating that failure before every
# PDP multiplies browser time past the store wall. The first request is still
# direct; stickiness starts only after proxy fallback actually succeeds.
_DIRECT_BLOCK_STICKY_SECONDS = 300.0
# A proxy browser attempt that cannot finish inside the Match deadline would
# be reported as STORE_WALL_TIMEOUT. Skip it and keep the classified block.
_MIN_PROXY_FALLBACK_SECONDS = 25.0


class _BrowserPostProvider(Protocol):
    def browser_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout_ms: int | None = None,
    ) -> tuple[int, str]: ...


def is_shoppingchina_quick_search(url: str) -> bool:
    """JSON autocomplete/search endpoint — no browser needed (Proxy Cost Mode)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "shoppingchina.com" not in host:
        return False
    return "/quick_search" in (parsed.path or "").lower()


class StoreAwareHtmlFetcher:
    """Route fetches by store policy; never force paid proxy globally."""

    def __init__(
        self,
        *,
        direct: HtmlFetcher,
        proxied: HtmlFetcher | None,
        http: HtmlFetcher | None = None,
    ) -> None:
        self._direct = direct
        self._proxied = proxied
        self._http = http
        self._direct_block_until: dict[str, float] = {}
        self._direct_block_lock = threading.Lock()

    def browser_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout_ms: int | None = None,
    ) -> tuple[int, str]:
        """Delegate Server Action POSTs to the direct Camoufox session."""
        direct = self._direct
        post = getattr(direct, "browser_post", None)
        if post is None:
            raise RequestError(
                "Fetcher direto sem browser_post para Server Action",
                code="BROWSER_INFRASTRUCTURE_UNAVAILABLE",
                url=url,
                retryable=False,
            )
        return cast(Callable[..., tuple[int, str]], post)(
            url, headers=headers, data=data, timeout_ms=timeout_ms
        )

    @property
    def direct(self) -> HtmlFetcher:
        return self._direct

    @property
    def proxied(self) -> HtmlFetcher | None:
        return self._proxied

    def fetch(self, url: str) -> HtmlResponse:
        if is_shoppingchina_quick_search(url) and self._http is not None:
            logger.info("shoppingchina_quick_search_http", extra={"url": url})
            response = self._http.fetch(url)
            return self._annotate(
                response,
                proxy_used=False,
                policy=ProxyPolicy.DIRECT,
            )
        policy = proxy_policy_for_url(url)
        if policy is ProxyPolicy.REQUIRED:
            return self._fetch_required(url, policy)
        if policy is ProxyPolicy.DIRECT:
            response = self._direct.fetch(url)
            return self._annotate(response, proxy_used=False, policy=policy)
        return self._fetch_fallback(url, policy)

    def _fetch_required(self, url: str, policy: ProxyPolicy) -> HtmlResponse:
        if self._proxied is None:
            raise RequestError(
                "Esta loja exige proxy residencial; configure CAMOUFOX_PROXY_URL",
                code="PROXY_REQUIRED",
                url=url,
                retryable=False,
            )
        response = self._proxied.fetch(url)
        return self._annotate(response, proxy_used=True, policy=policy)

    def _fetch_fallback(self, url: str, policy: ProxyPolicy) -> HtmlResponse:
        host = _host_key(url)
        if self._proxied is not None and self._direct_block_active(host):
            logger.info(
                "proxy_skip_direct_after_classified_block",
                extra={"url": url, "proxy_policy": policy.value},
            )
            response = self._proxied.fetch(url)
            return self._annotate(
                response,
                proxy_used=True,
                policy=policy,
                fallback=True,
                sticky=True,
            )
        try:
            response = self._direct.fetch(url)
            self._clear_direct_block(host)
            return self._annotate(response, proxy_used=False, policy=policy)
        except RequestError as exc:
            if exc.code not in PROXY_FALLBACK_ERROR_CODES or self._proxied is None:
                raise
            if not _proxy_fallback_fits_deadline():
                logger.info(
                    "proxy_fallback_skipped_deadline",
                    extra={
                        "url": url,
                        "proxy_policy": policy.value,
                        "code": exc.code,
                    },
                )
                raise
            logger.info(
                "proxy_fallback_after_block",
                extra={
                    "url": url,
                    "proxy_policy": policy.value,
                    "code": exc.code,
                },
            )
            try:
                response = self._proxied.fetch(url)
            except RequestError as proxy_exc:
                # Best Buy Akamai: one sticky-session retry after TCP RST / WAF.
                from .html_fetcher import is_bestbuy_url  # noqa: PLC0415

                if not is_bestbuy_url(url) or proxy_exc.code != "UPSTREAM_BLOCKED":
                    raise
                logger.info(
                    "bestbuy_proxy_retry_after_block",
                    extra={"url": url, "code": proxy_exc.code},
                )
                response = self._proxied.fetch(url)
            self._remember_direct_block(host)
            return self._annotate(
                response,
                proxy_used=True,
                policy=policy,
                fallback=True,
            )

    def _direct_block_active(self, host: str) -> bool:
        if not host:
            return False
        now = time.monotonic()
        with self._direct_block_lock:
            until = self._direct_block_until.get(host, 0.0)
            if until <= now:
                self._direct_block_until.pop(host, None)
                return False
            return True

    def _remember_direct_block(self, host: str) -> None:
        if not host:
            return
        with self._direct_block_lock:
            self._direct_block_until[host] = (
                time.monotonic() + _DIRECT_BLOCK_STICKY_SECONDS
            )

    def _clear_direct_block(self, host: str) -> None:
        if not host:
            return
        with self._direct_block_lock:
            self._direct_block_until.pop(host, None)

    @staticmethod
    def _annotate(
        response: HtmlResponse,
        *,
        proxy_used: bool,
        policy: ProxyPolicy,
        fallback: bool = False,
        sticky: bool = False,
    ) -> HtmlResponse:
        metrics: dict[str, Any] = dict(response.meta.get("fetch_metrics") or {})
        metrics["proxy_used"] = proxy_used
        metrics["proxy_policy"] = policy.value
        if fallback:
            metrics["proxy_fallback"] = True
        if sticky:
            metrics["proxy_sticky"] = True
        response.meta["fetch_metrics"] = metrics
        return response


def _host_key(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _proxy_fallback_fits_deadline() -> bool:
    """False when the Match store deadline cannot fit another browser attempt."""
    from scout_api.modules.crawler.core.browser_scheduler import (  # noqa: PLC0415
        operation_remaining_seconds,
    )

    remaining = operation_remaining_seconds()
    if remaining is None:
        return True
    return remaining >= _MIN_PROXY_FALLBACK_SECONDS


def is_proxied_camoufox(fetcher: HtmlFetcher) -> bool:
    return isinstance(fetcher, CamoufoxHtmlFetcher) and bool(fetcher.proxy_url)


def find_browser_post(fetcher: object) -> Any | None:
    """Walk http-first wrappers to a Camoufox/StoreAware ``browser_post``.

    Shared stack is typically
    ``Kabum → Pichau → ML → Amazon → StoreAware → Camoufox``. Only the
    browser layers implement Server Action POST with CF cookies.
    """
    seen: set[int] = set()
    cur: object | None = fetcher
    # Bound depth: real stack is ~5 wrappers; guards MagicMock auto-attrs.
    for _ in range(16):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        if hasattr(type(cur), "browser_post"):
            return cast(_BrowserPostProvider, cur).browser_post
        nxt = getattr(cur, "_browser", None)
        if nxt is None:
            nxt = getattr(cur, "_direct", None)
        if nxt is cur:
            break
        cur = nxt
    return None
