"""Deeper Terabyte HTML extraction map (specs, pix, gallery, product JS)."""

from __future__ import annotations

import re
from pathlib import Path

html = Path("memory/working/_terabyte_probe/curl_cffi_clean.html").read_text(
    encoding="utf-8", errors="replace"
)
out = Path("memory/working/_terabyte_probe")
out.mkdir(parents=True, exist_ok=True)

# --- Specs accordion panels ---
panels = re.findall(
    r'<div class="panel panel-default">(.*?)</div>\s*</div>\s*</div>',
    html,
    re.I | re.S,
)
print("panels approx", len(panels))

# More reliable: find panel-title spans and following panel-body
titles = re.findall(
    r'class="panel-title".*?<span>(.*?)</span>',
    html,
    re.I | re.S,
)
print("panel titles:", [re.sub(r"\s+", " ", t).strip() for t in titles[:20]])

# Look for label/value pairs in especificacoes section
espec_start = html.casefold().find('class="especificacoes')
espec_chunk = html[espec_start : espec_start + 40000] if espec_start >= 0 else ""
print("espec chunk len", len(espec_chunk))

# Common Magento patterns: <strong>Label</strong> value, or dt/dd, or li
strongs = re.findall(
    r"<strong[^>]*>([^<]{2,80})</strong>\s*([^<]{1,200})",
    espec_chunk,
    re.I,
)
print("strong pairs", len(strongs))
for a, b in strongs[:30]:
    print("  ", a.strip(), "=", b.strip()[:120])

# li patterns
lis = re.findall(r"<li[^>]*>(.*?)</li>", espec_chunk, re.I | re.S)
print("lis", len(lis))
for li in lis[:40]:
    text = re.sub(r"<[^>]+>", " ", li)
    text = re.sub(r"\s+", " ", text).strip()
    if text and len(text) < 200:
        print("  li:", text)

# dt/dd
dtdd = re.findall(
    r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>",
    espec_chunk,
    re.I | re.S,
)
print("dt/dd", len(dtdd))

# table rows
rows = re.findall(r"<tr[^>]*>(.*?)</tr>", espec_chunk, re.I | re.S)
print("tr", len(rows))
for row in rows[:20]:
    cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.I | re.S)
    cells = [re.sub(r"<[^>]+>", " ", c) for c in cells]
    cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
    if any(cells):
        print("  row:", cells)

# --- Price / Pix DOM ---
# Find money near pix
for m in re.finditer(r".{0,80}pix.{0,80}", html, re.I):
    snip = re.sub(r"\s+", " ", m.group(0))
    if "R$" in snip or "r$" in snip.casefold() or any(ch.isdigit() for ch in snip):
        print("pix ctx:", snip[:160])

# Installments
for m in re.finditer(r".{0,40}\d+\s*x\s*(?:de\s*)?R\$.{0,40}", html, re.I):
    print("parcel:", re.sub(r"\s+", " ", m.group(0))[:160])

# --- Gallery images ---
imgs = re.findall(
    r'(?:data-zoom-image|data-src|src)=["\'](https?://img\.terabyteshop\.com\.br/[^"\']+)',
    html,
    re.I,
)
uniq = []
seen = set()
for u in imgs:
    if u not in seen:
        seen.add(u)
        uniq.append(u)
print("terabyte img urls", len(uniq))
for u in uniq[:25]:
    print(" ", u)

# --- Inline JS product object ---
# Magento often has: var spConfig / product data
for pat in (
    r"var\s+productId\s*=\s*(\d+)",
    r"product_id['\"]?\s*[:=]\s*['\"]?(\d+)",
    r"['\"]id['\"]\s*:\s*22809",
    r"window\.__NUXT__",
    r"__NEXT_DATA__",
    r"self\.__next_f",
):
    print(pat, "found", bool(re.search(pat, html, re.I)))

# Search for JSON-ish product blobs
for m in re.finditer(r"\{[^{}]{0,40}\"price\"\s*:\s*[0-9.]+[^{}]{0,200}\}", html):
    snip = m.group(0)
    if "22809" in snip or "product" in snip.casefold():
        print("price blob:", snip[:250])

# Save espec HTML for manual review
if espec_chunk:
    (out / "espec_chunk.html").write_text(espec_chunk, encoding="utf-8")
print("wrote espec_chunk.html")
