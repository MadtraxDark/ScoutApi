"""Debug Nissei page B specs markup."""

from __future__ import annotations

import re
from pathlib import Path

from scrapy.http import HtmlResponse, Request

text = Path("data/nissei_variant_diag/page_1.html").read_text(encoding="utf-8")
for needle in (
    "product-attribute-specs-table",
    "additional-attributes",
    "data-th",
    "Memoria Interna",
    "Black Titanium",
    "product-info-main",
    "swatch-attribute",
):
    print(needle, text.find(needle), text.count(needle))

idx = text.find("Memoria Interna")
print("memoria context", repr(text[idx - 250 : idx + 250]) if idx >= 0 else None)
idx = text.find("Black Titanium")
print("black context", repr(text[idx - 250 : idx + 150]) if idx >= 0 else None)

for m in re.finditer(r'class=["\']([^"\']*spec[^"\']*)["\']', text, flags=re.I):
    print("spec class", m.group(1)[:160], "at", m.start())
    if m.start() > 100000:
        break

url = "https://nissei.com/br/x"
resp = HtmlResponse(
    url, body=text.encode("utf-8"), encoding="utf-8", request=Request(url)
)
print("tables", len(resp.css("table")))
print("tr count", len(resp.css("table tr")))
print(
    "th sample",
    [t.strip() for t in resp.css("table th::text").getall()[:20] if t.strip()],
)
print("product-info-main", bool(resp.css(".product-info-main")))
print("specs table css", bool(resp.css(".product-attribute-specs-table")))
# Try alternate selectors Magento uses
for sel in (
    "#product-attribute-specs-table",
    ".additional-attributes-wrapper table",
    "table#product-attribute-specs-table",
    "[data-role='content'] table",
    ".product.data.items table",
):
    rows = resp.css(f"{sel} tr")
    print(sel, len(rows))
