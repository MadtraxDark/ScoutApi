"""Force live Terabyte scrape inside API container (bypass cache)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from scrapy.http import HtmlResponse

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.curl_cffi_fetcher import CurlCffiHtmlFetcher
from scout_api.modules.crawler.services.html_fetcher import (
    build_html_fetcher,
    is_challenge_page,
)
from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

URL_TRACKED = (
    "https://www.terabyteshop.com.br/produto/22809/"
    "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5"
    "?gad_source=1&gad_campaignid=16136003025"
    "&gbraid=0AAAAADm8AXRS6u1KR_rAi0F51-DZzOkJl"
    "&gclid=Cj0KCQjwlNPVBhCMARIsAPZ5Rqh06PioCAB9wgCmShJcBfjqhRNNzisS90x87SNGZR5c76BWkY7VWdAaApsqEALw_wcB"
)
URL_CLEAN = (
    "https://www.terabyteshop.com.br/produto/22809/"
    "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5"
)
OUT = Path("/tmp/terabyte_probe")
OUT.mkdir(parents=True, exist_ok=True)


def summarize(response: HtmlResponse, label: str) -> dict:
    text = response.text or ""
    title = ""
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    metrics = dict(response.meta.get("fetch_metrics") or {})
    challenge = is_challenge_page(text, title=title)
    result = {
        "label": label,
        "status": response.status,
        "final_url": response.url,
        "bytes": len(text.encode("utf-8", errors="replace")),
        "title": title[:180],
        "challenge": challenge,
        "fetch_metrics": metrics,
        "has_jsonld_product": '"@type":"Product"' in text
        or '"@type": "Product"' in text,
        "has_price": '"price"' in text,
        "has_h1": bool(response.css("h1").get()),
    }
    spider = TerabyteShopSpider()
    try:
        offer = spider.extract_offer(response)
        details = spider.extract_details(response)
        images = spider.extract_images(response)
        result["parser_ok"] = True
        result["offer"] = offer.model_dump(mode="json")
        result["details"] = details.model_dump(mode="json")
        result["images_count"] = len(images)
        result["images_sample"] = images[:5]
    except Exception as exc:
        result["parser_ok"] = False
        result["parser_error"] = f"{type(exc).__name__}: {exc}"
        if hasattr(exc, "code"):
            result["parser_code"] = getattr(exc, "code", None)
    return result


def main() -> None:
    report: dict = {}

    # 1) curl_cffi direct
    http = CurlCffiHtmlFetcher(timeout=45.0)
    t0 = time.perf_counter()
    http_resp = http.fetch(URL_CLEAN)
    report["http_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    (OUT / "http_clean.html").write_text(http_resp.text or "", encoding="utf-8")
    report["http"] = summarize(http_resp, "curl_cffi")

    # 2) production fetcher chain (Camoufox path for terabyte today)
    settings = get_settings()
    fetcher = build_html_fetcher(
        camoufox_enabled=settings.camoufox_enabled,
        user_agent=settings.scraper_user_agent,
        urllib_timeout=settings.scraper_http_timeout,
        camoufox_headless=settings.camoufox_headless,
        camoufox_humanize=settings.camoufox_humanize,
        camoufox_timeout_ms=settings.camoufox_timeout_ms,
        camoufox_launch_timeout_ms=settings.camoufox_launch_timeout_ms,
        camoufox_settle_ms=settings.camoufox_settle_ms,
        camoufox_max_settle_attempts=settings.camoufox_max_settle_attempts,
        camoufox_proxy_url=settings.camoufox_proxy_url,
        camoufox_user_data_dir=settings.camoufox_user_data_dir,
        camoufox_disable_coop=settings.camoufox_disable_coop,
        camoufox_warmup_origin=settings.camoufox_warmup_origin,
        camoufox_warm_reuse=settings.camoufox_warm_reuse,
        camoufox_warm_max_fetches=settings.camoufox_warm_max_fetches,
        shopee_warmup_policy=settings.shopee_warmup_policy,
        shopee_resource_blocking_enabled=settings.shopee_resource_blocking_enabled,
        captcha_solver_enabled=settings.captcha_solver_enabled,
        captcha_solver_provider=settings.captcha_solver_provider,
        captcha_solver_max_attempts=settings.captcha_solver_max_attempts,
        auth_bypass_enabled=settings.auth_bypass_enabled,
        auth_bypass_max_attempts=settings.auth_bypass_max_attempts,
        amazon_auth_email=settings.amazon_auth_email,
        amazon_auth_password=settings.amazon_auth_password,
        shopee_auth_email=settings.shopee_auth_email,
        shopee_auth_password=settings.shopee_auth_password,
        camoufox_browser_scheduler_enabled=settings.camoufox_browser_scheduler_enabled,
        camoufox_browser_capacity=settings.camoufox_browser_capacity,
        camoufox_browser_queue_capacity=settings.camoufox_browser_queue_capacity,
        camoufox_queue_timeout_ms=settings.camoufox_queue_timeout_ms,
        camoufox_profile_lock=settings.camoufox_profile_lock,
        camoufox_profile_lock_ttl_ms=settings.camoufox_profile_lock_ttl_ms,
        camoufox_profile_lock_timeout_ms=settings.camoufox_profile_lock_timeout_ms,
    )
    t0 = time.perf_counter()
    try:
        browser_resp = fetcher.fetch(URL_TRACKED)
        report["browser_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        (OUT / "browser_tracked.html").write_text(
            browser_resp.text or "", encoding="utf-8"
        )
        report["browser"] = summarize(browser_resp, "production_chain")
    except Exception as exc:
        report["browser_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        report["browser_error"] = f"{type(exc).__name__}: {exc}"
        if hasattr(exc, "code"):
            report["browser_code"] = getattr(exc, "code", None)

    (OUT / "live_compare.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str)[:8000])


if __name__ == "__main__":
    main()
