"""Probe Nissei HTML for child IDs and Magento config endpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path("data/nissei_variant_diag")


def main() -> None:
    text = (OUT / "page_0.html").read_text(encoding="utf-8")
    for needle in ("1613992", "136659", "PC-134391", "1609955", "6016", "33354"):
        positions = [m.start() for m in re.finditer(re.escape(needle), text)]
        print(needle, "count", len(positions), "first", positions[:5])
        if positions:
            p = positions[0]
            print(" ", repr(text[max(0, p - 60) : p + 80]))

    # Look for script src related to product
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', text, flags=re.I)
    productish = [
        s
        for s in srcs
        if any(k in s.lower() for k in ("product", "swatch", "configurable", "catalog"))
    ]
    print("productish scripts", productish[:20])

    # Common Magento endpoints to try (may 403)
    product_id = "1609955"
    endpoints = [
        f"https://nissei.com/br/swatches/ajax/media/?product_id={product_id}",
        f"https://nissei.com/rest/V1/products/{product_id}",
        f"https://nissei.com/rest/default/V1/products/{product_id}",
        f"https://nissei.com/br/rest/V1/configurable-products/{product_id}/children",
        "https://nissei.com/graphql",
    ]
    for url in endpoints:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
            with urlopen(req, timeout=15) as resp:
                body = resp.read(300)
                print("OK", resp.status, url, body[:120])
        except Exception as exc:  # noqa: BLE001
            print("ERR", url, type(exc).__name__, exc)

    # Specs table full for page A and B
    for i in (0, 1):
        page = (OUT / f"page_{i}.html").read_text(encoding="utf-8")
        print("=" * 40, "specs", i)
        rows = re.findall(
            r"<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
            page,
            flags=re.I | re.S,
        )
        for th, td in rows[:40]:
            key = re.sub(r"<[^>]+>", "", th).strip()
            val = re.sub(r"<[^>]+>", "", td).strip()
            if key:
                print(f"  {key}: {val[:100]}")

    # For page B: look for manufacturer model / SM-S938
    page_b = (OUT / "page_1.html").read_text(encoding="utf-8")
    for m in re.finditer(r"SM-S938[^\s<\"']*", page_b):
        print("mpn hit B", m.group(0))
        break
    page_a = (OUT / "page_0.html").read_text(encoding="utf-8")
    for m in re.finditer(r"SM-S938[^\s<\"']*", page_a):
        print("mpn hit A", m.group(0))
        break

    # Save contentsWithIds block
    m = re.search(r"contentsWithIds\s*=\s*(\{.*?\});", text, flags=re.S)
    if m:
        raw = m.group(1)
        print("contentsWithIds raw", raw)
        try:
            print(json.loads(raw))
        except json.JSONDecodeError as exc:
            print("parse fail", exc)


if __name__ == "__main__":
    main()
