"""Analyze saved Nissei HTML for Magento selected-variant sources."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.spiders.paraguay.nissei import NisseiSpider

OUT = Path("data/nissei_variant_diag")


def walk(obj: object, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            p = f"{path}.{key}" if path else str(key)
            if key in {
                "jsonConfig",
                "spConfig",
                "attributes",
                "optionPrices",
                "images",
                "index",
                "skus",
                "productId",
                "defaultValues",
                "selectedProductId",
            }:
                preview = (
                    f"len={len(value)}"
                    if isinstance(value, (dict, list))
                    else str(value)[:120]
                )
                print("FOUND", p, type(value).__name__, preview)
            if key == "jsonConfig" and isinstance(value, dict):
                print("  jsonConfig keys", list(value.keys())[:40])
                attrs = value.get("attributes")
                if isinstance(attrs, dict):
                    for aid, ainfo in attrs.items():
                        if not isinstance(ainfo, dict):
                            continue
                        opts = ainfo.get("options") or []
                        print(
                            "  attr",
                            aid,
                            ainfo.get("code"),
                            ainfo.get("label"),
                            "options",
                            len(opts) if isinstance(opts, list) else type(opts),
                        )
                        if isinstance(opts, list):
                            for opt in opts[:8]:
                                if not isinstance(opt, dict):
                                    continue
                                print(
                                    "    opt",
                                    opt.get("id"),
                                    opt.get("label"),
                                    "products",
                                    (opt.get("products") or [])[:6],
                                )
                print("  defaultValues", value.get("defaultValues"))
                print("  productId", value.get("productId"))
                index = value.get("index")
                if isinstance(index, dict):
                    print("  index sample", list(index.items())[:4])
                skus = value.get("skus")
                if isinstance(skus, dict):
                    print("  skus sample", list(skus.items())[:4])
                else:
                    print("  skus", skus)
                prices = value.get("optionPrices")
                if isinstance(prices, dict):
                    print("  optionPrices sample", list(prices.items())[:2])
            walk(value, p)
    elif isinstance(obj, list) and len(obj) < 80:
        for i, value in enumerate(obj):
            walk(value, f"{path}[{i}]")


def main() -> None:
    for i, label in enumerate(["A_no_storage_slug", "B_with_storage_slug"]):
        text = (OUT / f"page_{i}.html").read_text(encoding="utf-8")
        print("=" * 60, label, "len", len(text))

        for sel in re.findall(
            r"<link[^>]+rel=[\"']canonical[\"'][^>]*>", text, flags=re.I
        ):
            print("canonical_tag", sel[:220])

        m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        print("title", (m.group(1).strip()[:140] if m else None))
        m = re.search(r'class=["\']base["\'][^>]*>(.*?)<', text, flags=re.I | re.S)
        print("h1", (m.group(1).strip()[:140] if m else None))

        for m in re.finditer(r"selectedProduct.{0,220}", text):
            print("selectedProduct", m.group(0)[:220])
            break

        for m in re.finditer(r"option-selected=[\"']?([^\"'\s>]+)", text):
            print("option-selected", m.group(0)[:160])

        for m in re.finditer(
            r'data-attribute-code=["\']([^"\']+)["\'][^>]{0,240}', text
        ):
            print("attr-code", m.group(0)[:240])

        for m in re.finditer(
            r"<script[^>]*type=[\"']text/x-magento-init[\"'][^>]*>(.*?)</script>",
            text,
            flags=re.I | re.S,
        ):
            body = m.group(1).strip()
            lower = body.casefold()
            if not any(k in lower for k in ("swatch", "jsonconfig", "configurable")):
                continue
            path = OUT / f"xmagento_{i}.json"
            path.write_text(body, encoding="utf-8")
            print("x-magento-init saved", path, "len", len(body))
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                print("json parse fail", exc)
                print(body[:400])
                continue
            print("top keys", list(data.keys())[:12])
            walk(data)

        for m in re.finditer(
            r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
            text,
            flags=re.I | re.S,
        ):
            raw = m.group(1).strip()
            try:
                ld = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items = ld if isinstance(ld, list) else [ld]
            for it in items:
                if not isinstance(it, dict):
                    continue
                t = it.get("@type")
                types = t if isinstance(t, list) else [t]
                if not any(x in {"Product", "ProductGroup"} for x in types):
                    continue
                print(
                    "JSON-LD",
                    t,
                    "name",
                    str(it.get("name"))[:90],
                    "sku",
                    it.get("sku"),
                    "mpn",
                    it.get("mpn"),
                    "gtin",
                    it.get("gtin13") or it.get("gtin") or it.get("gtin14"),
                    "color",
                    it.get("color"),
                    "model",
                    it.get("model"),
                )
                offers = it.get("offers")
                if isinstance(offers, dict):
                    print(
                        "  offer",
                        offers.get("price"),
                        offers.get("availability"),
                        str(offers.get("url"))[:120],
                    )

        # Specs table keys of interest
        for label_name in (
            "Color",
            "Colour",
            "Cor",
            "Almacenamiento",
            "Storage",
            "Memoria",
            "Capacidad",
            "Modelo",
            "Model",
            "UPC",
            "EAN",
            "SKU",
        ):
            pattern = (
                rf"<th[^>]*>\s*{re.escape(label_name)}\s*</th>\s*<td[^>]*>(.*?)</td>"
            )
            hit = re.search(pattern, text, flags=re.I | re.S)
            if hit:
                val = re.sub(r"<[^>]+>", "", hit.group(1)).strip()
                print("spec", label_name, val[:80])

        url = f"https://nissei.com/br/diag-{i}"
        response = HtmlResponse(
            url, body=text.encode("utf-8"), encoding="utf-8", request=Request(url)
        )
        item = NisseiSpider().parse_product(response)
        print(
            "PARSE",
            {
                "title": item.title,
                "product_id": item.product_id,
                "sku": item.sku,
                "brand": item.brand,
                "model": item.model,
                "variant": item.variant,
                "gtin": item.gtin,
                "price": str(item.price),
                "availability": item.availability,
                "canonical_url": item.canonical_url,
                "metadata": item.metadata,
            },
        )


if __name__ == "__main__":
    main()
