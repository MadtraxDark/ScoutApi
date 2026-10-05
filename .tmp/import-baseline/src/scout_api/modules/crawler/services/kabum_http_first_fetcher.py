"""KaBuM progressive fetch: urllib HTTP first (Next.js), then browser.

SERP and PDP both embed product state in ``script#__NEXT_DATA__``. When that
payload is present without a challenge interstitial, Camoufox is unnecessary.
Failures escalate to the shared browser stack (StoreAware + Proxy Cost Mode).
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


def is_kabum_store_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "kabum.com.br" or host.endswith(".kabum.com.br")


def looks_like_kabum_search(response: HtmlResponse) -> bool:
    """True when SERP HTML exposes Next.js catalog state or product anchors."""
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    path = (urlparse(page_url).path or "").casefold()
    if "/busca/" not in path and "/busca?" not in (page_url.casefold()):
        return False
    if response.css("script#__NEXT_DATA__::text").get():
        return True
    return bool(response.css("a[href*='/produto/']").get())


def looks_like_kabum_pdp(response: HtmlResponse) -> bool:
    """True when PDP HTML exposes Next.js product state or structured offer."""
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    if response.css("script#__NEXT_DATA__::text").get():
        return True
    folded = text.casefold()
    if '"@type"' in folded and "product" in folded and '"price"' in folded:
        return True
    path = (urlparse(page_url).path or "").casefold()
    return "/produto/" in path and bool(
        response.css("h1::text, [itemprop='name']::text").get()
    )


class KabumHttpFirstHtmlFetcher:
    """Try lightweight HTTP for KaBuM; escalate to browser on challenge/miss."""

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
        if not is_kabum_store_url(url):
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
                "kabum_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        text = response.text or ""
        if is_challenge_page(text) or is_auth_wall_page(
            text, url=str(response.url or url)
        ):
            logger.info(
                "kabum_http_challenge_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        if looks_like_kabum_search(response) or looks_like_kabum_pdp(response):
            logger.info(
                "kabum_http_accepted",
                extra={
                    "url": url,
                    "kind": "search" if looks_like_kabum_search(response) else "pdp",
                },
            )
            return self._annotate_http(response, url=url)

        logger.info(
            "kabum_http_insufficient_fallback_browser",
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
