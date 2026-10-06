"""Debug chunk discovery."""

import re

import httpx

BASE_URL = "https://www.visaovip.com"
slug = "asus-tuf-gaming-b650m-e-wifi"
serp_url = f"{BASE_URL}/busca/termo/{slug}/"
hdrs = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR,pt;q=0.9"}

with httpx.Client(timeout=20, follow_redirects=True) as client:
    r = client.get(serp_url, headers=hdrs)
    html = r.text

# Check build ID
build_m = re.search(r'"b":"([A-Za-z0-9_-]{10,30})"', html)
print("build_id:", build_m.group(1) if build_m else None)
print("html len:", len(html))

# Find chunk scripts
CHUNK_RE = re.compile(r'src="(/_next/static/chunks/[0-9a-f]{16}\.js)"')
chunks = CHUNK_RE.findall(html)
print("total chunks found:", len(chunks))
for c in chunks[:30]:
    print(" ", c)

# Now check which chunks contain searchProducts
ACTION_ID_RE = re.compile(
    'createServerReference\\("([0-9a-f]{40,})",.+?c'
    'allServer.+?,void 0,.+?findSourceMapURL,"(\\w+'
    ')"\\)'
)
print("\n=== Fetching chunks to find searchProducts ===")
with httpx.Client(timeout=20) as client:
    for rel in chunks:
        chunk_url = f"{BASE_URL}{rel}"
        cr = client.get(chunk_url, headers={"User-Agent": "Mozilla/5.0"})
        if cr.status_code != 200:
            continue
        ct = cr.text
        if "searchProducts" in ct:
            print(f"FOUND in {rel}")
            for m in ACTION_ID_RE.finditer(ct):
                print(f"  {m.group(2)}: {m.group(1)}")
            break
