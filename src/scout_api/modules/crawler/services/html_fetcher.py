"""HTML fetch strategies for product pages.

Spiders only parse ``HtmlResponse``; fetchers own upstream access and WAF waits.
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import AbstractContextManager
from http.cookiejar import CookieJar
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPCookieProcessor, build_opener
from urllib.request import Request as UrlRequest

from scrapy.http import HtmlResponse, Request

from scout_api.core.performance import OperationCategory, observe

from ..core.exceptions import RequestError, shopee_auth_required_error
from ..core.fetch_metrics import FetchCostMetrics
from ..core.fingerprints import canonicalize_url
from ..core.proxy_policy import proxy_policy_for_url, resolve_store_config


class _BrowserSchedulerOptions(TypedDict):
    enabled: bool
    capacity: int
    queue_capacity: int
    queue_timeout_ms: int
    profile_lock_mode: str
    profile_lock_ttl_ms: int
    profile_lock_timeout_ms: int
    profile_base_path: Path
    redis_gateway: Any | None


logger = logging.getLogger(__name__)

BrowserFactory = Callable[..., AbstractContextManager[Any]]

SHOPEE_BLOCKED_RESOURCE_TYPES: tuple[str, ...] = ("image", "media", "font")
# Alias: any paid-proxy session runs in minimal-traffic mode.
PROXY_COST_BLOCKED_RESOURCE_TYPES: tuple[str, ...] = SHOPEE_BLOCKED_RESOURCE_TYPES
WarmupPolicy = str  # always | once_per_session | never


class _PlaywrightOwnerLoop:
    """Run Playwright sync_api work on one OS thread.

    Playwright's greenlet is thread-affine. Product Match wave-2 may call
    ``fetch`` from a ThreadPoolExecutor; without a dedicated owner thread,
    warm reuse raises ``greenlet.error: Cannot switch to a different thread``.
    """

    def __init__(self, *, name: str = "camoufox-owner") -> None:
        self._queue: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = (
            queue.Queue()
        )
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._started = False
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._thread.start()
            self._started = True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, fut = item
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 — forward to caller
                fut.set_exception(exc)

    def call[T](self, fn: Callable[[], T]) -> T:
        self._ensure_started()
        if threading.current_thread() is self._thread:
            return fn()
        fut: Future[T] = Future()
        self._queue.put((fn, fut))
        return fut.result()

    def shutdown(self, *, timeout: float = 30.0) -> None:
        with self._start_lock:
            if not self._started:
                return
        self._queue.put(None)
        self._thread.join(timeout=timeout)


# Playwright/Firefox resets that typically mean WAF/IP reset, not local bugs.
_CAMOUFOX_UPSTREAM_BLOCK_MARKERS: tuple[str, ...] = (
    "ns_error_net_reset",
    "ns_error_connection_refused",
    "ns_error_net_interrupt",
    "ns_error_net_timeout",
    "ns_error_proxy_connection_refused",
    "err_connection_reset",
    "err_connection_refused",
    "err_proxy_connection_failed",
    "net::err_connection_reset",
    "net::err_connection_refused",
)

# Playwright sync state that makes the warm context unsafe to reuse.
_WARM_SESSION_POISON_MARKERS: tuple[str, ...] = (
    "sync api inside",
    "cannot switch to a different thread",
    "greenlet.error",
    "target closed",
    "browser has been closed",
    "context or browser has been closed",
    "execution context was destroyed",
    "connection closed while reading from the driver",
)


def _should_drop_warm_session(exc: BaseException) -> bool:
    """True when the warm Camoufox context is likely unusable after ``exc``."""
    message = str(exc).casefold()
    return any(marker in message for marker in _WARM_SESSION_POISON_MARKERS)


def classify_camoufox_navigation_error(exc: BaseException, *, url: str) -> RequestError:
    """Map Camoufox/Playwright failures to crawler RequestError codes.

    Structural launch failures become ``BROWSER_LAUNCH_ERROR`` (no proxy
    fallback, no Product Match NO_MATCH). Connection resets / refused from the
    upstream (or its WAF) are ``UPSTREAM_BLOCKED`` so Proxy Cost Mode FALLBACK
    may retry with proxy. Other render failures stay ``UPSTREAM_REQUEST_ERROR``.
    """
    from scout_api.modules.crawler.core.browser_health import (
        classify_browser_error,
        is_browser_launch_failure,
    )

    if is_browser_launch_failure(exc):
        return classify_browser_error(exc, url=url)
    message = str(exc).casefold()
    if any(marker in message for marker in _CAMOUFOX_UPSTREAM_BLOCK_MARKERS):
        return RequestError(
            "A loja resetou ou recusou a conexão (bloqueio de rede / WAF)",
            code="UPSTREAM_BLOCKED",
            url=url,
            retryable=True,
        )
    return RequestError(
        f"Falha ao renderizar a página: {exc}",
        code="UPSTREAM_REQUEST_ERROR",
        url=url,
        retryable=True,
    )


def _is_net_reset_error(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in _CAMOUFOX_UPSTREAM_BLOCK_MARKERS)


def _warmup_origin_key(url: str) -> str:
    warm = warmup_url_for(url)
    target = warm or url
    return (urlparse(target).netloc or "").lower()


# HTTP leg for Amazon only (AmazonHttpFirst). Browser-like UA; not Camoufox spoofing.
_AMAZON_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class HtmlFetcher(Protocol):
    def fetch(self, url: str) -> HtmlResponse:
        """Return a Scrapy response ready for ``parse_product``."""


def is_hard_block_page(html: str, *, title: str | None = None) -> bool:
    """Detect Cloudflare / WAF hard bans (IP block), not solvable JS challenges."""
    title_text = (title or "").strip().lower()
    if "attention required" in title_text:
        return True
    lower = html.lower()
    return "you have been blocked" in lower or "sorry, you have been blocked" in lower


def is_akamai_sec_cpt_page(html: str) -> bool:
    """Akamai Bot Manager behavioral / sec-cpt interstitial (Magalu and peers).

    Markers are interstitial-specific (not ordinary Akamai-fronted PDPs). Size
    gate avoids false positives on large pages that happen to mention Akamai.
    """
    text = html or ""
    if len(text) >= 40_000:
        return False
    lower = text.casefold()
    markers = (
        "sec-if-cpt-container",
        "behavioral-content",
        "scf-akamai-logo",
        'id="sec-bc-tile-parent"',
        "sec-bc-text-container",
    )
    if any(marker in lower for marker in markers):
        return True
    # Sensor bootstrap script alone is not exclusive — keep size-gated.
    if len(text) < 8_000 and re.search(
        r"/[_a-z0-9]+/[_a-z0-9]+/[_a-z0-9]+/.+\?v=[0-9a-f-]{8,}",
        lower,
    ):
        if "sec-if-cpt" in lower or "behavioral" in lower:
            return True
    return False


def is_mercadolivre_snoopy_challenge(html: str) -> bool:
    """Detect Mercado Livre Bot Manager / Snoopy PoW interstitial (often HTTP 200)."""
    lower = (html or "").casefold()
    if not lower:
        return False
    if "verifychallenge" in lower and "continue-button" in lower:
        return True
    if "snoopy-generation" in lower or "snoopy-script" in lower:
        if "continue-button" in lower or "_bmc" in lower or "micro-landing" in lower:
            return True
    if "micro-landing-button" in lower and (
        "verifychallenge" in lower or "_bmstate" in lower
    ):
        return True
    return False


def is_challenge_page(html: str, *, title: str | None = None) -> bool:
    """Detect Cloudflare/Akamai/Amazon/ML interstitials that are not product HTML."""
    if is_hard_block_page(html, title=title):
        return True
    title_text = (title or "").strip().lower()
    if title_text.startswith("loading "):
        return True
    if "just a moment" in title_text or "un momento" in title_text:
        return True
    lower = html.lower()
    if "akamai-bot" in lower and (
        "não é possível acessar" in lower or "nao e possivel acessar" in lower
    ):
        return True
    if is_akamai_sec_cpt_page(html):
        return True
    if "performing security verification" in lower and len(html) < 80_000:
        return True
    if re.search(r"cf-challenge|challenge-platform", lower) and len(html) < 40_000:
        return True
    if is_amazon_robot_check(html, title=title):
        return True
    if is_mercadolivre_snoopy_challenge(html):
        return True
    if is_aliexpress_block_page(html, title=title):
        return True
    return False


def is_amazon_robot_check(html: str, *, title: str | None = None) -> bool:
    """Amazon CAPTCHA / robot-check pages must never be parsed as product offers."""
    title_text = (title or "").strip().casefold()
    lower = (html or "")[:20_000].casefold()
    haystack = f"{title_text}\n{lower}"
    markers = (
        "validatecaptcha",
        "/errors/validatecaptcha",
        "opfcaptcha",
        "robot check",
        "type the characters you see",
        "enter the characters you see",
        "sorry, we just need to make sure you're not a robot",
        "clique na caixa para confirmar",
        "não sou um robô",
        "nao sou um robo",
        "to discuss automated access to amazon data",
    )
    if any(marker in haystack for marker in markers):
        return True
    # Short interstitial often lacks #productTitle / ASIN.
    if "amazon" in haystack and "captcha" in haystack and len(html) < 80_000:
        if 'id="producttitle"' not in lower and 'name="asin"' not in lower:
            return True
    return False


def is_auth_wall_page(
    html: str,
    *,
    url: str | None = None,
    title: str | None = None,
) -> bool:
    """Login / soft-auth / session-gate pages that block a public offer scrape."""
    folded_url = (url or "").casefold()
    title_text = (title or "").strip().casefold()
    lower = (html or "")[:40_000].casefold()

    if "ap/signin" in folded_url or "/ap/signin" in lower:
        return True
    if ("amazon." in folded_url or "amazon." in lower[:2_000]) and (
        "sign in" in title_text
        or "sign-in" in title_text
        or "fazer login" in title_text
        or "iniciar sessão" in title_text
        or "iniciar sesion" in title_text
    ):
        if 'id="producttitle"' not in lower and 'name="asin"' not in lower:
            return True

    if "shopee." in folded_url or "shopee." in lower[:2_000]:
        if "/verify/traffic" in folded_url or "verify/traff" in lower:
            return True
        if "/buyer/login" in folded_url:
            return True
        if "login" in folded_url and "next=" in folded_url:
            return True
        if "buyer/login" in lower and "next=" in (folded_url + lower[:4_000]):
            if '"item_id"' not in lower and '"itemid"' not in lower:
                return True

    # Mercado Livre / Mercado Libre account-verification soft-auth gate.
    if "mercadolivre." in folded_url or "mercadolibre." in folded_url:
        if "account-verification" in folded_url:
            return True
        if (
            "/gz/account-verification" in lower
            or "account-verification" in lower[:8_000]
        ):
            # Verification interstitial without SERP/PDP product markup.
            if "/p/mlb" not in lower and "ui-search-layout" not in lower:
                return True

    return False


def needs_interstitial_resolution(
    html: str,
    *,
    url: str | None = None,
    title: str | None = None,
) -> bool:
    """True when challenge/auth wall must be cleared before treating HTML as PDP."""
    if is_hard_block_page(html, title=title):
        return False
    return is_challenge_page(html, title=title) or is_auth_wall_page(
        html, url=url, title=title
    )


def locale_for_url(url: str) -> str | None:
    """Prefer store-local locale so Intl/fingerprint match the target site."""
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if hostname == "nissei.com" or hostname.endswith(".nissei.com"):
        return "es-PY"
    if hostname == "magazineluiza.com.br" or hostname.endswith(".magazineluiza.com.br"):
        return "pt-BR"
    if hostname == "shopee.com.br" or hostname.endswith(".shopee.com.br"):
        return "pt-BR"
    if is_aliexpress_url(f"https://{hostname}/"):
        return "pt-BR"
    if hostname == "amazon.com.br" or hostname.endswith(".amazon.com.br"):
        return "pt-BR"
    if hostname == "amazon.com" or hostname.endswith(".amazon.com"):
        return "en-US"
    if hostname == "bestbuy.com" or hostname.endswith(".bestbuy.com"):
        return "en-US"
    if hostname == "mercadolivre.com.br" or hostname.endswith(".mercadolivre.com.br"):
        return "pt-BR"
    # Keep BR e-commerce on one Camoufox fingerprint (warm reuse / Match waves).
    if hostname.endswith(".com.br") or hostname in {
        "kabum.com.br",
        "pichau.com.br",
        "terabyteshop.com.br",
        "shoppingchina.com.br",
        "visaovip.com.br",
    }:
        return "pt-BR"
    if "shoppingchina" in hostname:
        return "pt-BR"
    return None


def accept_language_for_url(url: str) -> str:
    """HTTP Accept-Language aligned with ``locale_for_url`` (no spoofed geo)."""
    locale = locale_for_url(url)
    if locale == "pt-BR":
        return "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    if locale == "es-PY":
        return "es-PY,es;q=0.9,en;q=0.8"
    if locale == "en-US":
        return "en-US,en;q=0.9"
    return "en-US,en;q=0.9"


def marketplace_referer_for_url(url: str) -> str | None:
    """Same-marketplace Referer for Amazon / Best Buy PDP session continuity."""
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if hostname == "amazon.com.br" or hostname.endswith(".amazon.com.br"):
        return "https://www.amazon.com.br/"
    if hostname == "amazon.com" or hostname.endswith(".amazon.com"):
        return "https://www.amazon.com/"
    if hostname == "bestbuy.com" or hostname.endswith(".bestbuy.com"):
        return "https://www.bestbuy.com/"
    return None


def warmup_url_for(url: str) -> str | None:
    """Origin warm-up to mint Cloudflare / Akamai cookies before the PDP."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname or not parsed.scheme:
        return None
    if hostname == "nissei.com" or hostname.endswith(".nissei.com"):
        return f"{parsed.scheme}://{parsed.netloc}/py/"
    if hostname == "shopee.com.br" or hostname.endswith(".shopee.com.br"):
        return f"{parsed.scheme}://{parsed.netloc}/"
    if is_aliexpress_url(url):
        return f"{parsed.scheme}://{parsed.netloc}/"
    if hostname == "magazineluiza.com.br" or hostname.endswith(".magazineluiza.com.br"):
        # Mint Akamai/_abck cookies on the origin before the PDP (ADR 0017).
        return f"{parsed.scheme}://{parsed.netloc}/"
    if hostname == "bestbuy.com" or hostname.endswith(".bestbuy.com"):
        # Akamai Bot Manager: cold PDP navigations often get TCP RST / sec-cpt.
        # Homepage first lets ``bmak`` mint ``_abck`` / ``bm_sz`` on the same IP.
        return f"{parsed.scheme}://{parsed.netloc}/"
    return None


_SHOPEE_ITEM_PATH = re.compile(
    r"[.-]i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)",
    re.IGNORECASE,
)


def shopee_ids_from_url(url: str) -> tuple[str, str] | None:
    """Extract shop_id / item_id from a Shopee product URL path."""
    path = urlparse(url).path or ""
    match = _SHOPEE_ITEM_PATH.search(path)
    if not match:
        return None
    return match.group("shop_id"), match.group("item_id")


def looks_like_shopee_pdp(html: str) -> bool:
    """True when HTML/JSON already carries a Shopee item payload."""
    head = (html or "")[:20_000]
    return '"item"' in head and (
        '"item_id"' in head or '"itemid"' in head or '"models"' in head
    )


def is_shopee_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname == "shopee.com.br" or hostname.endswith(".shopee.com.br")


def is_shopee_traffic_block(url: str, html: str = "") -> bool:
    """Shopee anti-fraud interstitial (verify/traffic), not a product page."""
    folded_url = (url or "").casefold()
    folded_html = (html or "")[:8_000].casefold()
    return "/verify/traffic" in folded_url or "verify/traff" in folded_html


def wrap_shopee_pdp_json(raw: str) -> str:
    """Embed a captured get_pc JSON body so spiders parse a normal HtmlResponse."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>Shopee PDP</title></head><body>"
        f'<script type="application/json" data-shopee-pdp="1">{raw}</script>'
        "</body></html>"
    )


def wrap_shopee_search_json(raw: str) -> str:
    """Embed a captured search_items JSON body for SERP parsing."""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>Shopee Search</title></head><body>"
        f'<script type="application/json" data-shopee-search="1">{raw}</script>'
        "</body></html>"
    )


def is_shopee_get_pc_url(url: str) -> bool:
    path = (urlparse(url).path or "").casefold()
    return "/api/v4/pdp/get_pc" in path or path.endswith("/api/v4/pdp/get")


def is_shopee_search_page_url(url: str) -> bool:
    """True for Shopee HTML search pages (not the JSON API itself)."""
    if not is_shopee_url(url):
        return False
    path = (urlparse(url).path or "").casefold().rstrip("/")
    return path == "/search" or path.startswith("/search/")


def is_shopee_search_api_url(url: str) -> bool:
    path = (urlparse(url).path or "").casefold()
    return "/api/v4/search/search_items" in path


def looks_like_shopee_search_payload(raw: str) -> bool:
    sample = (raw or "")[:4_000].casefold()
    if '"error"' in sample[:300] and "90309999" in sample[:500]:
        return False
    return '"items"' in sample or '"item_basic"' in sample or '"itemid"' in sample


_ALIEXPRESS_ITEM_PATH = re.compile(
    r"/item/(?:[^/]+/)?(?P<item_id>\d{6,})\.html",
    re.IGNORECASE,
)


def is_aliexpress_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname == "aliexpress.com" or hostname.endswith(".aliexpress.com")


def aliexpress_item_id_from_url(url: str) -> str | None:
    match = _ALIEXPRESS_ITEM_PATH.search(urlparse(url).path or "")
    return match.group("item_id") if match else None


def is_aliexpress_pdp_api_url(url: str) -> bool:
    folded = (url or "").casefold()
    return "mtop.aliexpress.pdp.pc.query" in folded or (
        "mtop.aliexpress" in folded and "asyncpcdetail" in folded
    )


def is_aliexpress_search_page_url(url: str) -> bool:
    if not is_aliexpress_url(url):
        return False
    path = (urlparse(url).path or "").casefold()
    return (
        path.startswith("/w/wholesale")
        or path.startswith("/wholesale")
        or "searchtext=" in (urlparse(url).query or "").casefold()
    )


def is_aliexpress_search_api_url(url: str) -> bool:
    folded = (url or "").casefold()
    return "aer-webapi" in folded and "/search" in folded


def looks_like_aliexpress_pdp(raw: str) -> bool:
    text = raw or ""
    head = text[:8_000]
    if "FAIL_SYS_USER_VALIDATE" in head or "RGV587" in head:
        return False
    if "FAIL_SYS_TOKEN_EMPTY" in head and "PRODUCT_TITLE" not in text[:50_000]:
        return False
    # Component order varies; PRICE/SKU/TITLE may sit after a large POPUP blob.
    sample = text[:80_000]
    return (
        "PRODUCT_TITLE" in sample
        or "targetSkuPriceInfo" in sample
        or "skuIdStrPriceInfoMap" in sample
        or ('"PRICE"' in sample and '"SKU"' in sample)
    )


def looks_like_aliexpress_search_payload(raw: str) -> bool:
    sample = (raw or "")[:4_000]
    return '"itemList"' in sample or '"productId"' in sample or "mods" in sample[:200]


def wrap_aliexpress_pdp_json(raw: str) -> str:
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>AliExpress PDP</title></head><body>"
        f'<script type="application/json" data-aliexpress-pdp="1">{raw}</script>'
        "</body></html>"
    )


def wrap_aliexpress_search_json(raw: str) -> str:
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>AliExpress Search</title></head><body>"
        f'<script type="application/json" data-aliexpress-search="1">{raw}</script>'
        "</body></html>"
    )


def is_aliexpress_block_page(html: str, *, title: str | None = None) -> bool:
    """True for TMD/RGV587/x5sec shells without a usable PDP payload."""
    if "data-aliexpress-pdp" in (html or "") and "PRODUCT_TITLE" in (html or ""):
        return False
    title_text = (title or "").strip().casefold()
    lower = (html or "")[:12_000].casefold()
    haystack = f"{title_text}\n{lower}"
    markers = (
        "rgv587",
        "fail_sys_user_validate",
        "fail_sys_token_empty",
        "_____tmd_____",
        "x5secdata",
        "baxia-dialog",
        "nc_wrapper",
        "slide-to-validate",
    )
    if any(marker in haystack for marker in markers):
        # CSR shell with empty title is common when MTop is gated.
        if "product_title" not in lower and "targetskupriceinfo" not in lower:
            return True
    return False


def apply_shopee_br_proxy_targeting(proxy_url: str, page_url: str) -> str:
    """Pin DataImpulse-style username geo to Brazil for shopee.com.br."""
    return apply_proxy_geo_targeting(proxy_url, page_url)


def apply_proxy_geo_targeting(proxy_url: str, page_url: str) -> str:
    """Pin residential proxy country to the storefront market when possible.

    DataImpulse-style usernames accept ``__cr.<cc>`` (ISO country). Shopee BR
    needs Brazil egress; Best Buy (and amazon.com) need US egress — BR IPs
    often see ``NS_ERROR_NET_RESET`` from Akamai on ``bestbuy.com``.

    Best Buy also pins a sticky ``sessid`` so ``_abck`` / sensor cookies stay
    bound to one residential exit for the session window (~30 min).
    """
    if not proxy_url:
        return proxy_url
    country: str | None = None
    if is_shopee_url(page_url):
        country = "br"
    elif is_aliexpress_url(page_url):
        country = "br"
    elif is_bestbuy_url(page_url):
        country = "us"
    elif is_amazon_us_url(page_url):
        country = "us"
    if country is None:
        return proxy_url
    pinned = _pin_proxy_country(proxy_url, country)
    if is_bestbuy_url(page_url):
        return _pin_proxy_sessid(pinned, "scoutbb")
    return pinned


def is_bestbuy_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    return host == "bestbuy.com" or host.endswith(".bestbuy.com")


def is_amazon_us_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    return host == "amazon.com"


def _pin_proxy_country(proxy_url: str, country: str) -> str:
    parsed = urlparse(proxy_url.strip())
    if not parsed.hostname or not parsed.username:
        return proxy_url
    username = unquote(parsed.username)
    marker = f"__cr.{country.casefold()}"
    if "__cr." in username:
        # Replace an existing country pin rather than stacking markers.
        username = re.sub(r"__cr\.[a-z]{2}\b", marker, username, count=1, flags=re.I)
        if marker not in username.casefold():
            username = f"{username}{marker}"
    else:
        username = f"{username}{marker}"
    return _rebuild_proxy_url(parsed, username)


def _pin_proxy_sessid(proxy_url: str, sessid: str) -> str:
    """Pin DataImpulse-style sticky session (``;sessid.<id>``) on the username."""
    parsed = urlparse(proxy_url.strip())
    if not parsed.hostname or not parsed.username:
        return proxy_url
    username = unquote(parsed.username)
    token = f"sessid.{sessid}"
    if "sessid." in username.casefold():
        username = re.sub(
            r";?sessid\.[^;]+",
            f";{token}",
            username,
            count=1,
            flags=re.I,
        )
        if "sessid." not in username.casefold():
            username = f"{username};{token}"
    else:
        username = f"{username};{token}"
    return _rebuild_proxy_url(parsed, username)


def _rebuild_proxy_url(parsed: Any, username: str) -> str:
    from urllib.parse import quote, urlunparse

    netloc = (
        f"{quote(username, safe='')}"
        f":{quote(unquote(parsed.password or ''), safe='')}"
        f"@{parsed.hostname}"
    )
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    rebuilt: str = urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    return rebuilt


def proxy_settings_from_url(proxy_url: str) -> dict[str, str]:
    """Convert ``http(s)://user:pass@host:port`` into Camoufox/Playwright proxy dict."""
    parsed = urlparse(proxy_url.strip())
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid proxy URL: {proxy_url!r}")
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port is not None:
        server = f"{server}:{parsed.port}"
    settings: dict[str, str] = {"server": server}
    if parsed.username:
        settings["username"] = unquote(parsed.username)
    if parsed.password:
        settings["password"] = unquote(parsed.password)
    return settings


def default_user_data_dir() -> Path:
    return Path.home() / ".cache" / "scout-api" / "camoufox-profiles" / "default"


def _estimate_response_bytes(response: Any) -> int:
    try:
        headers = getattr(response, "headers", None)
        if headers is not None:
            cl = headers.get("content-length")
            if cl is not None:
                return max(0, int(cl))
    except Exception:
        pass
    try:
        body = response.body()
        if body is not None:
            return len(body)
    except Exception:
        pass
    return 0


class UrllibHtmlFetcher:
    """Lightweight HTTP fetch for stores without a hard WAF (tests / fallback)."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        user_agent: str,
        timeout: int = 30,
    ) -> None:
        if opener is None:
            # Persist cookies across fetches on this instance (session reuse).
            jar = CookieJar()
            self._opener = build_opener(HTTPCookieProcessor(jar)).open
        else:
            self._opener = opener
        self._user_agent = user_agent
        self._timeout = timeout

    def fetch(self, url: str) -> HtmlResponse:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": accept_language_for_url(url),
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        referer = marketplace_referer_for_url(url)
        if referer:
            headers["Referer"] = referer
        request = UrlRequest(url, headers=headers)
        try:
            with self._opener(request, timeout=self._timeout) as upstream:
                body = upstream.read()
                status = int(getattr(upstream, "status", 200))
                content_type = upstream.headers.get("Content-Type", "")
                charset = upstream.headers.get_content_charset() or "utf-8"
                final_url = str(getattr(upstream, "geturl", lambda: url)() or url)
                if status >= 400:
                    raise RequestError(
                        f"A loja recusou a requisição (HTTP {status})",
                        code="UPSTREAM_BLOCKED"
                        if status in (403, 429)
                        else "UPSTREAM_HTTP_ERROR",
                        url=url,
                        upstream_status=status,
                        retryable=status == 429,
                    )
                text = body.decode(charset, errors="replace")
                if is_challenge_page(text) or is_auth_wall_page(
                    text, url=final_url or url
                ):
                    raise RequestError(
                        "A loja bloqueou a requisição (desafio anti-bot / auth wall)",
                        code="UPSTREAM_BLOCKED",
                        url=final_url or url,
                        upstream_status=403,
                        retryable=True,
                    )
                return HtmlResponse(
                    url=final_url,
                    status=status,
                    headers={"Content-Type": content_type},
                    body=body,
                    encoding=charset,
                    request=Request(final_url),
                )
        except RequestError:
            raise
        except HTTPError as exc:
            raise RequestError(
                "A loja recusou a requisição",
                code="UPSTREAM_BLOCKED"
                if exc.code in (403, 429)
                else "UPSTREAM_HTTP_ERROR",
                url=url,
                upstream_status=exc.code,
                retryable=exc.code == 429,
            ) from exc
        except URLError as exc:
            raise RequestError(
                "Não foi possível conectar à loja",
                code="UPSTREAM_NETWORK_ERROR",
                url=url,
                retryable=True,
            ) from exc
        except OSError as exc:
            raise RequestError(
                "Falha local ao acessar a loja",
                code="UPSTREAM_REQUEST_ERROR",
                url=url,
                retryable=False,
            ) from exc


class _WarmBrowserSession:
    """Keep one Camoufox persistent context alive across fetches.

    Launch is amortized; pages are created/closed per URL. Fingerprint is
    locale + profile + proxy — changing fingerprint replaces the session
    (Playwright Sync cannot hold multiple Camoufox contexts on one thread).
    """

    DEFAULT_IDLE_TIMEOUT_S = 10.0

    __slots__ = (
        "cm",
        "browser",
        "fingerprint",
        "fetch_count",
        "scheduler",
        "slot_lease",
        "_idle_timeout_s",
        "_on_idle",
        "_idle_timer",
        "_idle_generation",
        "_timer_lock",
    )

    def __init__(
        self,
        *,
        cm: AbstractContextManager[Any],
        browser: Any,
        fingerprint: str,
        on_idle: Callable[[_WarmBrowserSession, int], None] | None = None,
    ) -> None:
        self.cm = cm
        self.browser = browser
        self.fingerprint = fingerprint
        self.fetch_count = 0
        self.scheduler: Any | None = None
        self.slot_lease: Any | None = None
        self._idle_timeout_s = 0.0
        self._on_idle = on_idle
        self._idle_timer: threading.Timer | None = None
        self._idle_generation = 0
        self._timer_lock = threading.Lock()

    def retain_profile_ownership(
        self,
        *,
        scheduler: Any,
        slot_lease: Any,
        idle_timeout_s: float,
    ) -> None:
        self.scheduler = scheduler
        self.slot_lease = slot_lease
        self._idle_timeout_s = idle_timeout_s
        self.touch()

    def touch(self) -> None:
        if self._idle_timeout_s <= 0 or self._on_idle is None:
            return
        with self._timer_lock:
            self._idle_generation += 1
            generation = self._idle_generation
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            self._idle_timer = threading.Timer(
                self._idle_timeout_s,
                self._on_idle,
                args=(self, generation),
            )
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def lock_lost(self) -> bool:
        lock_lease = getattr(self.slot_lease, "_profile_lock_lease", None)
        return bool(lock_lease is not None and lock_lease.lost)

    def idle_generation_matches(self, generation: int) -> bool:
        with self._timer_lock:
            return generation == self._idle_generation

    def close(self, *, generation: int | None = None) -> None:
        with self._timer_lock:
            if generation is not None and generation != self._idle_generation:
                return
            self._idle_generation += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        try:
            self.cm.__exit__(None, None, None)
        except Exception:
            logger.debug("camoufox_warm_session_close_failed", exc_info=True)
        finally:
            if self.scheduler is not None and self.slot_lease is not None:
                self.scheduler.release_profile_lock(self.slot_lease)
                self.scheduler = None
                self.slot_lease = None


class CamoufoxHtmlFetcher:
    """Fetch product HTML with Camoufox (Firefox patched for anti-bot)."""

    def __init__(
        self,
        *,
        headless: bool = True,
        humanize: bool = True,
        timeout_ms: int = 90_000,
        launch_timeout_ms: int = 45_000,
        settle_ms: int = 5_000,
        max_settle_attempts: int = 12,
        proxy_url: str | None = None,
        user_data_dir: str | Path | None = None,
        disable_coop: bool = True,
        warmup_origin: bool = True,
        warmup_policy: WarmupPolicy = "always",
        block_resource_types: Sequence[str] | None = None,
        early_stop_on_shopee_get_pc: bool = True,
        fetch_strategy: str = "camoufox",
        browser_factory: BrowserFactory | None = None,
        challenge_resolver: Any | None = None,
        warm_reuse: bool = True,
        warm_max_fetches: int = 40,
        # BrowserScheduler (Phase 1 / C1 hardening)
        scheduler: Any | None = None,
        scheduler_enabled: bool = True,
    ) -> None:
        self._headless = headless
        self._humanize = humanize
        self._timeout_ms = timeout_ms
        self._launch_timeout_ms = max(5_000, launch_timeout_ms)
        self._settle_ms = settle_ms
        self._max_settle_attempts = max_settle_attempts
        self._proxy_url = proxy_url
        if user_data_dir:
            self._user_data_dir = Path(user_data_dir)
        else:
            self._user_data_dir = default_user_data_dir()
        self._disable_coop = disable_coop
        self._warmup_origin = warmup_origin
        self._warmup_policy = warmup_policy
        self._block_resource_types = tuple(block_resource_types or ())
        self._early_stop_on_shopee_get_pc = early_stop_on_shopee_get_pc
        self._fetch_strategy = fetch_strategy
        self._browser_factory = browser_factory
        self._challenge_resolver = challenge_resolver
        self._warm_reuse = warm_reuse
        self._warm_max_fetches = max(1, warm_max_fetches)
        self._lock = threading.Lock()
        self._owner = _PlaywrightOwnerLoop()
        self._warmed_origins: set[str] = set()
        self._warm_session: _WarmBrowserSession | None = None
        self._active_fingerprint: str | None = None
        self._browser_launches = 0
        self._browser_reuses = 0
        self._browser_launch_failures = 0
        self._browser_circuit_open_hits = 0
        # BrowserScheduler: bounded FIFO queue (Phase 1 / C1 hardening)
        self._scheduler = scheduler
        self._scheduler_enabled = scheduler_enabled and scheduler is not None

    @property
    def proxy_url(self) -> str | None:
        return self._proxy_url

    @property
    def block_resource_types(self) -> tuple[str, ...]:
        return self._block_resource_types

    @property
    def warmed_origins(self) -> frozenset[str]:
        return frozenset(self._warmed_origins)

    @property
    def browser_launch_count(self) -> int:
        return self._browser_launches

    @property
    def browser_reuse_count(self) -> int:
        return self._browser_reuses

    @property
    def browser_launch_failure_count(self) -> int:
        return self._browser_launch_failures

    @property
    def browser_circuit_open_hit_count(self) -> int:
        return self._browser_circuit_open_hits

    def close(self) -> None:
        """Release any warm Camoufox session held by this fetcher."""

        def _close() -> None:
            with self._lock:
                self._close_warm_session_unlocked()

        self._owner.call(_close)

    def fetch(
        self,
        url: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> HtmlResponse:
        """Fetch URL with Camoufox, gated by BrowserScheduler when enabled.

        If the scheduler is enabled, acquire() is called BEFORE dispatching to
        the owner thread. This replaces the implicit unbounded queue of
        _PlaywrightOwnerLoop with an explicit bounded FIFO queue with timeout
        and cancellation support. Navigation/selectors/Camoufox kwargs are
        unchanged.

        Args:
            url: Target product URL.
            cancel_event: When set, the pending queue entry is removed and
                          RequestError(BROWSER_JOB_CANCELLED) is raised.
                          Threaded directly to BrowserScheduler.acquire().
                          Has no effect when the scheduler is disabled.
        """
        if self._scheduler_enabled and self._scheduler is not None:
            lease = None
            if not self._has_reusable_warm_session(url):
                lease = self._scheduler.acquire(cancel_event=cancel_event)
            store_cfg = resolve_store_config(url)
            store_key = store_cfg.key if store_cfg is not None else "unknown"
            if lease is not None:
                logger.debug(
                    "browser_slot_fetch_start",
                    extra={"slot_id": lease.slot_id, "url": url, "store": store_key},
                )
            try:
                return self._fetch_via_owner(url, slot_lease=lease)
            finally:
                if lease is not None:
                    self._scheduler.release(lease)
                    logger.debug(
                        "browser_slot_fetch_end",
                        extra={
                            "slot_id": lease.slot_id,
                            "url": url,
                            "store": store_key,
                        },
                    )
        return self._fetch_via_owner(url)

    def browser_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout_ms: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[int, str]:
        """POST using the warm Camoufox context (cookies / CF clearance).

        Used by Visão VIP Strategy A: bare ``httpx`` is Cloudflare-403'd, but
        the same Server Action succeeds when issued from the browser session
        that already hydrated the SERP. Does not change navigation/selectors.
        """

        def _post() -> tuple[int, str]:
            browser, _reused = self._acquire_browser(url)
            # Cloudflare JA3-blocks Playwright APIRequestContext even with
            # shared cookies. In-page fetch() uses Camoufox TLS + cookies.
            page = self._new_page(browser)
            try:
                parsed = urlparse(url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
                land = headers.get("Referer") or f"{origin}/"
                timeout = float(
                    timeout_ms if timeout_ms is not None else self._timeout_ms
                )
                page.goto(land, wait_until="domcontentloaded", timeout=timeout)
                body_text = (
                    data.decode("utf-8")
                    if isinstance(data, (bytes, bytearray))
                    else str(data)
                )
                result = page.evaluate(
                    """async ({url, headers, body}) => {
                            const resp = await fetch(url, {
                                method: 'POST',
                                headers,
                                body,
                                credentials: 'include',
                            });
                            const text = await resp.text();
                            return {status: resp.status, text};
                        }""",
                    {
                        "url": url,
                        "headers": dict(headers),
                        "body": body_text,
                    },
                )
                if not isinstance(result, dict):
                    raise RequestError(
                        "browser_post evaluate retornou shape inesperado",
                        code="BROWSER_INFRASTRUCTURE_UNAVAILABLE",
                        url=url,
                        retryable=False,
                    )
                status = int(result.get("status") or 0)
                text = result.get("text") or ""
                return status, str(text)
            finally:
                closer = getattr(page, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        logger.debug(
                            "browser_post_page_close_failed",
                            exc_info=True,
                        )

        if self._scheduler_enabled and self._scheduler is not None:
            lease = None
            if not self._has_reusable_warm_session(url):
                lease = self._scheduler.acquire(cancel_event=cancel_event)
            try:
                return self._owner_call_with_profile_lease(
                    _post,
                    slot_lease=lease,
                )
            finally:
                if lease is not None:
                    self._scheduler.release(lease)
        return self._owner_call_with_profile_lease(_post, slot_lease=None)

    def _fetch_via_owner(
        self,
        url: str,
        *,
        slot_lease: Any | None = None,
    ) -> HtmlResponse:
        """Dispatch fetch to the owner thread (Playwright sync API)."""
        result = self._owner_call_with_profile_lease(
            lambda: self._fetch_locked(url),
            slot_lease=slot_lease,
        )
        assert isinstance(result, HtmlResponse)
        return result

    def _owner_call_with_profile_lease[T](
        self,
        operation: Callable[[], T],
        *,
        slot_lease: Any | None,
    ) -> T:
        def _run() -> T:
            with self._lock:
                try:
                    return operation()
                finally:
                    session = self._warm_session
                    scheduler = self._scheduler
                    if (
                        slot_lease is not None
                        and session is not None
                        and scheduler is not None
                    ):
                        if scheduler.retain_profile_lock(slot_lease):
                            lock_lease = slot_lease._profile_lock_lease
                            ttl_ms = getattr(lock_lease, "_ttl_ms", None)
                            lock_wait_ms = getattr(
                                self._scheduler,
                                "profile_lock_timeout_ms",
                                30_000,
                            )
                            idle_limits = [
                                _WarmBrowserSession.DEFAULT_IDLE_TIMEOUT_S,
                                max(0.05, lock_wait_ms / 2000.0),
                            ]
                            if isinstance(ttl_ms, int):
                                idle_limits.append(max(0.05, ttl_ms / 3000.0))
                            idle_timeout_s = min(idle_limits)
                            session.retain_profile_ownership(
                                scheduler=self._scheduler,
                                slot_lease=slot_lease,
                                idle_timeout_s=idle_timeout_s,
                            )
                        else:
                            # Without a backend that can retain ownership, do
                            # not leave a persistent browser on an unprotected profile.
                            self._close_warm_session_unlocked()
                    elif self._scheduler is not None and session is not None:
                        # An injected but disabled scheduler cannot protect a
                        # persistent profile between fetches.
                        self._close_warm_session_unlocked()

        return self._owner.call(_run)

    def _has_reusable_warm_session(self, url: str) -> bool:
        def _check() -> bool:
            with self._lock:
                session = self._warm_session
                if session is None:
                    return False
                kwargs = self._launch_kwargs(url=url)
                if (
                    session.fingerprint != self._warm_fingerprint(kwargs)
                    or session.fetch_count >= self._warm_max_fetches
                    or session.lock_lost()
                ):
                    self._close_warm_session_unlocked()
                    return False
                session.touch()
                return True

        return bool(self._owner.call(_check))

    def _expire_warm_session(
        self,
        expected: _WarmBrowserSession,
        generation: int,
    ) -> None:
        def _expire() -> None:
            with self._lock:
                if self._warm_session is expected and expected.idle_generation_matches(
                    generation
                ):
                    logger.info("camoufox_warm_session_idle_expired")
                    self._close_warm_session_unlocked()

        try:
            self._owner.call(_expire)
        except Exception:
            logger.warning("camoufox_warm_session_idle_close_failed", exc_info=True)

    def _close_warm_session_unlocked(self) -> None:
        """Close the single warm Camoufox session, if any."""
        session = self._warm_session
        self._warm_session = None
        self._active_fingerprint = None
        if session is not None:
            session.close()

    @staticmethod
    def _warm_fingerprint(launch_kwargs: dict[str, Any]) -> str:
        proxy = launch_kwargs.get("proxy") or {}
        proxy_key = ""
        if isinstance(proxy, dict):
            proxy_key = f"{proxy.get('server', '')}|{proxy.get('username', '')}"
        return (
            f"locale={launch_kwargs.get('locale')}"
            f"|dir={launch_kwargs.get('user_data_dir')}"
            f"|proxy={proxy_key}"
        )

    def _oneshot_browser(self, url: str) -> bool:
        """AliExpress needs a fresh temp profile; never reuse that context."""
        return is_aliexpress_url(url) or not self._warm_reuse

    def _acquire_browser(self, url: str) -> tuple[Any, bool]:
        """Return ``(browser, reused)``. Caller must not close a reused browser."""
        from scout_api.modules.crawler.core.browser_health import (
            TrialToken,
            browser_unavailable_error,
            get_browser_circuit,
        )

        circuit = get_browser_circuit()
        trial_token: TrialToken | None = None

        if not circuit.allow():
            # Circuit is OPEN or HALF_OPEN — try to claim the probe slot.
            trial_token = circuit.claim_trial()
            if trial_token is None:
                # Either still OPEN (cooldown not elapsed) or another probe is
                # already in progress.  Fail-fast without waiting.
                self._browser_circuit_open_hits += 1
                store_cfg = resolve_store_config(url)
                logger.warning(
                    "camoufox_circuit_open_skip",
                    extra={
                        "store": store_cfg.key if store_cfg is not None else None,
                        "url": url,
                        **circuit.snapshot(),
                    },
                )
                raise browser_unavailable_error(url=url)
            logger.info(
                "camoufox_circuit_probe_start",
                extra={"url": url, "token_id": trial_token.token_id},
            )

        launch_kwargs = self._launch_kwargs(url=url)
        fingerprint = self._warm_fingerprint(launch_kwargs)
        self._active_fingerprint = fingerprint
        session = self._warm_session
        if session is not None and (
            session.fingerprint != fingerprint
            or session.fetch_count >= self._warm_max_fetches
        ):
            self._close_warm_session_unlocked()
            session = None
        if session is None:
            timed = self._open_browser(url=url)
            try:
                browser = timed.__enter__()
            except Exception as exc:
                # Any failure during context enter is structural launch failure
                # (Playwright TimeoutError often lacks "launch" in the message).
                self._browser_launch_failures += 1
                if trial_token is not None:
                    circuit.complete_trial(trial_token, success=False)
                else:
                    circuit.record_launch_failure()
                logger.warning(
                    "camoufox_launch_failed",
                    extra={
                        "url": url,
                        "fingerprint": fingerprint,
                        "exc_type": type(exc).__name__,
                        "probe": trial_token is not None,
                        **circuit.snapshot(),
                    },
                )
                raise
            if trial_token is not None:
                circuit.complete_trial(trial_token, success=True)
            else:
                circuit.record_success()
            self._warm_session = _WarmBrowserSession(
                cm=timed,
                browser=browser,
                fingerprint=fingerprint,
                on_idle=self._expire_warm_session,
            )
            self._warm_session.fetch_count = 1
            self._browser_launches += 1
            store_cfg = resolve_store_config(url)
            logger.info(
                "camoufox_warm_launch",
                extra={
                    "store": store_cfg.key if store_cfg is not None else None,
                    "launches": self._browser_launches,
                    "fingerprint": fingerprint,
                    "launch_timeout_ms": self._launch_timeout_ms,
                    "probe": trial_token is not None,
                },
            )
            return browser, False
        # Warm session reuse — browser is already running; complete any active probe.
        if trial_token is not None:
            circuit.complete_trial(trial_token, success=True)
        session.fetch_count += 1
        session.touch()
        self._browser_reuses += 1
        store_cfg = resolve_store_config(url)
        store_key = store_cfg.key if store_cfg is not None else "unknown"
        observe(
            "browser_reuse",
            0.0,
            category=OperationCategory.BROWSER_LAUNCH,
            stage=store_key,
            context={
                "reuses": self._browser_reuses,
                "session_fetches": session.fetch_count,
            },
        )
        return session.browser, True

    def _fetch_locked(self, url: str) -> HtmlResponse:
        store_cfg = resolve_store_config(url)
        metrics = FetchCostMetrics(
            store=store_cfg.key if store_cfg else None,
            canonical_url=canonicalize_url(url),
            proxy_used=bool(self._proxy_url),
            proxy_policy=proxy_policy_for_url(url).value,
            fetch_strategy=self._fetch_strategy,
            blocked_resource_types=self._block_resource_types,
        )
        request_types: Counter[str] = Counter()
        transferred = 0
        t0 = time.perf_counter()
        oneshot = self._oneshot_browser(url)
        browser: Any = None
        reused = False
        try:
            from scout_api.modules.crawler.core.browser_health import (
                TrialToken,  # noqa: F401 (local re-import for oneshot path)
                browser_unavailable_error,
                get_browser_circuit,
            )

            circuit = get_browser_circuit()
            oneshot_trial: TrialToken | None = None
            if oneshot:
                # Playwright Sync cannot nest a second Camoufox context on the
                # owner thread while a warm session still holds the driver loop
                # (AliExpress oneshot during Match wave-2 → Sync-in-asyncio).
                if not circuit.allow():
                    oneshot_trial = circuit.claim_trial()
                    if oneshot_trial is None:
                        self._browser_circuit_open_hits += 1
                        raise browser_unavailable_error(url=url)
                    logger.info(
                        "camoufox_circuit_probe_start",
                        extra={
                            "url": url,
                            "oneshot": True,
                            "token_id": oneshot_trial.token_id,
                        },
                    )
                if self._warm_session is not None:
                    logger.info(
                        "camoufox_warm_drop_for_oneshot",
                        extra={"url": url},
                    )
                    self._close_warm_session_unlocked()
                browser_cm = self._open_browser(url=url)
                try:
                    browser = metrics.measure("browser_acquire", browser_cm.__enter__)
                except Exception as exc:
                    self._browser_launch_failures += 1
                    if oneshot_trial is not None:
                        circuit.complete_trial(oneshot_trial, success=False)
                    else:
                        circuit.record_launch_failure()
                    logger.warning(
                        "camoufox_launch_failed",
                        extra={
                            "url": url,
                            "oneshot": True,
                            "exc_type": type(exc).__name__,
                            "probe": oneshot_trial is not None,
                            **circuit.snapshot(),
                        },
                    )
                    raise
                if oneshot_trial is not None:
                    circuit.complete_trial(oneshot_trial, success=True)
                else:
                    circuit.record_success()
                self._browser_launches += 1
            else:
                browser, reused = metrics.measure(
                    "browser_acquire", lambda: self._acquire_browser(url)
                )
                browser_cm = None
            try:
                page = metrics.measure("page_create", lambda: self._new_page(browser))
                self._maybe_attach_resource_blocking(page, url)
                self._attach_cost_listeners(page, request_types)
                byte_holder = {"n": 0}

                def on_response(response: Any) -> None:
                    byte_holder["n"] += _estimate_response_bytes(response)

                on_fn = getattr(page, "on", None)
                if callable(on_fn):
                    on_fn("response", on_response)

                captured: dict[str, str] = {}
                shopee = is_shopee_url(url)
                shopee_search = is_shopee_search_page_url(url)
                aliexpress = is_aliexpress_url(url)
                aliexpress_search = is_aliexpress_search_page_url(url)
                if shopee:
                    if shopee_search:
                        self._attach_shopee_search_listener(page, captured)
                    else:
                        self._attach_shopee_get_pc_listener(page, captured, url)
                elif aliexpress:
                    if aliexpress_search:
                        self._attach_aliexpress_search_listener(page, captured)
                    else:
                        self._attach_aliexpress_pdp_listener(page, captured, url)
                warmup_used = False
                if self._should_warmup(url):
                    warmup_used = metrics.measure(
                        "warmup", lambda: self._maybe_warmup(page, url)
                    )
                metrics.warmup_used = warmup_used

                if shopee and self._early_stop_on_shopee_get_pc:
                    self._goto_shopee_early_stop(page, url, captured)
                    metrics.early_stop = True
                elif aliexpress:
                    self._goto_aliexpress_early_stop(page, url, captured)
                    metrics.early_stop = True
                    if not captured.get("body") and not aliexpress_search:
                        metrics.result = "blocked"
                        self._log_metrics(metrics, request_types, byte_holder["n"], t0)
                        raise RequestError(
                            "AliExpress não entregou payload MTop de produto "
                            "(shell CSR / anti-bot)",
                            code="UPSTREAM_BLOCKED",
                            url=url,
                            upstream_status=403,
                            retryable=True,
                        )
                else:
                    metrics.measure(
                        "navigation",
                        lambda: self._goto_with_bestbuy_retry(
                            page, url, metrics=metrics
                        ),
                    )

                html, final_url, title = metrics.measure(
                    "settle_challenge",
                    lambda: self._wait_for_product_html(
                        page, captured=captured, resume_url=url
                    ),
                )
                if captured.get("body"):
                    if shopee_search or captured.get("kind") == "search":
                        if aliexpress or captured.get("store") == "aliexpress":
                            html = wrap_aliexpress_search_json(captured["body"])
                            title = "AliExpress Search"
                        else:
                            html = wrap_shopee_search_json(captured["body"])
                            title = "Shopee Search"
                        final_url = url
                        metrics.get_pc_captured = True
                        logger.info(
                            "store_search_api_intercepted",
                            extra={"url": captured.get("response_url") or url},
                        )
                    elif aliexpress or captured.get("store") == "aliexpress":
                        html = wrap_aliexpress_pdp_json(captured["body"])
                        final_url = url
                        title = "AliExpress PDP"
                        metrics.get_pc_captured = True
                        logger.info(
                            "aliexpress_mtop_pdp_intercepted",
                            extra={"url": captured.get("response_url") or url},
                        )
                    else:
                        html = wrap_shopee_pdp_json(captured["body"])
                        final_url = url
                        title = "Shopee PDP"
                        metrics.get_pc_captured = True
                        logger.info(
                            "shopee_get_pc_intercepted",
                            extra={"url": captured.get("response_url") or url},
                        )
                # AliExpress PDP is CSR: without intercepted MTop SUCCESS there is
                # no product payload — fail here so ProxyPolicy.FALLBACK can retry.
                if (
                    aliexpress
                    and not aliexpress_search
                    and not (
                        captured.get("body")
                        and captured.get("store") == "aliexpress"
                        and captured.get("kind") == "pdp"
                    )
                ):
                    metrics.result = "blocked"
                    self._log_metrics(metrics, request_types, byte_holder["n"], t0)
                    raise RequestError(
                        "AliExpress não entregou payload MTop de produto "
                        "(shell CSR / anti-bot)",
                        code="UPSTREAM_BLOCKED",
                        url=final_url or url,
                        upstream_status=403,
                        retryable=True,
                    )
                if is_shopee_traffic_block(final_url or url, html):
                    # Prefer a captured signed search payload over a traffic wall.
                    if not (
                        captured.get("body")
                        and (shopee_search or captured.get("kind") == "search")
                    ):
                        # ADR 0018: attempt login/session before AUTH_REQUIRED.
                        if self._challenge_resolver is not None:
                            resolved = self._challenge_resolver.try_resolve(
                                page,
                                html=html,
                                title=title,
                                page_url=final_url or url,
                                resume_url=url,
                            )
                            if resolved:
                                metrics.retry_count = int(metrics.retry_count) + 1
                                # Short resume: do not re-enter the full settle
                                # loop (would re-attempt login many times).
                                try:
                                    self._goto(page, url)
                                    resume_deadline = time.perf_counter() + 20.0
                                    while (
                                        not captured.get("body")
                                        and time.perf_counter() < resume_deadline
                                    ):
                                        cur = str(getattr(page, "url", "") or "")
                                        if is_shopee_traffic_block(cur, ""):
                                            break
                                        page.wait_for_timeout(250)
                                    html = page.content()
                                    final_url = str(page.url)
                                    try:
                                        title = str(page.title())
                                    except Exception:
                                        title = title
                                except Exception:
                                    logger.warning(
                                        "shopee_post_auth_resume_failed",
                                        exc_info=True,
                                    )
                                if captured.get("body") and (
                                    shopee_search or captured.get("kind") == "search"
                                ):
                                    html = wrap_shopee_search_json(captured["body"])
                                    final_url = url
                                    title = "Shopee Search"
                                    metrics.get_pc_captured = True
                                elif captured.get("body"):
                                    html = wrap_shopee_pdp_json(captured["body"])
                                    final_url = url
                                    title = "Shopee PDP"
                                    metrics.get_pc_captured = True
                        if is_shopee_traffic_block(final_url or url, html) and not (
                            captured.get("body")
                            and (
                                shopee_search
                                or captured.get("kind") in {"search", "pdp"}
                            )
                        ):
                            metrics.result = "blocked"
                            self._log_metrics(
                                metrics, request_types, byte_holder["n"], t0
                            )
                            raise shopee_auth_required_error(url=final_url or url)
                if is_hard_block_page(html, title=title):
                    metrics.result = "blocked"
                    self._log_metrics(metrics, request_types, byte_holder["n"], t0)
                    raise RequestError(
                        "A loja bloqueou o IP de saída (hard-block anti-bot); "
                        "configure um proxy residencial (CAMOUFOX_PROXY_URL)",
                        code="UPSTREAM_BLOCKED",
                        url=final_url or url,
                        upstream_status=403,
                        retryable=False,
                    )
                if needs_interstitial_resolution(
                    html, url=final_url or url, title=title
                ):
                    # ADR 0017 / 0018: attempt CAPTCHA + auth-wall resolution.
                    if self._challenge_resolver is not None:
                        resolved = self._challenge_resolver.try_resolve(
                            page,
                            html=html,
                            title=title,
                            page_url=final_url or url,
                            resume_url=url,
                        )
                        if resolved:
                            html = page.content()
                            final_url = str(page.url)
                            try:
                                title = str(page.title())
                            except Exception:
                                title = title
                            metrics.retry_count = int(metrics.retry_count) + 1
                    if needs_interstitial_resolution(
                        html, url=final_url or url, title=title
                    ):
                        metrics.result = "blocked"
                        self._log_metrics(metrics, request_types, byte_holder["n"], t0)
                        page_url = final_url or url
                        if is_auth_wall_page(html, url=page_url, title=title):
                            if is_shopee_url(page_url):
                                raise shopee_auth_required_error(url=page_url)
                            raise RequestError(
                                "A loja exige login/sessão autenticada "
                                "(falta de login). Configure as credenciais "
                                "da loja no ambiente ou faça o seed da sessão.",
                                code="AUTH_REQUIRED",
                                url=page_url,
                                upstream_status=401,
                                retryable=True,
                            )
                        raise RequestError(
                            "A loja bloqueou a requisição "
                            "(desafio/auth wall após tentativa de resolução)",
                            code="UPSTREAM_BLOCKED",
                            url=page_url,
                            upstream_status=403,
                            retryable=True,
                        )
                body = html.encode("utf-8")
                response = HtmlResponse(
                    url=final_url or url,
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=body,
                    encoding="utf-8",
                    request=Request(url),
                )
                transferred = byte_holder["n"]
                metrics.result = "success"
                self._log_metrics(metrics, request_types, transferred, t0)
                meta = metrics.as_log_dict()
                meta["browser_reused"] = reused
                meta["browser_launches"] = self._browser_launches
                meta["browser_reuses"] = self._browser_reuses
                response.meta["fetch_metrics"] = meta
                try:
                    page.close()
                except Exception:
                    logger.debug("camoufox_page_close_failed", exc_info=True)
                return response
            finally:
                if oneshot and browser_cm is not None:
                    try:
                        browser_cm.__exit__(None, None, None)
                    except Exception:
                        logger.debug(
                            "camoufox_oneshot_browser_close_failed",
                            exc_info=True,
                        )
        except RequestError as exc:
            # Owner-thread serializes Playwright; only drop warm when the
            # context itself is poisoned (not every UPSTREAM_BLOCKED/challenge).
            if not oneshot and _should_drop_warm_session(exc):
                logger.info(
                    "camoufox_warm_drop_after_error",
                    extra={"url": url, "code": exc.code},
                )
                self._close_warm_session_unlocked()
            raise
        except Exception as exc:
            if not oneshot:
                # Unknown render/process failure — drop warm session so the
                # next fetch cold-starts instead of reusing a dead browser.
                logger.info(
                    "camoufox_warm_drop_after_error",
                    extra={"url": url, "code": "unclassified"},
                )
                self._close_warm_session_unlocked()
            # Launch failures already recorded in _acquire_browser / oneshot enter.
            metrics.result = "error"
            self._log_metrics(metrics, request_types, transferred, t0)
            store_key = store_cfg.key if store_cfg is not None else None
            logger.exception(
                "camoufox_fetch_failed",
                extra={
                    "url": url,
                    "store": store_key,
                    "oneshot": oneshot,
                    "launch_failures": self._browser_launch_failures,
                },
            )
            raise classify_camoufox_navigation_error(exc, url=url) from exc

    def _log_metrics(
        self,
        metrics: FetchCostMetrics,
        request_types: Counter[str],
        transferred: int,
        t0: float,
    ) -> None:
        metrics.network_request_count = sum(request_types.values())
        metrics.requests_by_resource_type = dict(request_types)
        metrics.estimated_transferred_bytes = transferred
        metrics.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        payload = metrics.as_log_dict()
        logger.info(
            "fetch_cost_metrics store=%s proxy_used=%s proxy_policy=%s "
            "warmup_used=%s get_pc_captured=%s early_stop=%s "
            "network_request_count=%s estimated_transferred_bytes=%s "
            "duration_ms=%s result=%s requests_by_resource_type=%s "
            "stage_timings_ms=%s",
            payload.get("store"),
            payload.get("proxy_used"),
            payload.get("proxy_policy"),
            payload.get("warmup_used"),
            payload.get("get_pc_captured"),
            payload.get("early_stop"),
            payload.get("network_request_count"),
            payload.get("estimated_transferred_bytes"),
            payload.get("duration_ms"),
            payload.get("result"),
            payload.get("requests_by_resource_type"),
            payload.get("stage_timings_ms"),
            extra=payload,
        )
        observe(
            "browser_fetch",
            float(metrics.duration_ms),
            category=OperationCategory.BROWSER_NAVIGATION,
            stage=str(payload.get("store") or "unknown"),
            context={
                "result": payload.get("result"),
                "proxy_used": payload.get("proxy_used"),
                "retry_count": payload.get("retry_count"),
                "network_request_count": payload.get("network_request_count"),
                "warmup_used": payload.get("warmup_used"),
                "early_stop": payload.get("early_stop"),
                "browser_launches": self._browser_launches,
                "browser_reuses": self._browser_reuses,
                "browser_launch_failures": self._browser_launch_failures,
                "circuit_open_hits": self._browser_circuit_open_hits,
            },
        )

    def _attach_cost_listeners(
        self,
        page: Any,
        request_types: Counter[str],
    ) -> None:
        def on_request(request: Any) -> None:
            rtype = str(getattr(request, "resource_type", "unknown") or "unknown")
            request_types[rtype] += 1

        on_fn = getattr(page, "on", None)
        if callable(on_fn):
            on_fn("request", on_request)

    def _maybe_attach_resource_blocking(self, page: Any, url: str) -> None:
        # Proxy cost mode: abort heavy assets on any proxied navigation.
        del url
        if not self._block_resource_types:
            return
        blocked = set(self._block_resource_types)
        route_fn = getattr(page, "route", None)
        if not callable(route_fn):
            logger.debug("camoufox_route_unavailable")
            return

        def _route(route: Any, request: Any) -> None:
            if getattr(request, "resource_type", None) in blocked:
                route.abort()
            else:
                route.continue_()

        route_fn("**/*", _route)

    def _should_warmup(self, url: str) -> bool:
        if not self._warmup_origin:
            return False
        # AliExpress: fresh context + MTop intercept; origin warmup often burns
        # budget and does not mint a usable PDP token on cold/direct egress.
        if is_aliexpress_url(url):
            return False
        if self._warmup_policy == "never":
            return False
        warm = warmup_url_for(url)
        if warm is None:
            return False
        if self._warmup_policy == "once_per_session":
            origin = (urlparse(warm).netloc or "").lower()
            return bool(origin) and origin not in self._warmed_origins
        return True

    def _mark_warmup(self, url: str) -> None:
        warm = warmup_url_for(url)
        if warm is None:
            return
        origin = (urlparse(warm).netloc or "").lower()
        if origin:
            self._warmed_origins.add(origin)

    def _open_browser(self, *, url: str) -> AbstractContextManager[Any]:
        launch_kwargs = self._launch_kwargs(url=url)
        if self._browser_factory is not None:
            inner = self._browser_factory(**launch_kwargs)
        else:
            from camoufox.sync_api import Camoufox

            inner = Camoufox(**launch_kwargs)  # type: ignore[no-untyped-call]
        store_cfg = resolve_store_config(url)
        store_key = store_cfg.key if store_cfg is not None else "unknown"
        return _TimedBrowserLaunch(inner, store=store_key)

    def _launch_kwargs(self, *, url: str) -> dict[str, Any]:
        # Linux Docker headless is detected by Cloudflare; Xvfb "virtual" passes.
        headless: bool | str = self._headless
        if self._headless is True and sys.platform.startswith("linux"):
            headless = "virtual"
        profile_dir = self._ensure_user_data_dir()
        # Isolate locale fingerprints so the warm pool never shares one
        # Firefox profile directory (``.parentlock`` / concurrent contexts).
        if self._warm_reuse and not is_aliexpress_url(url):
            locale_slug = (locale_for_url(url) or "default").replace("-", "_").lower()
            profile_dir = profile_dir / f"locale_{locale_slug}"
            try:
                profile_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.debug(
                    "camoufox_locale_profile_mkdir_failed",
                    exc_info=True,
                    extra={"dir": str(profile_dir)},
                )
        kwargs: dict[str, Any] = {
            "headless": headless,
            "humanize": self._humanize,
            "os": "windows",
            "geoip": True,
            "persistent_context": True,
            "user_data_dir": str(profile_dir),
            # Playwright launch timeout (ms). Separate from page navigation
            # ``self._timeout_ms`` used in page.goto / settle.
            "timeout": self._launch_timeout_ms,
        }
        if is_aliexpress_url(url):
            import tempfile

            # Fresh profile per lookup — reused Camoufox state often yields RGV587.
            kwargs["user_data_dir"] = tempfile.mkdtemp(prefix="ae-camoufox-")
            kwargs["persistent_context"] = True
        # Camoufox #450 / #555: instant CSS-animation collapse is an Akamai
        # detection vector on some builds; opt out when the flag exists.
        kwargs["config"] = {"disableInstantAnimations": True}
        # uBlock triggers Shopee "automated tools" detection (Camoufox #345).
        try:
            from camoufox.addons import DefaultAddons

            kwargs["exclude_addons"] = [DefaultAddons.UBO]
        except Exception:
            logger.debug("camoufox_exclude_addons_unavailable", exc_info=True)
        if self._disable_coop:
            # Needed so Turnstile iframes are interactable; ack Camoufox leak warning.
            kwargs["disable_coop"] = True
            kwargs["i_know_what_im_doing"] = True
        if self._proxy_url:
            proxy_url = apply_proxy_geo_targeting(self._proxy_url, url)
            kwargs["proxy"] = proxy_settings_from_url(proxy_url)
            # Some HTTP residential proxies break Camoufox's geoip IP probe (SSL to
            # ipecho/etc). Keep residential egress; pin locale from the store URL.
            # Best Buy Akamai is especially sensitive — prefer geoip when we already
            # pinned ``__cr.us`` so timezone/WebRTC match the US exit IP.
            # AliExpress MTop similarly benefits from geoip matching BR egress.
            if is_bestbuy_url(url) or is_aliexpress_url(url):
                kwargs["geoip"] = True
            else:
                kwargs["geoip"] = False
            locale = locale_for_url(url)
            if locale is not None:
                kwargs["locale"] = locale
        else:
            locale = locale_for_url(url)
            if locale is not None:
                kwargs["locale"] = locale
        return kwargs

    def _ensure_user_data_dir(self) -> Path:
        try:
            self._user_data_dir.mkdir(parents=True, exist_ok=True)
            return self._user_data_dir
        except OSError as exc:
            fallback = Path("/tmp/scout-api-camoufox-profiles/default")
            logger.warning(
                "camoufox_profile_dir_unwritable",
                extra={
                    "configured": str(self._user_data_dir),
                    "fallback": str(fallback),
                    "error": str(exc),
                },
            )
            fallback.mkdir(parents=True, exist_ok=True)
            self._user_data_dir = fallback
            return fallback

    @staticmethod
    def _new_page(browser: Any) -> Any:
        if hasattr(browser, "new_page"):
            return browser.new_page()
        pages = getattr(browser, "pages", None)
        if pages:
            return pages[0]
        raise RuntimeError("Camoufox browser has no page factory")

    def _maybe_warmup(self, page: Any, url: str) -> bool:
        warmup = warmup_url_for(url)
        if warmup is None:
            return False
        logger.info("camoufox_warmup_origin", extra={"url": warmup})
        try:
            self._apply_page_headers(page, url)
            self._goto(page, warmup)
            # Brief pause so JS challenge / cookie minting can finish before product.
            # Best Buy Akamai sensor (``bmak`` / ``_abck``) needs a longer settle
            # plus light pointer telemetry — cold PDPs often see NET_RESET.
            settle = min(self._settle_ms, 3_000)
            if is_bestbuy_url(url):
                settle = max(settle, 6_000)
                self._bestbuy_akamai_sensor_nudge(page)
            page.wait_for_timeout(settle)
            self._mark_warmup(url)
            return True
        except Exception:
            logger.warning(
                "camoufox_warmup_failed",
                extra={"url": warmup},
                exc_info=True,
            )
            return False

    def _goto_with_bestbuy_retry(
        self,
        page: Any,
        url: str,
        *,
        metrics: FetchCostMetrics,
    ) -> None:
        """Navigate to PDP; on Best Buy Akamai TCP reset, re-warm and retry once."""
        self._apply_page_headers(page, url)
        try:
            self._goto(page, url)
            return
        except Exception as exc:
            if not is_bestbuy_url(url) or not _is_net_reset_error(exc):
                raise
            logger.warning(
                "bestbuy_net_reset_retry_after_warmup",
                extra={"url": url},
                exc_info=True,
            )
            metrics.retry_count = int(metrics.retry_count) + 1
            # Force another origin warm so ``_abck`` can mint on this sticky IP.
            self._warmed_origins.discard(_warmup_origin_key(url))
            self._maybe_warmup(page, url)
            self._apply_page_headers(page, url)
            self._goto(page, url)

    @staticmethod
    def _apply_page_headers(page: Any, url: str) -> None:
        headers: dict[str, str] = {
            "Accept-Language": accept_language_for_url(url),
        }
        referer = marketplace_referer_for_url(url)
        path = (urlparse(url).path or "/").rstrip("/") or "/"
        if referer and path != "/":
            headers["Referer"] = referer
        setter = getattr(page, "set_extra_http_headers", None)
        if callable(setter):
            try:
                setter(headers)
            except Exception:
                logger.debug("camoufox_set_headers_failed", exc_info=True)

    @staticmethod
    def _bestbuy_akamai_sensor_nudge(page: Any) -> None:
        """Light mouse wander so Akamai ``bmak`` posts telemetry on the homepage."""
        try:
            from .challenge_resolution import ChallengeResolver

            ChallengeResolver._akamai_wander_mouse(page)
        except Exception:
            logger.debug("bestbuy_akamai_sensor_nudge_failed", exc_info=True)

    def _goto(self, page: Any, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host == "magazineluiza.com.br":
            if urlparse(url).path in {"", "/"}:
                # Origin warmup retains its explicit settle below; waiting for
                # analytics networkidle does not validate or resolve a PDP.
                return
            from .magalu_readiness import magalu_document_ready

            html = page.content()
            if not needs_interstitial_resolution(
                html, url=url
            ) and magalu_document_ready(html, url):
                return
        wait_for_load_state = getattr(page, "wait_for_load_state", None)
        if callable(wait_for_load_state):
            try:
                wait_for_load_state(
                    "networkidle", timeout=min(20_000, self._timeout_ms)
                )
            except Exception:
                logger.debug(
                    "camoufox_networkidle_timeout",
                    extra={"url": url},
                    exc_info=True,
                )

    def _goto_shopee_early_stop(
        self, page: Any, url: str, captured: dict[str, str]
    ) -> None:
        """Navigate to PDP and stop as soon as a valid get_pc payload arrives."""
        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        deadline = time.perf_counter() + (self._timeout_ms / 1000.0)
        while not captured.get("body") and time.perf_counter() < deadline:
            if is_shopee_traffic_block(str(getattr(page, "url", "") or ""), ""):
                break
            page.wait_for_timeout(150)
        if captured.get("body"):
            self._mark_warmup(url)

    def _goto_aliexpress_early_stop(
        self, page: Any, url: str, captured: dict[str, str]
    ) -> None:
        """Navigate to AliExpress PDP/SERP and stop when MTop/search JSON arrives."""
        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        # Bound wait: SUCCESS MTop usually arrives in a few seconds; long waits
        # on cold/direct shells only burn time before ProxyPolicy.FALLBACK.
        budget_s = min(25.0, max(8.0, self._timeout_ms / 1000.0 / 3.0))
        deadline = time.perf_counter() + budget_s
        while not captured.get("body") and time.perf_counter() < deadline:
            try:
                html = page.content()
            except Exception:
                html = ""
            if is_aliexpress_block_page(html):
                # Bail early — waiting does not mint MTop SUCCESS on TMD punish.
                break
            page.wait_for_timeout(200)
        if captured.get("body"):
            self._mark_warmup(url)

    def _wait_for_product_html(
        self,
        page: Any,
        *,
        captured: dict[str, str] | None = None,
        resume_url: str | None = None,
    ) -> tuple[str, str, str]:
        html = ""
        final_url = ""
        title = ""
        resolve_failures = 0
        for attempt in range(max(1, self._max_settle_attempts)):
            if captured and captured.get("body"):
                break
            if attempt > 0:
                page.wait_for_timeout(self._settle_ms)
            html = page.content()
            final_url = str(page.url)
            try:
                title = str(page.title())
            except Exception:
                title = ""
            if is_hard_block_page(html, title=title):
                return html, final_url, title
            # verify/traffic is an auth wall — keep settling so the resolver
            # can login before we treat the response as terminal.
            if not needs_interstitial_resolution(html, url=final_url, title=title):
                return html, final_url, title
            logger.info(
                "camoufox_waiting_challenge",
                extra={"url": final_url, "attempt": attempt + 1},
            )
            # Mid-settle resolution (CAPTCHA / CF / auth wall).
            if self._challenge_resolver is not None and attempt >= 1:
                resolved = self._challenge_resolver.try_resolve(
                    page,
                    html=html,
                    title=title,
                    page_url=final_url,
                    resume_url=resume_url,
                )
                if resolved:
                    html = page.content()
                    final_url = str(page.url)
                    try:
                        title = str(page.title())
                    except Exception:
                        title = title
                    if not needs_interstitial_resolution(
                        html, url=final_url, title=title
                    ):
                        return html, final_url, title
                else:
                    # Failed resolve: do not burn remaining settle ticks
                    # (Magalu/Akamai previously hung /match for many minutes).
                    resolve_failures += 1
                    if resolve_failures >= 1:
                        logger.info(
                            "camoufox_challenge_fail_fast",
                            extra={"url": final_url, "attempt": attempt + 1},
                        )
                        return html, final_url, title
                # Shopee /verify/traffic: one auth attempt is enough — do not
                # re-login for every settle tick (can hang /match for minutes).
                if is_shopee_traffic_block(final_url, html) or is_shopee_traffic_block(
                    str(getattr(page, "url", "") or ""), ""
                ):
                    return html, final_url, title
        return html, final_url, title

    @classmethod
    def _attach_shopee_search_listener(
        cls,
        page: Any,
        captured: dict[str, str],
    ) -> None:
        """Capture the browser's own signed ``search_items`` response."""

        def on_response(response: Any) -> None:
            if captured.get("body"):
                return
            try:
                response_url = str(getattr(response, "url", "") or "")
            except Exception:
                return
            if not is_shopee_search_api_url(response_url):
                return
            try:
                status = int(getattr(response, "status", 0) or 0)
            except (TypeError, ValueError):
                status = 0
            if status and status >= 400:
                return
            try:
                raw = response.text()
            except Exception:
                logger.debug("shopee_search_body_read_failed", exc_info=True)
                return
            if not isinstance(raw, str) or not raw.strip().startswith("{"):
                return
            if not looks_like_shopee_search_payload(raw):
                return
            captured["body"] = raw
            captured["response_url"] = response_url
            captured["kind"] = "search"

        on_fn = getattr(page, "on", None)
        if not callable(on_fn):
            logger.debug("shopee_search_listener_unavailable")
            return
        on_fn("response", on_response)

    @classmethod
    def _attach_shopee_get_pc_listener(
        cls,
        page: Any,
        captured: dict[str, str],
        request_url: str,
    ) -> None:
        """Capture the browser's own signed get_pc response (Mode A)."""
        ids = shopee_ids_from_url(request_url)
        wanted_shop = ids[0] if ids else None
        wanted_item = ids[1] if ids else None

        def on_response(response: Any) -> None:
            if captured.get("body"):
                return
            try:
                response_url = str(getattr(response, "url", "") or "")
            except Exception:
                return
            if not is_shopee_get_pc_url(response_url):
                return
            if wanted_shop and wanted_shop not in response_url:
                return
            if wanted_item and wanted_item not in response_url:
                return
            try:
                status = int(getattr(response, "status", 0) or 0)
            except (TypeError, ValueError):
                status = 0
            if status and status >= 400:
                return
            try:
                raw = response.text()
            except Exception:
                logger.debug("shopee_get_pc_body_read_failed", exc_info=True)
                return
            if not isinstance(raw, str) or not raw.strip().startswith("{"):
                return
            if '"error"' in raw[:200] and "90309999" in raw[:400]:
                return
            if not looks_like_shopee_pdp(raw):
                return
            captured["body"] = raw
            captured["response_url"] = response_url
            captured["kind"] = "pdp"

        on_fn = getattr(page, "on", None)
        if not callable(on_fn):
            logger.debug("shopee_get_pc_listener_unavailable")
            return
        on_fn("response", on_response)

    @classmethod
    def _attach_aliexpress_search_listener(
        cls,
        page: Any,
        captured: dict[str, str],
    ) -> None:
        def on_response(response: Any) -> None:
            if captured.get("body"):
                return
            try:
                response_url = str(getattr(response, "url", "") or "")
            except Exception:
                return
            if not is_aliexpress_search_api_url(response_url):
                return
            try:
                status = int(getattr(response, "status", 0) or 0)
            except (TypeError, ValueError):
                status = 0
            if status and status >= 400:
                return
            try:
                raw = response.text()
            except Exception:
                logger.debug("aliexpress_search_body_read_failed", exc_info=True)
                return
            if not isinstance(raw, str) or not looks_like_aliexpress_search_payload(
                raw
            ):
                return
            captured["body"] = raw
            captured["response_url"] = response_url
            captured["kind"] = "search"
            captured["store"] = "aliexpress"

        on_fn = getattr(page, "on", None)
        if not callable(on_fn):
            logger.debug("aliexpress_search_listener_unavailable")
            return
        on_fn("response", on_response)

    @classmethod
    def _attach_aliexpress_pdp_listener(
        cls,
        page: Any,
        captured: dict[str, str],
        request_url: str,
    ) -> None:
        """Capture the browser's own signed MTop PDP response."""
        wanted_item = aliexpress_item_id_from_url(request_url)

        def on_response(response: Any) -> None:
            if captured.get("body") and captured.get("ok") == "1":
                return
            try:
                response_url = str(getattr(response, "url", "") or "")
            except Exception:
                return
            if not is_aliexpress_pdp_api_url(response_url):
                return
            if wanted_item and wanted_item not in response_url:
                # productId is usually in the query ``data=`` blob.
                pass
            try:
                status = int(getattr(response, "status", 0) or 0)
            except (TypeError, ValueError):
                status = 0
            if status and status >= 400:
                return
            try:
                raw = response.text()
            except Exception:
                logger.debug("aliexpress_mtop_body_read_failed", exc_info=True)
                return
            if not isinstance(raw, str) or not raw.strip():
                return
            if not looks_like_aliexpress_pdp(raw):
                return
            if (
                wanted_item
                and wanted_item not in raw
                and wanted_item not in response_url
            ):
                return
            captured["body"] = raw
            captured["response_url"] = response_url
            captured["kind"] = "pdp"
            captured["store"] = "aliexpress"
            captured["ok"] = "1"

        on_fn = getattr(page, "on", None)
        if not callable(on_fn):
            logger.debug("aliexpress_mtop_listener_unavailable")
            return
        on_fn("response", on_response)


class _TimedBrowserLaunch:
    """Measure browser/context enter without changing launch behavior."""

    def __init__(self, inner: AbstractContextManager[Any], *, store: str) -> None:
        self._inner = inner
        self._store = store

    def __enter__(self) -> Any:
        t0 = time.perf_counter()
        try:
            browser = self._inner.__enter__()
        except Exception as exc:
            observe(
                "browser_launch",
                (time.perf_counter() - t0) * 1000,
                category=OperationCategory.BROWSER_LAUNCH,
                stage=self._store,
                context={
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                    "failure_kind": _browser_launch_failure_kind(exc),
                },
                force_event=True,
            )
            raise
        observe(
            "browser_launch",
            (time.perf_counter() - t0) * 1000,
            category=OperationCategory.BROWSER_LAUNCH,
            stage=self._store,
        )
        return browser

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        result = self._inner.__exit__(exc_type, exc, tb)
        return result if isinstance(result, bool) else None


def _browser_launch_failure_kind(exc: BaseException) -> str:
    """Return a low-cardinality diagnostic without logging raw browser output."""
    message = str(exc).casefold()
    if (
        "process did exit" in message
        or "failed to launch the browser process" in message
    ):
        return "process_exit"
    if "timeout" in message or "timed out" in message:
        return "launch_timeout"
    if "profile" in message or "parentlock" in message or "lock file" in message:
        return "profile_or_lock"
    if "executable doesn't exist" in message or "no such file or directory" in message:
        return "missing_binary"
    return "launch_error"


def profile_dirs_for_base(base: Path) -> tuple[Path, Path]:
    """Return ``(direct_profile, proxied_profile)`` under the Camoufox profiles root."""
    # Seeded sticky Shopee sessions live in ``default``; direct stores use a sibling.
    if base.name == "default":
        return base.parent / "direct", base
    return Path(f"{base}-direct"), base


def camoufox_profiles_root(base: Path) -> Path:
    """Root directory containing ``slot-{n}`` dirs for cross-process ProfileLock."""
    if base.name in ("default", "direct"):
        return base.parent
    return base.parent


def _build_browser_scheduler(
    *,
    enabled: bool,
    capacity: int,
    queue_capacity: int,
    queue_timeout_ms: int,
    profile_lock_mode: str,
    profile_lock_ttl_ms: int,
    profile_lock_timeout_ms: int,
    profile_base_path: Path,
    redis_gateway: Any | None,
) -> Any | None:
    if not enabled:
        return None
    from scout_api.modules.crawler.core.browser_scheduler import BrowserScheduler
    from scout_api.modules.crawler.core.profile_lock import build_profile_lock

    profile_lock = build_profile_lock(
        profile_lock_mode,
        redis_gateway,
        redis_ttl_ms=profile_lock_ttl_ms,
    )
    scheduler = BrowserScheduler(
        capacity=max(1, capacity),
        queue_capacity=max(0, queue_capacity),
        queue_timeout_ms=max(0, queue_timeout_ms),
        profile_lock=profile_lock,
        profile_base_path=profile_base_path,
        profile_lock_timeout_ms=max(1_000, int(profile_lock_timeout_ms)),
    )
    logger.info(
        "browser_scheduler_created",
        extra={
            "capacity": capacity,
            "queue_capacity": queue_capacity,
            "queue_timeout_ms": queue_timeout_ms,
            "profile_lock_mode": (profile_lock_mode or "off").strip().lower(),
            "profile_base_path": str(profile_base_path),
        },
    )
    return scheduler


def build_html_fetcher(
    *,
    camoufox_enabled: bool,
    user_agent: str,
    urllib_timeout: int = 30,
    camoufox_headless: bool = True,
    camoufox_humanize: bool = True,
    camoufox_timeout_ms: int = 90_000,
    camoufox_launch_timeout_ms: int = 45_000,
    camoufox_settle_ms: int = 5_000,
    camoufox_max_settle_attempts: int = 12,
    camoufox_proxy_url: str | None = None,
    camoufox_user_data_dir: str | None = None,
    camoufox_disable_coop: bool = True,
    camoufox_warmup_origin: bool = True,
    camoufox_warm_reuse: bool = True,
    camoufox_warm_max_fetches: int = 40,
    shopee_warmup_policy: WarmupPolicy = "once_per_session",
    shopee_resource_blocking_enabled: bool = True,
    captcha_solver_enabled: bool = True,
    captcha_solver_provider: str = "amazoncaptcha",
    captcha_solver_max_attempts: int = 2,
    auth_bypass_enabled: bool = True,
    auth_bypass_max_attempts: int = 2,
    amazon_auth_email: str | None = None,
    amazon_auth_password: str | None = None,
    shopee_auth_email: str | None = None,
    shopee_auth_password: str | None = None,
    # BrowserScheduler (Phase 1 / C1 hardening)
    camoufox_browser_scheduler_enabled: bool = True,
    camoufox_browser_capacity: int = 1,
    camoufox_browser_queue_capacity: int = 32,
    camoufox_queue_timeout_ms: int = 60_000,
    # ProfileLock (Phase 4 / cross-process profile ownership)
    camoufox_profile_lock: str = "redis",
    camoufox_profile_lock_ttl_ms: int = 10 * 60 * 1000,
    camoufox_profile_lock_timeout_ms: int = 30_000,
    redis_gateway: Any | None = None,
) -> HtmlFetcher:
    """Build the shared store-aware fetcher (direct + optional proxied Camoufox)."""
    from .challenge_resolution import (
        ChallengeResolver,
        StoreAuthCredentials,
        build_image_captcha_solver,
    )

    http = UrllibHtmlFetcher(user_agent=user_agent, timeout=urllib_timeout)
    if not camoufox_enabled:
        return http

    from .amazon_http_first_fetcher import AmazonHttpFirstHtmlFetcher
    from .store_aware_fetcher import StoreAwareHtmlFetcher

    image_solver = build_image_captcha_solver(
        enabled=captcha_solver_enabled,
        provider=captcha_solver_provider,
    )
    resolve_attempts = max(
        1,
        captcha_solver_max_attempts,
        auth_bypass_max_attempts,
    )
    challenge_resolver = ChallengeResolver(
        image_solver=image_solver,
        max_attempts=resolve_attempts,
        soft_wait_ms=min(8_000, max(1_000, camoufox_settle_ms)),
        enabled=captcha_solver_enabled or auth_bypass_enabled,
        auth_bypass_enabled=auth_bypass_enabled,
        credentials=StoreAuthCredentials(
            amazon_email=amazon_auth_email,
            amazon_password=amazon_auth_password,
            shopee_email=shopee_auth_email,
            shopee_password=shopee_auth_password,
        ),
    )

    base_dir = (
        Path(camoufox_user_data_dir)
        if camoufox_user_data_dir
        else default_user_data_dir()
    )
    direct_dir, proxy_dir = profile_dirs_for_base(base_dir)
    profiles_root = camoufox_profiles_root(base_dir)

    _sched_common: _BrowserSchedulerOptions = {
        "enabled": camoufox_browser_scheduler_enabled,
        "capacity": camoufox_browser_capacity,
        "queue_capacity": camoufox_browser_queue_capacity,
        "queue_timeout_ms": camoufox_queue_timeout_ms,
        "profile_lock_mode": camoufox_profile_lock,
        "profile_lock_ttl_ms": camoufox_profile_lock_ttl_ms,
        "profile_lock_timeout_ms": camoufox_profile_lock_timeout_ms,
        "profile_base_path": profiles_root,
        "redis_gateway": redis_gateway,
    }

    # BrowserScheduler: bounded FIFO queue (Phase 1 / C1 hardening).
    # Each CamoufoxHtmlFetcher instance (direct / proxied) gets its own
    # scheduler so they queue independently (direct doesn't block proxy).
    _scheduler = _build_browser_scheduler(**_sched_common)

    common: dict[str, Any] = {
        "headless": camoufox_headless,
        "humanize": camoufox_humanize,
        "timeout_ms": camoufox_timeout_ms,
        "launch_timeout_ms": camoufox_launch_timeout_ms,
        "settle_ms": camoufox_settle_ms,
        "max_settle_attempts": camoufox_max_settle_attempts,
        "disable_coop": camoufox_disable_coop,
        "warmup_origin": camoufox_warmup_origin,
        "challenge_resolver": challenge_resolver,
        "warm_reuse": camoufox_warm_reuse,
        "warm_max_fetches": camoufox_warm_max_fetches,
        "scheduler_enabled": camoufox_browser_scheduler_enabled,
    }
    direct = CamoufoxHtmlFetcher(
        **common,
        proxy_url=None,
        user_data_dir=direct_dir,
        warmup_policy="once_per_session",
        early_stop_on_shopee_get_pc=True,
        fetch_strategy="camoufox-direct",
        scheduler=_scheduler,
    )
    proxied: CamoufoxHtmlFetcher | None = None
    if camoufox_proxy_url:
        # Proxied fetcher gets its own independent scheduler instance.
        _proxy_scheduler = _build_browser_scheduler(**_sched_common)
        proxied = CamoufoxHtmlFetcher(
            **common,
            proxy_url=camoufox_proxy_url,
            user_data_dir=proxy_dir,
            warmup_policy=shopee_warmup_policy,
            block_resource_types=(
                PROXY_COST_BLOCKED_RESOURCE_TYPES
                if shopee_resource_blocking_enabled
                else ()
            ),
            early_stop_on_shopee_get_pc=True,
            fetch_strategy="camoufox-proxy",
            scheduler=_proxy_scheduler,
        )
    browser = StoreAwareHtmlFetcher(direct=direct, proxied=proxied, http=http)
    # Amazon HTTP leg only (non-Amazon never hits this fetcher).
    amazon_http = UrllibHtmlFetcher(
        user_agent=_AMAZON_HTTP_USER_AGENT,
        timeout=urllib_timeout,
    )
    amazon_first = AmazonHttpFirstHtmlFetcher(http=amazon_http, browser=browser)

    # Mercado Livre: curl_cffi TLS impersonation → Camoufox (+ proxy FALLBACK).
    from .curl_cffi_fetcher import CurlCffiHtmlFetcher
    from .mercadolivre_http_first_fetcher import MercadoLivreHttpFirstHtmlFetcher

    ml_http = CurlCffiHtmlFetcher(timeout=float(urllib_timeout))
    ml_first = MercadoLivreHttpFirstHtmlFetcher(http=ml_http, browser=amazon_first)

    # Pichau: curl_cffi SSR/RSC flight → Camoufox fallback (challenge / miss).
    from .pichau_http_first_fetcher import PichauHttpFirstHtmlFetcher

    pichau_http = CurlCffiHtmlFetcher(timeout=float(urllib_timeout))
    pichau_first = PichauHttpFirstHtmlFetcher(http=pichau_http, browser=ml_first)

    # KaBuM: urllib HTTP for Next.js SERP/PDP (__NEXT_DATA__) → Camoufox fallback.
    from .kabum_http_first_fetcher import KabumHttpFirstHtmlFetcher

    kabum_first = KabumHttpFirstHtmlFetcher(http=http, browser=pichau_first)

    # TerabyteShop: curl_cffi SSR (JSON-LD + accordion) → Camoufox fallback.
    from .terabyteshop_http_first_fetcher import TerabyteShopHttpFirstHtmlFetcher

    terabyte_http = CurlCffiHtmlFetcher(timeout=float(urllib_timeout))
    return TerabyteShopHttpFirstHtmlFetcher(http=terabyte_http, browser=kabum_first)
