"""Mercado Livre progressive fetch: TLS-impersonated HTTP first, then browser.

``curl_cffi`` spoofs Chrome JA3/HTTP2 fingerprints so plain ``urllib``/``requests``
TLS rejects are avoided. Soft blocks (Snoopy PoW interstitial with HTTP 200) and
hard failures escalate to Camoufox (+ Proxy Cost Mode FALLBACK), which must
resolve the challenge (ADR 0017) before emitting ``UPSTREAM_BLOCKED``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from scrapy.http import HtmlResponse

from ..core.exceptions import RequestError
from ..core.proxy_policy import proxy_policy_for_url
from .html_fetcher import (
    HtmlFetcher,
    is_auth_wall_page,
    is_challenge_page,
    is_mercadolivre_snoopy_challenge,
)

logger = logging.getLogger(__name__)

_CATALOG_ID_IN_URL = re.compile(r"/p/(MLB\d+)(?:[/?]|$)", re.I)


def is_mercadolivre_store_url(url: str) -> bool:
    """True for Scout-supported Mercado Livre BR hosts."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == "mercadolivre.com.br" or host.endswith(".mercadolivre.com.br")


def looks_like_mercadolivre_pdp(response: HtmlResponse) -> bool:
    """True when HTML looks like a product page, not a Snoopy interstitial."""
    text = response.text or ""
    if is_mercadolivre_snoopy_challenge(text) or is_challenge_page(text):
        return False
    if is_auth_wall_page(text, url=str(response.url or "")):
        return False
    if response.css("h1.ui-pdp-title::text, h1.ui-pdp-title").get():
        return True
    if response.css(".ui-pdp-price, [itemprop='price']").get():
        return True
    folded = text.casefold()
    if '"@type"' in folded and "product" in folded and '"price"' in folded:
        return True
    path = urlparse(response.url or "").path or ""
    return bool(_CATALOG_ID_IN_URL.search(path) or "/MLB-" in path.upper())


def looks_like_mercadolivre_search(response: HtmlResponse) -> bool:
    """True when HTML looks like a public SERP (lista), not verification/login."""
    text = response.text or ""
    page_url = str(response.url or "")
    if is_mercadolivre_snoopy_challenge(text) or is_challenge_page(text):
        return False
    if is_auth_wall_page(text, url=page_url):
        return False
    host_path = (urlparse(page_url).netloc + urlparse(page_url).path).casefold()
    if "lista.mercadolivre" not in host_path and "lista.mercadolibre" not in host_path:
        # Some redirects keep result cards on www with /..._Desde_...
        if (
            "ui-search-layout" not in text.casefold()
            and "poly-component__title" not in (text.casefold())
        ):
            return False
    folded = text.casefold()
    if "ui-search" in folded or "poly-component__title" in folded:
        return True
    if "/p/mlb" in folded or "produto.mercadolivre" in folded:
        return True
    return False


def has_mercadolivre_price_signal(response: HtmlResponse) -> bool:
    """True when JSON-LD Offer or primary price widget exposes a digit price."""
    for raw in response.css('script[type="application/ld+json"]::text').getall():
        folded = (raw or "").casefold()
        if "product" in folded and '"price"' in folded:
            if re.search(r'"price"\s*:\s*"?\d', raw or ""):
                return True
    price_meta = response.css(
        ".ui-pdp-price [itemprop='price']::attr(content), "
        "[itemprop='price']::attr(content)"
    ).get()
    if price_meta and any(ch.isdigit() for ch in price_meta):
        return True
    return False


class MercadoLivreHttpFirstHtmlFetcher:
    """Try curl_cffi HTTP for Mercado Livre; escalate to browser (+ proxy policy)."""

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
        if not is_mercadolivre_store_url(url):
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
                "mercadolivre_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        text = response.text or ""
        if (
            is_mercadolivre_snoopy_challenge(text)
            or is_challenge_page(text)
            or is_auth_wall_page(text, url=str(response.url or url))
        ):
            logger.info(
                "mercadolivre_http_challenge_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        # SERP (lista) can succeed on HTTP without PDP price widgets.
        if looks_like_mercadolivre_search(response):
            return self._annotate_http(response, url=url)

        if not looks_like_mercadolivre_pdp(response):
            logger.info(
                "mercadolivre_http_not_pdp_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        if not has_mercadolivre_price_signal(response):
            logger.info(
                "mercadolivre_http_no_price_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        return self._annotate_http(response, url=url)

    @staticmethod
    def _annotate_http(response: HtmlResponse, *, url: str) -> HtmlResponse:
        metrics: dict[str, Any] = dict(response.meta.get("fetch_metrics") or {})
        metrics["proxy_used"] = False
        metrics["proxy_policy"] = proxy_policy_for_url(url).value
        metrics["fetch_strategy"] = "curl-cffi-direct"
        metrics["browser_used"] = False
        response.meta["fetch_metrics"] = metrics
        return response
