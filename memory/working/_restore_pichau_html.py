"""Restore full Pichau HTML from CDP dump."""

from __future__ import annotations

import json
from pathlib import Path

src = Path(
    r"C:\Users\Thyago\.cursor\browser-logs"
    r"\cdp-response-Runtime.evaluate-2026-09-23T01-38-15-781Z.json"
)
out = Path("data/_pichau_ryzen_live.html")
html = json.loads(src.read_text(encoding="utf-8"))["result"]["value"]
out.write_text(html, encoding="utf-8")
esc = '\\"product\\":'
print("rewrote bytes", out.stat().st_size, "chars", len(html))
print("next_f", html.count("__next_f"))
print("pichau_prices", html.count("pichau_prices"))
print("escaped_product_colon", html.count(esc))
print("challenge?", "Just a moment" in html[:400])
