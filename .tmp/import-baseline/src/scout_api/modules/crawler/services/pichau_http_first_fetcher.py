"""Pichau progressive fetch: curl_cffi HTTP first (RSC flight), then browser.

Pichau PDPs embed Magento ``product`` + ``pichau_prices`` inside Next.js
``self.__next_f.push`` flight data. ``curl_cffi`` Chrome TLS usually returns
that SSR HTML without Camoufox. Challenge / insufficient HTML escalate to the
shared browser stack (StoreAware + Proxy Cost Mode FALLBACK).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from scrapy.http import HtmlResponse

from ..core.exceptions import RequestError
from ..core.proxy_policy import proxy_policy_for_url
from .html_fetcher import HtmlFetcher, is_auth_wall_page, is_challenge_page

logger = logging.getLogger(__name__)


def is_pichau_store_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "pichau.com.br" or host.endswith(".pichau.com.br")


def looks_like_pichau_pdp(response: HtmlResponse) -> bool:
    """True when HTML exposes RSC product prices or JSON-LD Product offer."""
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    folded = text.casefold()
    if "pichau_prices" in folded and ("avista" in folded or "final_price" in folded):
        return True
    if '"@type"' in folded and "product" in folded and '"price"' in folded:
        return True
    if "self.__next_f.push" in text and (
        "product" in folded and ("sku" in folded or "special_price" in folded)
    ):
        return True
    return False


def looks_like_pichau_search(response: HtmlResponse) -> bool:
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    path = (urlparse(page_url).path or "").casefold()
    if "/search" not in path:
        return False
    return bool(response.css("a[href]").get())


class PichauHttpFirstHtmlFetcher:
    """Try curl_cffi HTTP for Pichau; escalate to browser on challenge/miss."""

    def __init__(
        self,
        *,
        http: HtmlFetcher,
        browser: HtmlFetcher,
    ) -> None:
        self._http = http
        self._browser = browser

    @property
    def http(self) -> HtmlFetcher:
        return self._http

    @property
    def browser(self) -> HtmlFetcher:
        return self._browser

    def fetch(self, url: str) -> HtmlResponse:
        if not is_pichau_store_url(url):
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
                "pichau_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        text = response.text or ""
        if is_challenge_page(text) or is_auth_wall_page(
            text, url=str(response.url or url)
        ):
            logger.info(
                "pichau_http_challenge_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        if looks_like_pichau_search(response) or looks_like_pichau_pdp(response):
            logger.info(
                "pichau_http_accepted",
                extra={
                    "url": url,
                    "kind": "search" if looks_like_pichau_search(response) else "pdp",
                },
            )
            return self._annotate_http(response, url=url)

        logger.info(
            "pichau_http_insufficient_fallback_browser",
            extra={"url": url},
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
