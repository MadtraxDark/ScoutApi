"""Baseline Visão VIP PDP pricing + Match (pre-change). Do not hardcode product URLs."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from scout_api.modules.crawler.core.scrape_guard import ScrapeGuard
from scout_api.modules.crawler.services.offer_scrape_service import OfferScrapeService
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.spiders.paraguay.visaovip import VisaoVipSpider
from scout_api.modules.matching.store_search_service import StoreSearchService

OUT = Path("memory/working/visaovip_pricing_baseline.json")
MPN_QUERY = "AX4U32008G16A-SBKD35G"
TITLE_HINT = "Spectrix D35G"
GT_MARKER = "/41749/"


def _item_snapshot(item) -> dict:
    return {
        "title": getattr(item, "title", None),
        "brand": getattr(item, "brand", None),
        "model": getattr(item, "model", None),
        "variant": getattr(item, "variant", None),
        "product_id": getattr(item, "product_id", None),
        "sku": getattr(item, "sku", None),
        "gtin": getattr(item, "gtin", None),
        "canonical_url": getattr(item, "canonical_url", None),
        "currency": getattr(item, "currency", None),
        "price": str(item.price) if getattr(item, "price", None) is not None else None,
        "pix_price": (
            str(item.pix_price)
            if getattr(item, "pix_price", None) is not None
            else None
        ),
        "original_price": (
            str(item.original_price)
            if getattr(item, "original_price", None) is not None
            else None
        ),
        "discount_percentage": (
            str(item.discount_percentage)
            if getattr(item, "discount_percentage", None) is not None
            else None
        ),
        "installment_price": getattr(item, "installment_price", None),
        "installment_count": getattr(item, "installment_count", None),
        "seller": getattr(item, "seller", None),
        "available": getattr(item, "available", None),
        "specifications": getattr(item, "specifications", None),
        "description": (getattr(item, "description", None) or "")[:200] or None,
        "images_count": len(getattr(item, "images", None) or []),
        "images": list(getattr(item, "images", None) or [])[:8],
        "metadata": getattr(item, "metadata", None),
    }


def _audit_html(html: str) -> dict:
    spider = VisaoVipSpider()
    blob = spider._flight_blob(html)
    product = spider._parse_product_object(blob)
    keys = sorted(product.keys()) if product else []
    money_keys = [
        k
        for k in keys
        if re.search(r"price|promo|currency|cambio|rate|iva|pix", k, re.I)
    ]
    body_has = {
        "U$": "U$" in html or "U\\$" in html,
        "G$": "G$" in html or "G\\$" in html,
        "R$": "R$" in html or "R\\$" in html,
        "Pix": bool(re.search(r"\bPix\b", html, re.I)),
        "product:price:amount": "product:price:amount" in html,
        "product:price:currency": "product:price:currency" in html,
    }
    og_amount = re.search(
        r'property=["\']product:price:amount["\']\s+content=["\']([^"\']+)',
        html,
        re.I,
    )
    og_currency = re.search(
        r'property=["\']product:price:currency["\']\s+content=["\']([^"\']+)',
        html,
        re.I,
    )
    g_match = re.search(r"G\$\s*([\d.]+)", html)
    r_match = re.search(r"R\$\s*([\d.,]+)", html)
    u_match = re.search(r"U\$\s*([\d.,]+)", html)
    return {
        "product_keys": keys,
        "money_related_keys": money_keys,
        "product_price": product.get("productPrice") if product else None,
        "product_promotion_price": product.get("productPromotionPrice")
        if product
        else None,
        "is_product_promotion": product.get("isProductPromotion") if product else None,
        "product_subset": {
            k: product.get(k)
            for k in keys
            if k
            in {
                "productCode",
                "productName",
                "productPrice",
                "productPromotionPrice",
                "isProductPromotion",
                "manufactureName",
                "currency",
                "currencyCode",
                "exchangeRate",
                "dolarRate",
                "guaraniRate",
                "realRate",
                "priceGuarani",
                "priceReal",
                "priceUSD",
            }
            or re.search(r"price|rate|currency|guarani|real|dolar", k, re.I)
        }
        if product
        else {},
        "body_markers": body_has,
        "og_price_amount": og_amount.group(1) if og_amount else None,
        "og_price_currency": og_currency.group(1) if og_currency else None,
        "html_U$": u_match.group(0) if u_match else None,
        "html_G$": g_match.group(0) if g_match else None,
        "html_R$": r_match.group(0) if r_match else None,
    }


def main() -> None:
    get_shared_html_fetcher.cache_clear()
    guard = ScrapeGuard(
        url_cooldown_seconds=0,
        domain_min_interval_seconds=1,
        result_cache_ttl_seconds=0,
    )
    scrape = ProductScrapeService(guard=guard)
    offer_svc = OfferScrapeService(guard=guard)
    search = StoreSearchService()

    payload: dict = {"phase": "baseline", "mpn_query": MPN_QUERY}

    t0 = time.perf_counter()
    candidates = search.search("visaovip", MPN_QUERY, limit=8)
    search_ms = round((time.perf_counter() - t0) * 1000)
    payload["search"] = {
        "ms": search_ms,
        "count": len(candidates),
        "candidates": [
            {
                "title": c.title,
                "url": c.url,
                "product_id": c.product_id,
            }
            for c in candidates
        ],
    }

    chosen = None
    for c in candidates:
        title = (c.title or "").casefold()
        url = (c.url or "").casefold()
        if (
            TITLE_HINT.casefold() in title
            or MPN_QUERY.casefold() in title
            or MPN_QUERY.casefold() in url
        ):
            chosen = c
            break
    if chosen is None and candidates:
        chosen = candidates[0]
    payload["chosen_candidate"] = (
        {"title": chosen.title, "url": chosen.url, "product_id": chosen.product_id}
        if chosen
        else None
    )

    if chosen and chosen.url:
        t1 = time.perf_counter()
        offer = offer_svc.scrape_offer(chosen.url)
        offer_ms = round((time.perf_counter() - t1) * 1000)
        payload["crawl_offer"] = {"ms": offer_ms, **_item_snapshot(offer)}

        t2 = time.perf_counter()
        full_no_img = scrape.scrape(chosen.url, include_images=False)
        full_no_img_ms = round((time.perf_counter() - t2) * 1000)
        payload["crawl_full_no_images"] = {
            "ms": full_no_img_ms,
            **_item_snapshot(full_no_img),
        }

        t3 = time.perf_counter()
        full_img = scrape.scrape(chosen.url, include_images=True)
        full_img_ms = round((time.perf_counter() - t3) * 1000)
        payload["crawl_full_with_images"] = {
            "ms": full_img_ms,
            **_item_snapshot(full_img),
        }

        fetcher = get_shared_html_fetcher()
        raw = fetcher.fetch(chosen.url)
        payload["pdp_audit"] = {
            "http_status": getattr(raw, "status", None),
            "final_url": str(raw.url),
            "html_len": len(raw.text or ""),
            **_audit_html(raw.text or ""),
        }

    # Lightweight Match baseline: SERP for board only (full match is expensive).
    t4 = time.perf_counter()
    board_cands = search.search("visaovip", "ASUS TUF Gaming B650M-E WIFI", limit=8)
    board_ms = round((time.perf_counter() - t4) * 1000)
    payload["match_serp_baseline"] = {
        "query": "ASUS TUF Gaming B650M-E WIFI",
        "ms": board_ms,
        "count": len(board_cands),
        "gt_in_serp": any(GT_MARKER in (c.url or "") for c in board_cands),
        "candidates": [
            {"title": c.title, "url": c.url, "product_id": c.product_id}
            for c in board_cands
        ],
    }

    OUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(
        json.dumps(
            {"wrote": str(OUT), "chosen": payload.get("chosen_candidate")}, indent=2
        )
    )
    print("gt_in_serp", payload["match_serp_baseline"]["gt_in_serp"])
    if payload.get("crawl_full_no_images"):
        c = payload["crawl_full_no_images"]
        print(
            "price",
            c.get("price"),
            c.get("currency"),
            "pix",
            c.get("pix_price"),
            "orig",
            c.get("original_price"),
            "sku",
            c.get("sku"),
        )


if __name__ == "__main__":
    main()
