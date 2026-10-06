"""Inspect Nissei selectedProduct JS and swatch container markup."""

from __future__ import annotations

import re
from pathlib import Path

OUT = Path("data/nissei_variant_diag")


def main() -> None:
    for i, label in enumerate(["A", "B"]):
        text = (OUT / f"page_{i}.html").read_text(encoding="utf-8")
        print("=" * 50, label)

        for m in re.finditer("selectedProductId", text):
            print("--- selectedProductId context ---")
            print(text[max(0, m.start() - 300) : m.start() + 500])
            print("---")

        for m in re.finditer(r'id="(swatch-opt-\d+)"', text):
            start = m.start()
            # find closing roughly
            _end = text.find("</div>", start + 50)
            # expand to larger region: next 3k chars
            chunk = text[start : start + 4000]
            print("swatch-opt chunk head:\n", chunk[:1500])
            # look for data-* attributes on the container and parents
            # go backwards for opening parent with data attributes
            back = text[max(0, start - 2000) : start]
            print("swatch-opt preceding:\n", back[-800:])

        # Find AJAX / REST URLs related to product config
        urls = set(
            re.findall(
                r"https?://nissei\.com[^\"'\s<>]+",
                text,
            )
        )
        interesting = [
            u
            for u in urls
            if any(
                k in u.lower()
                for k in (
                    "swatch",
                    "configurable",
                    "variant",
                    "rest/",
                    "graphql",
                    "ajax",
                    "product",
                    "catalog",
                )
            )
        ]
        print("interesting urls sample", sorted(interesting)[:40])

        # Relative API paths
        paths = set(
            re.findall(
                r"[\"'](/[^\"']*(?:swatch|configurable|variant|graphql|rest/V1)[^\"']*)[\"']",
                text,
                flags=re.I,
            )
        )
        print("api-ish paths", sorted(paths)[:40])

        # Look for require([... config
        for m in re.finditer(
            r"require\(\s*\[[^\]]*(?:swatch|configurable)[^\]]*\]\s*,\s*function",
            text,
            flags=re.I,
        ):
            print("require block at", m.start(), text[m.start() : m.start() + 200])

        # Amasty / Mageplaza / custom: look for 'SpConfig' or hash configs
        for needle in (
            "jsonSwatchConfig",
            "swatchConfig",
            "configurableStatus",
            "updateProductData",
            "amasty",
            "mageworx",
            "weltpixel",
            "ajaxCart",
            "/catalog/product/",
            "getProductInfo",
            "variant-switch",
        ):
            c = text.casefold().count(needle.casefold())
            if c:
                print("needle", needle, c)

        # Extract all data-option-label from swatches
        labels = re.findall(r'data-option-label="([^"]+)"', text)
        print("option labels", labels[:30], "total", len(labels))

        # Check if URL query params or hash preselect
        # Also look for preconfigured values in JS variables
        for m in re.finditer(
            r"preconfigured|preSelected|defaultSelected|initialSelected",
            text,
            flags=re.I,
        ):
            print("preselect-ish", text[m.start() : m.start() + 120])


if __name__ == "__main__":
    main()
