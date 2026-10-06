"""Scan chunk files for Next.js Server Action IDs and searchProducts."""

import re
from pathlib import Path

base = Path(r"memory\working\_visaovip_serp_probe")

for name in sorted(base.glob("chunk_*.body")):
    text = name.read_text(encoding="utf-8", errors="replace")
    print(f"=== {name.name} len={len(text)} ===")

    # createServerReference("actionId", ...)
    hits = re.findall(r'createServerReference\("([^"]{20,})"\)', text)
    if hits:
        print("  createServerReference IDs:", hits[:10])

    # Look for searchProducts
    idx = text.find("searchProducts")
    if idx > 0:
        print("  FOUND searchProducts at idx", idx)
        snippet = text[max(0, idx - 300) : idx + 600]
        print(snippet)

    # hex-like IDs (potential action IDs) - 40+ hex chars
    hex_ids = re.findall(r'"([0-9a-f]{40,})"', text)
    if hex_ids:
        print("  hex-like IDs:", hex_ids[:5])

    # callServer references
    cs = re.findall(r".{0,60}callServer.{0,100}", text)
    if cs:
        print("  callServer usages:", cs[:3])

    print()
