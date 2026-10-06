"""Check if the known chunk is still accessible and has the same action IDs."""

import re

import httpx

BASE_URL = "https://www.visaovip.com"

# Known chunk from the probe files (captured 2026-09-23)
KNOWN_CHUNK = "/_next/static/chunks/5304c8ab31d35f98.js"

ACTION_ID_RE = re.compile(r'createServerReference\("([0-9a-f]{40,})"')

print(f"Checking: {BASE_URL}{KNOWN_CHUNK}")
with httpx.Client(timeout=20) as client:
    r = client.get(f"{BASE_URL}{KNOWN_CHUNK}", headers={"User-Agent": "Mozilla/5.0"})
    print(f"Status: {r.status_code}")
    print(f"Content-Length: {len(r.text)}")
    if r.status_code == 200:
        ids = ACTION_ID_RE.findall(r.text)
        print(f"Action IDs found: {ids}")
        if "searchProducts" in r.text:
            print("CONFIRMED: searchProducts in this chunk")
        if "searchFacets" in r.text:
            print("CONFIRMED: searchFacets in this chunk")

# Try the buildManifest approach
# For Next.js, we can try fetching /_next/static/<buildId>/_buildManifest.js
# but we need the buildId first

# Alternative: check if the Turbopack-related manifest has a stable location
# The build ID is typically in __NEXT_DATA__ — let's check a different URL
print("\n=== Checking if raw SERP HTML contains build ID via different endpoint ===")
# Try the RSC payload directly (if available)
with httpx.Client(timeout=20, follow_redirects=True) as client:
    # Try fetching with RSC headers
    r2 = client.get(
        f"{BASE_URL}/busca/termo/asus-tuf-gaming-b650m-e-wifi/",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Rsc": "1",
            ("Next-Router-State-Tree"): (
                "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE_"
                "_%22%2C%7B%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
            ),
            "Accept": "text/x-component",
        },
    )
    print(f"RSC endpoint status: {r2.status_code}")
    print(f"RSC response len: {len(r2.text)}")
    ct = r2.headers.get("content-type", "")
    print(f"Content-Type: {ct}")
    if "x-component" in ct:
        print("RSC response sample:", r2.text[:300])
