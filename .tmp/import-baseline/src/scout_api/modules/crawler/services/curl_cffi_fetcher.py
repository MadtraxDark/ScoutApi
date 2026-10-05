"""HTTP fetcher with browser-like TLS/HTTP2 fingerprint (curl_cffi)."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from scrapy.http import HtmlResponse, Request

from scout_api.core.performance import OperationCategory, RetryLedger

from ..core.exceptions import RequestError
from ..core.retry import backoff_delay
from .html_fetcher import (
    accept_language_for_url,
    is_auth_wall_page,
    is_challenge_page,
    is_mercadolivre_snoopy_challenge,
)

logger = logging.getLogger(__name__)

_DEFAULT_IMPERSONATE = "chrome"


class CurlCffiHtmlFetcher:
    """Lightweight HTTP fetch that impersonates a real browser TLS fingerprint.

    Used for Mercado Livre HTTP-first (ADR 0025). Retries transient failures with
    exponential backoff + jitter. Challenge / soft-block HTML is returned to the
    caller so progressive wrappers can escalate to Camoufox; hard 403/429 raise
    ``RequestError``.
    """

    def __init__(
        self,
        *,
        impersonate: str = _DEFAULT_IMPERSONATE,
        timeout: float = 30.0,
        max_retries: int = 3,
        base_delay: float = 0.75,
        max_delay: float = 12.0,
        rng: random.Random | None = None,
    ) -> None:
        self._impersonate = impersonate
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._rng = rng or random.Random()

    def fetch(self, url: str) -> HtmlResponse:
        last_error: Exception | None = None
        attempts = self._max_retries + 1
        ledger = RetryLedger(
            operation="curl_cffi_fetch",
            category=OperationCategory.HTTP_REQUEST,
        )
        pending_backoff_ms = 0.0
        for attempt in range(attempts):
            if attempt > 0:
                delay = backoff_delay(
                    attempt - 1,
                    base=self._base_delay,
                    cap=self._max_delay,
                    rng=self._rng,
                )
                pending_backoff_ms = delay * 1000
                # Small human-like pause even on first retry.
                time.sleep(delay)
            ledger.begin_attempt()
            try:
                response = self._fetch_once(url)
                ledger.end_attempt(
                    outcome="success",
                    backoff_ms=pending_backoff_ms,
                )
                pending_backoff_ms = 0.0
                if len(ledger.attempts) > 1:
                    ledger.observe(stage="http", extra={"host": _host_of(url)})
                return response
            except RequestError as exc:
                last_error = exc
                will_retry = bool(exc.retryable) and attempt < attempts - 1
                ledger.end_attempt(
                    outcome="retry" if will_retry else "failed",
                    code=exc.code,
                    backoff_ms=pending_backoff_ms,
                )
                pending_backoff_ms = 0.0
                if not will_retry:
                    ledger.observe(stage="http", extra={"host": _host_of(url)})
                    raise
                logger.info(
                    "curl_cffi_retry",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        "code": exc.code,
                        **ledger.as_context(),
                    },
                )
            except Exception as exc:
                last_error = exc
                will_retry = attempt < attempts - 1
                ledger.end_attempt(
                    outcome="retry" if will_retry else "failed",
                    code="UPSTREAM_NETWORK_ERROR",
                    backoff_ms=pending_backoff_ms,
                )
                pending_backoff_ms = 0.0
                if not will_retry:
                    ledger.observe(stage="http", extra={"host": _host_of(url)})
                    raise RequestError(
                        f"Falha ao buscar URL com curl_cffi: {exc}",
                        code="UPSTREAM_NETWORK_ERROR",
                        url=url,
                        retryable=True,
                    ) from exc
                logger.info(
                    "curl_cffi_retry_network",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        **ledger.as_context(),
                    },
                )
        assert last_error is not None
        raise last_error

    def _fetch_once(self, url: str) -> HtmlResponse:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise RequestError(
                "curl_cffi não está instalado (pip install curl_cffi)",
                code="UPSTREAM_REQUEST_ERROR",
                url=url,
                retryable=False,
            ) from exc

        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": accept_language_for_url(url),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            response = curl_requests.get(
                url,
                impersonate=self._impersonate,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=True,
            )
        except Exception as exc:
            raise RequestError(
                "Não foi possível conectar à loja (curl_cffi)",
                code="UPSTREAM_NETWORK_ERROR",
                url=url,
                retryable=True,
            ) from exc

        status = int(getattr(response, "status_code", 0) or 0)
        final_url = str(getattr(response, "url", None) or url)
        body = getattr(response, "content", None)
        if body is None:
            text_body = str(getattr(response, "text", "") or "")
            body = text_body.encode("utf-8", errors="replace")
        if not isinstance(body, (bytes, bytearray)):
            body = bytes(body)

        if status in {403, 429}:
            raise RequestError(
                f"A loja recusou a requisição (HTTP {status})",
                code="UPSTREAM_BLOCKED",
                url=final_url,
                upstream_status=status,
                retryable=status == 429 or status == 403,
            )
        if status >= 500:
            raise RequestError(
                f"A loja retornou erro HTTP {status}",
                code="UPSTREAM_HTTP_ERROR",
                url=final_url,
                upstream_status=status,
                retryable=True,
            )
        if status >= 400:
            raise RequestError(
                f"A loja recusou a requisição (HTTP {status})",
                code="UPSTREAM_HTTP_ERROR",
                url=final_url,
                upstream_status=status,
                retryable=False,
            )

        text = body.decode("utf-8", errors="replace")
        # Soft blocks (Snoopy / account-verification) often return HTTP 200 —
        # return body so progressive wrappers escalate to Camoufox + bypass.
        if is_mercadolivre_snoopy_challenge(text) or is_challenge_page(text):
            html_response = HtmlResponse(
                url=final_url,
                status=status,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=body,
                encoding="utf-8",
                request=Request(final_url),
            )
            html_response.meta["fetch_metrics"] = {
                "fetch_strategy": "curl-cffi-direct",
                "browser_used": False,
                "proxy_used": False,
                "soft_challenge": True,
            }
            return html_response
        if is_auth_wall_page(text, url=final_url):
            # ML account-verification: escalate to Camoufox session bypass
            # (credential-free). Other stores still raise AUTH_REQUIRED.
            host = _host_of(final_url).casefold()
            if "mercadolivre." in host or "mercadolibre." in host:
                html_response = HtmlResponse(
                    url=final_url,
                    status=status,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=body,
                    encoding="utf-8",
                    request=Request(final_url),
                )
                html_response.meta["fetch_metrics"] = {
                    "fetch_strategy": "curl-cffi-direct",
                    "browser_used": False,
                    "proxy_used": False,
                    "soft_auth_wall": True,
                }
                return html_response
            raise RequestError(
                "A loja bloqueou a requisição (auth wall)",
                code="AUTH_REQUIRED",
                url=final_url,
                upstream_status=401,
                retryable=True,
            )

        html_response = HtmlResponse(
            url=final_url,
            status=status,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=body,
            encoding="utf-8",
            request=Request(final_url),
        )
        metrics: dict[str, Any] = {
            "fetch_strategy": "curl-cffi-direct",
            "browser_used": False,
            "proxy_used": False,
        }
        html_response.meta["fetch_metrics"] = metrics
        return html_response


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).netloc or "")[:120]
