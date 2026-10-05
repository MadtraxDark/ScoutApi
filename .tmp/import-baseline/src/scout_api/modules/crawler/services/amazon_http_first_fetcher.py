"""Amazon progressive fetch: HTTP/structured first, then browser, then proxy.

Proxy remains a StoreAware FALLBACK after classified ``UPSTREAM_BLOCKED`` —
never a structural requirement of the Amazon parsers.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from scrapy.http import HtmlResponse

from ..core.exceptions import RequestError
from ..core.proxy_policy import proxy_policy_for_url
from .html_fetcher import (
    HtmlFetcher,
    is_amazon_robot_check,
    is_auth_wall_page,
    is_challenge_page,
)

logger = logging.getLogger(__name__)

_ASIN_IN_URL = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)

_BUYBOX_SIGNAL_SELECTORS = (
    "#ppd .apex-pricetopay-value .a-offscreen",
    "#ppd .priceToPay .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .apex-pricetopay-value .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#desktop_buybox .a-price .a-offscreen",
    "#apex_desktop .apex-pricetopay-value .a-offscreen",
    "#buybox .a-price .a-offscreen",
    "#qualifiedBuybox .a-price .a-offscreen",
    "#desktop_qualifiedBuyBox .a-price .a-offscreen",
)

_OOS_MARKERS = (
    "currently unavailable",
    "out of stock",
    "temporarily out of stock",
    "we don't know when",
    "we do not know when",
    "não disponível",
    "nao disponivel",
    "indisponível",
    "indisponivel",
    "sem estoque",
    "esgotado",
    "não temos previsão",
    "nao temos previsao",
    "atualmente indisponível",
    "atualmente indisponivel",
)

# BR sometimes serves PDP HTML without Buy Box widgets on the first hit.
_EMPTY_BUYBOX_RETRY_SECONDS = 1.25


def is_amazon_store_url(url: str) -> bool:
    """True for Scout-supported Amazon hosts (BR + US only)."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in {"amazon.com", "amazon.com.br"}


def looks_like_amazon_pdp(response: HtmlResponse) -> bool:
    text = response.text or ""
    if is_challenge_page(text) or is_amazon_robot_check(text):
        return False
    if response.css("#productTitle::text").get():
        return True
    asin = response.css(
        "input#ASIN::attr(value), input[name='ASIN']::attr(value)"
    ).get()
    if asin and re.fullmatch(r"[A-Z0-9]{10}", asin.strip(), re.I):
        return True
    return bool(_ASIN_IN_URL.search(response.url or ""))


def looks_like_amazon_search(response: HtmlResponse) -> bool:
    """True when HTML looks like a public SERP with parseable result cards.

    Bare ``/s`` shells without ``data-asin`` cards are incomplete / soft-blocked
    responses — do **not** treat them as successful HTTP SERPs (that produced
    silent empty candidate lists and false NO_MATCH).
    """
    text = response.text or ""
    if is_challenge_page(text) or is_amazon_robot_check(text):
        return False
    if is_auth_wall_page(text, url=str(response.url or "")):
        return False
    return bool(
        response.css(
            "div[data-component-type='s-search-result'][data-asin], "
            "div.s-result-item[data-asin]"
        ).get()
    )


def has_buybox_price_signal(response: HtmlResponse) -> bool:
    """True when Buy Box price widgets (not AOD ingress) expose a digit price."""
    for selector in _BUYBOX_SIGNAL_SELECTORS:
        for raw in response.css(f"{selector}::text").getall():
            text = (raw or "").strip()
            if text and any(ch.isdigit() for ch in text):
                return True
    # JSON-LD Product.offers.price — structured public source.
    for raw in response.css('script[type="application/ld+json"]::text').getall():
        folded = (raw or "").casefold()
        if '"@type"' in folded and "product" in folded and '"price"' in folded:
            if re.search(r'"price"\s*:\s*"?\d', raw or ""):
                return True
    return False


def looks_like_clear_oos(response: HtmlResponse) -> bool:
    texts = [
        t.strip()
        for t in response.css(
            "#availability span::text, #availability ::text, "
            "#outOfStock ::text, #availability_feature_div ::text, "
            "#desktop_buybox #availability ::text"
        ).getall()
        if t and t.strip()
    ]
    avail = " ".join(texts).casefold()
    if not avail:
        return False
    return any(marker in avail for marker in _OOS_MARKERS)


class AmazonHttpFirstHtmlFetcher:
    """Try lightweight HTTP for Amazon; escalate to browser (+ proxy policy)."""

    def __init__(
        self,
        *,
        http: HtmlFetcher,
        browser: HtmlFetcher,
        empty_buybox_retry_seconds: float = _EMPTY_BUYBOX_RETRY_SECONDS,
    ) -> None:
        self._http = http
        self._browser = browser
        self._empty_buybox_retry_seconds = max(0.0, empty_buybox_retry_seconds)

    @property
    def http(self) -> HtmlFetcher:
        return self._http

    @property
    def browser(self) -> HtmlFetcher:
        return self._browser

    def fetch(self, url: str) -> HtmlResponse:
        if not is_amazon_store_url(url):
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
                "amazon_http_failed_fallback_browser",
                extra={"url": url, "code": exc.code},
            )
            return self._browser.fetch(url)

        text = response.text or ""
        if (
            is_challenge_page(text)
            or is_amazon_robot_check(text)
            or is_auth_wall_page(text, url=str(response.url or url))
        ):
            logger.info(
                "amazon_http_challenge_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        # SERP can succeed on HTTP without Buy Box widgets — keep Camoufox off.
        if looks_like_amazon_search(response):
            logger.info("amazon_http_search_accepted", extra={"url": url})
            return self._annotate_http(response, url=url)

        if not looks_like_amazon_pdp(response):
            logger.info(
                "amazon_http_not_pdp_fallback_browser",
                extra={"url": url},
            )
            return self._browser.fetch(url)

        if has_buybox_price_signal(response) or looks_like_clear_oos(response):
            return self._annotate_http(response, url=url)

        # Soft HTTP retry once — BR often omits Buy Box on the first HTML hit.
        if self._empty_buybox_retry_seconds > 0:
            time.sleep(self._empty_buybox_retry_seconds)
            try:
                retry = self._http.fetch(url)
            except RequestError:
                return self._annotate_http(response, url=url)
            retry_text = retry.text or ""
            if not (
                is_challenge_page(retry_text)
                or is_amazon_robot_check(retry_text)
                or is_auth_wall_page(retry_text, url=str(retry.url or url))
            ) and looks_like_amazon_pdp(retry):
                logger.info(
                    "amazon_http_empty_buybox_retry",
                    extra={
                        "url": url,
                        "buybox": has_buybox_price_signal(retry),
                    },
                )
                return self._annotate_http(retry, url=url)

        return self._annotate_http(response, url=url)

    @staticmethod
    def _annotate_http(response: HtmlResponse, *, url: str) -> HtmlResponse:
        metrics: dict[str, Any] = dict(response.meta.get("fetch_metrics") or {})
        metrics["proxy_used"] = False
        metrics["proxy_policy"] = proxy_policy_for_url(url).value
        metrics["fetch_strategy"] = "http-direct"
        metrics["browser_used"] = False
        response.meta["fetch_metrics"] = metrics
        return response
