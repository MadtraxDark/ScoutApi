#!/usr/bin/env python3
"""One-shot CamoufoxHtmlFetcher smoke (about:blank via light HTTP URL)."""

from __future__ import annotations

import time

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.browser_health import (
    get_browser_circuit,
    reset_browser_circuit_for_tests,
)
from scout_api.modules.crawler.services.html_fetcher import build_html_fetcher


def main() -> None:
    reset_browser_circuit_for_tests()
    settings = get_settings()
    fetcher = build_html_fetcher(
        camoufox_enabled=True,
        user_agent="ScoutApiV2-probe",
        camoufox_headless=True,
        camoufox_humanize=False,
        camoufox_timeout_ms=30_000,
        camoufox_launch_timeout_ms=settings.camoufox_launch_timeout_ms,
        camoufox_settle_ms=500,
        camoufox_max_settle_attempts=2,
        camoufox_proxy_url=None,
        camoufox_user_data_dir=settings.camoufox_user_data_dir,
        camoufox_warmup_origin=False,
        camoufox_warm_reuse=True,
        captcha_solver_enabled=False,
        auth_bypass_enabled=False,
    )
    # Use a store URL that routes to Camoufox (Kabum PDP is heavy); prefer
    # example.com is not in registry — use kabum home which is light-ish.
    url = "https://www.kabum.com.br/"
    t0 = time.perf_counter()
    try:
        resp = fetcher.fetch(url)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        body = getattr(resp, "text", "") or ""
        print(
            {
                "result": "ok",
                "duration_ms": ms,
                "status": getattr(resp, "status", None),
                "body_len": len(body),
                "circuit": get_browser_circuit().snapshot(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        ms = round((time.perf_counter() - t0) * 1000, 1)
        print(
            {
                "result": "error",
                "duration_ms": ms,
                "exc": f"{type(exc).__name__}:{exc}",
                "circuit": get_browser_circuit().snapshot(),
            }
        )


if __name__ == "__main__":
    main()
