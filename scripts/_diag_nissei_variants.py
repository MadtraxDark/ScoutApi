"""Diagnose Nissei selected-variant sources for configurable PDPs."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from scrapy.http import HtmlResponse  # noqa: F401 — kept for type clarity

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.paraguay.nissei import NisseiSpider

URLS = [
    "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual",
    "https://nissei.com/br/samsung-galaxy-s25-ultra-sm-s938bz-ds-5g-dual-256-gb-black-titanium-1",
]
OUT = Path("data/nissei_variant_diag")
MARKERS = (
    "jsonConfig",
    "spConfig",
    "swatch-opt",
    'data-role="swatch-options"',
    "option-selected",
    "x-magento-init",
    "Magento_Swatches",
    "Magento_ConfigurableProduct",
    "selectedProduct",
    "hasVariant",
    "ProductGroup",
    "data-product-id",
    "swatch-attribute",
)


def _snip(text: str, needle: str, radius: int = 400) -> str | None:
    idx = text.find(needle)
    if idx < 0:
        return None
    start = max(0, idx - 80)
    end = min(len(text), idx + radius)
    return text[start:end]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    fetcher = get_shared_html_fetcher()
    spider = NisseiSpider()
    report: list[dict] = []

    for i, url in enumerate(URLS):
        entry: dict = {"url": url}
        print(f"SCRAPE {url}", flush=True)
        t0 = time.time()
        try:
            item = scrape.scrape(url, include_images=False)
            entry["scrape"] = {
                "secs": round(time.time() - t0, 1),
                "title": item.title,
                "product_id": item.product_id,
                "sku": item.sku,
                "url": item.url,
                "canonical_url": item.canonical_url,
                "price": str(item.price) if item.price is not None else None,
                "currency": item.currency,
                "available": item.available,
                "availability": item.availability,
                "brand": item.brand,
                "model": item.model,
                "variant": item.variant,
                "gtin": item.gtin,
                "specifications": item.specifications,
                "metadata": item.metadata,
            }
            print(json.dumps(entry["scrape"], ensure_ascii=False)[:1500], flush=True)
        except Exception as exc:  # noqa: BLE001 — diagnostic
            entry["scrape"] = {
                "secs": round(time.time() - t0, 1),
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }
            print("SCRAPE_ERR", entry["scrape"], flush=True)

        print(f"RAW {url}", flush=True)
        t0 = time.time()
        try:
            response = fetcher.fetch(url)
            text = response.text or ""
            page_path = OUT / f"page_{i}.html"
            page_path.write_text(text, encoding="utf-8")
            entry["html"] = {
                "secs": round(time.time() - t0, 1),
                "len": len(text),
                "final_url": response.url,
                "path": str(page_path),
                "markers": {m: (m in text) for m in MARKERS},
            }
            configs = []
            for m in re.finditer(
                r'"jsonConfig"\s*:\s*(\{.*?\})\s*(?:,\s*"|"|\})',
                text,
                re.S,
            ):
                configs.append(m.group(1)[:200])
            sp = re.search(r"spConfig\s*[:=]\s*(\{)", text)
            entry["html"]["spConfig_found"] = bool(sp)
            entry["html"]["jsonConfig_samples"] = configs[:3]
            entry["html"]["x_magento_snip"] = _snip(text, "x-magento-init", 800)
            entry["html"]["swatch_snip"] = _snip(text, "swatch-attribute", 600)
            entry["html"]["json_ld_snip"] = _snip(
                text, '"@type":"Product"', 600
            ) or _snip(text, '"@type": "Product"', 600)
            try:
                parsed = spider.parse_product(response)
                entry["current_parse"] = {
                    "title": parsed.title,
                    "product_id": parsed.product_id,
                    "sku": parsed.sku,
                    "brand": parsed.brand,
                    "model": parsed.model,
                    "variant": parsed.variant,
                    "gtin": parsed.gtin,
                    "price": str(parsed.price) if parsed.price is not None else None,
                    "availability": parsed.availability,
                    "canonical_url": parsed.canonical_url,
                    "specifications": parsed.specifications,
                    "metadata": parsed.metadata,
                }
            except Exception as exc:  # noqa: BLE001
                entry["current_parse"] = {
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            print(
                "html_len",
                entry["html"]["len"],
                "markers",
                {k: v for k, v in entry["html"]["markers"].items() if v},
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            entry["html"] = {
                "secs": round(time.time() - t0, 1),
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }
            print("RAW_ERR", entry["html"], flush=True)

        report.append(entry)
        (OUT / f"entry_{i}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print("DONE", OUT / "report.json", flush=True)


if __name__ == "__main__":
    main()
