"""Magazine Luiza: curl_cffi first when the document is already a SERP or PDP.

Akamai sec-cpt stubs are not accepted as documents. A crypto/adaptive payload
is solved on a fresh same-session HTTP impersonation when the server wait fits
the budget. Behavioral challenges, and any body that is still an interstitial,
fall through to Camoufox (same resolver) and then proxy fallback.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from scrapy.http import HtmlResponse

from ..core.exceptions import RequestError
from ..core.proxy_policy import proxy_policy_for_url
from .akamai_sec_cpt import fetch_after_http_crypto_sec_cpt, parse_sec_cpt
from .html_fetcher import HtmlFetcher, is_auth_wall_page, is_challenge_page
from .magalu_readiness import magalu_document_ready

logger = logging.getLogger(__name__)


def is_magalu_store_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "magazineluiza.com.br" or host.endswith(".magazineluiza.com.br")


def looks_like_magalu_search(response: HtmlResponse) -> bool:
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    path = (urlparse(page_url).path or "").casefold()
    if "/busca/" not in path:
        return False
    if response.css("a[data-testid='product-card-link']").get():
        return True
    return bool(response.css("a[href*='/p/']").get())


def looks_like_magalu_pdp(response: HtmlResponse) -> bool:
    text = response.text or ""
    page_url = str(response.url or "")
    if is_challenge_page(text) or is_auth_wall_page(text, url=page_url):
        return False
    return magalu_document_ready(text, page_url)


class MagaluHttpFirstHtmlFetcher:
    """Try lightweight HTTP for Magalu; escalate challenge/miss to the browser."""

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
        if not is_magalu_store_url(url):
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
                "magalu_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        if looks_like_magalu_search(response) or looks_like_magalu_pdp(response):
            logger.info(
                "magalu_http_accepted",
                extra={
                    "url": url,
                    "kind": "search" if looks_like_magalu_search(response) else "pdp",
                },
            )
            return self._annotate_http(response, url=url)

        solved = self._maybe_solve_http_sec_cpt(url, response.text or "")
        if solved is not None and (
            looks_like_magalu_search(solved) or looks_like_magalu_pdp(solved)
        ):
            logger.info("magalu_http_sec_cpt_cleared", extra={"url": url})
            return self._annotate_http(solved, url=url)

        logger.info(
            "magalu_http_insufficient_fallback_browser",
            extra={"url": url},
        )
        return self._browser.fetch(url)

    @staticmethod
    def _maybe_solve_http_sec_cpt(url: str, html: str) -> HtmlResponse | None:
        parsed = parse_sec_cpt(html)
        if parsed is None or not parsed.needs_proof_of_work:
            return None
        logger.info(
            "magalu_http_sec_cpt_crypto",
            extra={"url": url, "provider": parsed.provider},
        )
        return fetch_after_http_crypto_sec_cpt(url)

    @staticmethod
    def _annotate_http(response: HtmlResponse, *, url: str) -> HtmlResponse:
        metrics: dict[str, Any] = dict(response.meta.get("fetch_metrics") or {})
        metrics["proxy_used"] = False
        metrics["proxy_policy"] = proxy_policy_for_url(url).value
        metrics.setdefault("fetch_strategy", "http-direct")
        metrics["browser_used"] = False
        response.meta["fetch_metrics"] = metrics
        return response
