"""Deep-dive Magento markers in saved Nissei HTML."""

from __future__ import annotations

import html as html_lib
import json
import re
from collections import Counter
from pathlib import Path

OUT = Path("data/nissei_variant_diag")


def main() -> None:
    for i, label in enumerate(["A", "B"]):
        text = (OUT / f"page_{i}.html").read_text(encoding="utf-8")
        print("=" * 40, label)

        types = []
        for tag in re.findall(r"<script([^>]*)>", text, flags=re.I):
            m = re.search(r"""type=["']([^"']+)["']""", tag, flags=re.I)
            if m:
                types.append(m.group(1))
        print("script types", Counter(types).most_common(12))

        for m in re.finditer("jsonConfig", text):
            print(
                "jsonConfig at", m.start(), repr(text[m.start() - 40 : m.start() + 100])
            )

        for m in re.finditer("option-selected", text):
            print(
                "option-selected at",
                m.start(),
                repr(text[max(0, m.start() - 80) : m.start() + 120]),
            )

        for needle in (
            "[data-role=swatch-options]",
            "#product_addtocart_form",
            "swatch-opt-",
            "data-mage-init",
            "Magento_Swatches/js/swatch-renderer",
            "Magento_ConfigurableProduct/js/configurable",
        ):
            print(needle, text.find(needle))

        for m in re.finditer(r"""data-mage-init=(["'])(.*?)\1""", text, flags=re.S):
            body = html_lib.unescape(m.group(2))
            lower = body.casefold()
            if not any(k in lower for k in ("swatch", "jsonconfig", "configurable")):
                continue
            path = OUT / f"mage_init_{i}.json"
            path.write_text(body, encoding="utf-8")
            print("data-mage-init saved", path, "len", len(body))
            try:
                data = json.loads(body)
                print("mage-init top keys", list(data.keys())[:20])
                # dump nested jsonConfig if present
                blob = json.dumps(data)
                if "jsonConfig" in blob:
                    print("contains jsonConfig")
            except json.JSONDecodeError as exc:
                print("mage-init json fail", exc)
                print(body[:400])

        # Also unescape and search inside script tags for large JSON
        for m in re.finditer(
            r"<script[^>]*type=[\"']text/x-magento-init[\"'][^>]*>(.*?)</script>",
            text,
            flags=re.I | re.S,
        ):
            body = m.group(1).strip()
            if "jsonConfig" in body or "swatch" in body.casefold():
                print("x-magento-init len", len(body), "head", body[:120])

        for m in re.finditer(r"""data-product-id=["']?(\d+)""", text):
            print("data-product-id", m.group(1))
            break
        for m in re.finditer(r"""data-product-sku=["']([^"']+)""", text):
            print("data-product-sku", m.group(1))
            break

        prices = re.findall(r"""data-price-amount=["']([^"']+)""", text)
        print("price amounts sample", prices[:10])

        sku_val = re.search(
            r"""itemprop=["']sku["'][^>]*>\s*([^<]+)""", text, flags=re.I
        )
        if sku_val:
            print("sku text", sku_val.group(1).strip())

        for m in re.finditer(
            r"""class=["'][^"']*swatch-option[^"']*selected[^"']*["'][^>]{0,200}""",
            text,
            flags=re.I,
        ):
            print("swatch selected class", m.group(0)[:220])

        for m in re.finditer(r"""aria-checked=["']true["'][^>]{0,160}""", text):
            print("aria-checked", m.group(0)[:180])

        # Look near memoria_interna for selected value text
        idx = text.find('data-attribute-code="memoria_interna"')
        if idx >= 0:
            print("memoria_interna window", text[idx : idx + 800])
        idx = text.find('data-attribute-code="color"')
        if idx >= 0:
            print("color window", text[idx : idx + 800])

        # selected option label via .swatch-attribute-selected-option
        for m in re.finditer(
            r'class=["\']swatch-attribute-selected-option["\'][^>]*>(.*?)<',
            text,
            flags=re.I | re.S,
        ):
            print("selected-option label", m.group(1).strip())


if __name__ == "__main__":
    main()
