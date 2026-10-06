"""Probe public Pichau API endpoints discovered in live PDP (no invented auth)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

HTML = Path("data/_pichau_ryzen_live.html").read_text(encoding="utf-8")
OUT = Path("memory/working/_pichau_api_probes.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

build = re.search(r'"buildId"\s*:\s*"([^"]+)"', HTML)
build_manifest = re.search(r"/_next/static/([^/]+)/_buildManifest", HTML)
print("buildId", build.group(1) if build else None)
print("buildManifest", build_manifest.group(1) if build_manifest else None)

slug = (
    "processador-amd-ryzen-7-5800x3d-8-core-16-threads-3-4ghz-4-5ghz-turbo"
    "-cache-100mb-am4-100-100000651pof"
)
endpoints = [
    "https://www.pichau.com.br/api/request/openbox?id=66151",
    "https://www.pichau.com.br/api/request/product-review-v2?id=66151&page=1&rating=0&search=&sort=DESC&withMedia=false",
    "https://www.pichau.com.br/api/request/installments?cartSubtotal=2517.64&cartTotal=2517.64&total=2517.64&productId=66151&discountBy=product",
]
if build_manifest:
    bid = build_manifest.group(1)
    endpoints.append(f"https://www.pichau.com.br/_next/data/{bid}/{slug}.json")

results = []
for url in endpoints:
    entry: dict = {"url": url}
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — operator evidence fetch
            body = resp.read()
            entry["status"] = getattr(resp, "status", None) or resp.getcode()
            entry["content_type"] = resp.headers.get("Content-Type")
            entry["bytes"] = len(body)
            text = body.decode("utf-8", errors="replace")
            entry["body_head"] = text[:500]
            if "json" in (entry["content_type"] or "").lower() or text[:1] in "{[":
                try:
                    data = json.loads(text)
                    entry["json_keys"] = (
                        list(data.keys())[:30]
                        if isinstance(data, dict)
                        else type(data).__name__
                    )
                except Exception as exc:  # noqa: BLE001
                    entry["json_err"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)
        code = getattr(getattr(exc, "code", None), "__str__", lambda: None)()
        if hasattr(exc, "code"):
            entry["status"] = exc.code
    print(json.dumps(entry, ensure_ascii=False)[:400])
    results.append(entry)

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", OUT)
