"""Check the current content of the chunk vs what was captured."""

import re

import httpx

BASE_URL = "https://www.visaovip.com"
CHUNK = "/_next/static/chunks/5304c8ab31d35f98.js"

with httpx.Client(timeout=20) as client:
    r = client.get(f"{BASE_URL}{CHUNK}", headers={"User-Agent": "Mozilla/5.0"})
    text = r.text

print("Status:", r.status_code, "len:", len(text))
print("searchProducts in text:", "searchProducts" in text)
print("createServerReference in text:", "createServerReference" in text)
print("searchFacets in text:", "searchFacets" in text)

# Print first 500 and last 200 chars
print("\nFirst 500 chars:")
print(text[:500])
print("\nLast 200 chars:")
print(text[-200:])

# Look for any hex IDs
hex_ids = re.findall(r'"([0-9a-f]{40,})"', text)
print("\nhex IDs:", hex_ids[:5])
