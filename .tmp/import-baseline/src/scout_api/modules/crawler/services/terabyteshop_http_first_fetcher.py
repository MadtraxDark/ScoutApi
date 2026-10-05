"""TerabyteShop progressive fetch: curl_cffi HTTP first, then browser.

Terabyte PDPs are classic SSR HTML (JSON-LD Product + accordion specs +
``#valVista`` pricing). ``curl_cffi`` Chrome TLS usually returns the full PDP
without Camoufox. Challenge / insufficient HTML escalate to the shared browser
stack (StoreAware + Proxy Cost Mode FALLBACK).
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


def is_terabyteshop_store_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "terabyteshop.com.br" or host.endswith(".terabyteshop.com.br")


def looks_like_terabyteshop_pdp(response: HtmlResponse) -> bool:
    """True when HTML exposes Product JSON-LD / pricing DOM without challenge."""
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    path = (urlparse(page_url).path or "").casefold()
    if "/produto/" not in path:
        return False
    folded = text.casefold()
    if '"@type"' in folded and "product" in folded and '"price"' in folded:
        return True
    if 'id="valvista"' in folded or "id='valvista'" in folded:
        return True
    if "especificacoes" in folded and ("<h1" in folded or "panel-body" in folded):
        return True
    return False


def looks_like_terabyteshop_search(response: HtmlResponse) -> bool:
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    path = (urlparse(page_url).path or "").casefold()
    query = (urlparse(page_url).query or "").casefold()
    if "/busca" not in path and "str=" not in query:
        return False
    return bool(response.css("a[href*='/produto/']").get())


class TerabyteShopHttpFirstHtmlFetcher:
    """Try curl_cffi HTTP for TerabyteShop; escalate to browser on challenge/miss."""

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
        if not is_terabyteshop_store_url(url):
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
                "terabyteshop_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        text = response.text or ""
        if is_challenge_page(text) or is_auth_wall_page(
            text, url=str(response.url or url)
        ):
            logger.info(
                "terabyteshop_http_challenge_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        if looks_like_terabyteshop_search(response) or looks_like_terabyteshop_pdp(
            response
        ):
            kind = "search" if looks_like_terabyteshop_search(response) else "pdp"
            logger.info(
                "terabyteshop_http_accepted",
                extra={"url": url, "kind": kind},
            )
            return self._annotate_http(response, url=url)

        logger.info(
            "terabyteshop_http_insufficient_fallback_browser",
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
