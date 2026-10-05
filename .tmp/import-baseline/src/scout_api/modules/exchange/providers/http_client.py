"""Shared httpx.Client factory for exchange-rate providers.

URLs are hardcoded in each provider — never user-supplied (anti-SSRF).
"""

from __future__ import annotations

import httpx

from scout_api.core.config import get_settings

_USER_AGENT = "ScoutApiV2-ExchangeRate/0.1 (+price-monitoring)"


def build_http_client(*, timeout: float | None = None) -> httpx.Client:
    """Create a configured httpx.Client for exchange-rate fetching.

    Uses a short timeout appropriate for lightweight public endpoints.
    No proxies — all exchange-rate sources are public and not blocked.
    """
    settings = get_settings()
    effective_timeout = timeout if timeout is not None else settings.exchange_rate_http_timeout_seconds
    return httpx.Client(
        timeout=effective_timeout,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
        follow_redirects=True,
    )
