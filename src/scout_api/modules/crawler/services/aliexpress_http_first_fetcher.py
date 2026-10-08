"""AliExpress search: curl_cffi SSR first, browser only when that payload is missing.

Wholesale SERPs embed ``itemList`` / ``displayTitle`` in the HTML. A Camoufox
oneshot for that page drops the warm browser and burns the Match store wall
before any candidate is scored. PDP stays on the browser: the product payload
is client-rendered MTop, not this SSR blob.
"""

from __future__ import annotations

import logging
from typing import Any

from scrapy.http import HtmlResponse

from ..core.exceptions import RequestError
from ..core.proxy_policy import proxy_policy_for_url
from .html_fetcher import (
    HtmlFetcher,
    is_aliexpress_block_page,
    is_aliexpress_search_page_url,
    is_aliexpress_url,
    is_auth_wall_page,
    is_challenge_page,
)

logger = logging.getLogger(__name__)


def looks_like_aliexpress_search_document(response: HtmlResponse) -> bool:
    """True when the HTTP body already carries a usable wholesale item list."""
    text = response.text or ""
    page_url = str(response.url or "")
    if not is_aliexpress_search_page_url(page_url):
        return False
    if is_challenge_page(text) or is_aliexpress_block_page(text):
        return False
    if is_auth_wall_page(text, url=page_url):
        return False
    return '"itemList"' in text and '"productId"' in text and '"displayTitle"' in text


class AliExpressHttpFirstHtmlFetcher:
    """Try lightweight HTTP for AliExpress SERP; escalate PDP and blocks to browser."""

    def __init__(self, *, http: HtmlFetcher, browser: HtmlFetcher) -> None:
        self._http = http
        self._browser = browser

    @property
    def http(self) -> HtmlFetcher:
        return self._http

    @property
    def browser(self) -> HtmlFetcher:
        return self._browser

    def fetch(self, url: str) -> HtmlResponse:
        if not is_aliexpress_url(url) or not is_aliexpress_search_page_url(url):
            return self._browser.fetch(url)
        try:
            response = self._http.fetch(url)
        except RequestError as exc:
            if exc.code not in {
                "UPSTREAM_BLOCKED",
                "AUTH_REQUIRED",
                "UPSTREAM_HTTP_ERROR",
                "UPSTREAM_NETWORK_ERROR",
            }:
                raise
            logger.info(
                "aliexpress_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        if looks_like_aliexpress_search_document(response):
            logger.info("aliexpress_http_search_accepted", extra={"url": url})
            return self._annotate_http(response, url=url)

        logger.info(
            "aliexpress_http_search_insufficient_fallback_browser",
            extra={"url": url, "status": response.status},
        )
        return self._browser.fetch(url)

    @staticmethod
    def _annotate_http(response: HtmlResponse, *, url: str) -> HtmlResponse:
        metrics: dict[str, Any] = dict(response.meta.get("fetch_metrics") or {})
        metrics["proxy_used"] = False
        metrics["proxy_policy"] = proxy_policy_for_url(url).value
        metrics["fetch_strategy"] = "http-direct"
        metrics["browser_used"] = False
        response.meta["fetch_metrics"] = metrics
        return response
