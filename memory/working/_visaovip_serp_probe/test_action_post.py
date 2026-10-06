"""Quick HTTP POST test for searchProducts action."""

import json

import httpx

BASE_URL = "https://www.visaovip.com"
ACTION_ID = "7f674263c13a9d8d28d0768c8016b1791b8051502a"

# Test for B650M
test_cases = [
    ("b650m", "asus-tuf-gaming-b650m-e-wifi", "asus-tuf-gaming-b650m-e-wifi"),
    ("s25", "samsung-galaxy-s25-ultra", "samsung-galaxy-s25-ultra"),
]

for query_id, slug, search_term in test_cases:
    post_url = f"{BASE_URL}/busca/termo/{slug}/"
    # Next.js Server Action JSON payload: [searchTerm, type, filters, locale, page,
    # perPage, stock]
    payload = json.dumps(
        [search_term, "termo", [], "pt-BR", 1, 24, "all"],
        ensure_ascii=False,
    ).encode("utf-8")

    print(f"\n=== [{query_id}] POST {post_url} ===")
    print(f"Payload: {payload.decode()[:200]}")

    with httpx.Client(timeout=20, follow_redirects=False) as client:
        resp = client.post(
            post_url,
            content=payload,
            headers={
                ("User-Agent"): (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                ),
                "Next-Action": ACTION_ID,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component,*/*",
                "Origin": BASE_URL,
                "x-intlayer-locale": "pt-BR",
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
        )

    print(f"Status: {resp.status_code}")
    print(f"Content-Type: {resp.headers.get('content-type', '')}")
    print(f"x-nextjs-cache: {resp.headers.get('x-nextjs-cache', '')}")
    print(f"Response len: {len(resp.text)}")
    print(f"Response body: {resp.text[:500]!r}")

    import re

    count_m = re.search(r'"totalCount":(\d+)', resp.text)
    if count_m:
        print(f"totalCount: {count_m.group(1)}")
    prod_m = re.search(r'"productCode":"?(\d+)"?', resp.text)
    if prod_m:
        print(f"first productCode: {prod_m.group(1)}")
