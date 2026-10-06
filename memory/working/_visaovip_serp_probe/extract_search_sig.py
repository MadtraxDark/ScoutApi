"""Extract searchProducts call signature and payload structure from chunk_serp3."""

import re
from pathlib import Path

base = Path(r"memory\working\_visaovip_serp_probe")
text = (base / "chunk_serp3.body").read_text(encoding="utf-8", errors="replace")

# Find the searchProducts context more broadly
idx = text.find("searchProducts")
print("=== searchProducts context ===")
print(text[max(0, idx - 500) : idx + 3000])

print("\n=== await p( usages ===")
for m in re.finditer(r"await\s+p\([^)]{0,400}\)", text):
    print(m.group(0)[:500])
    print("---")

print("\n=== p( call with search-like args ===")
for m in re.finditer(r"\bp\([\s\{][^)]{0,600}\)", text):
    s = m.group(0)
    if any(k in s for k in ["page", "termo", "search", "filter", "perPage", "slug"]):
        print(s[:600])
        print("---")
