"""Baseline probe: TerabyteShop PDP HTTP (curl_cffi) vs raw signals. No Camoufox yet."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

URL_TRACKED = (
    "https://www.terabyteshop.com.br/produto/22809/"
    "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5"
    "?gad_source=1&gad_campaignid=16136003025"
    "&gbraid=0AAAAADm8AXRS6u1KR_rAi0F51-DZzOkJl"
    "&gclid=Cj0KCQjwlNPVBhCMARIsAPZ5Rqh06PioCAB9wgCmShJcBfjqhRNNzisS90x87SNGZR5c76BWkY7VWdAaApsqEALw_wcB"
)
URL_CLEAN = (
    "https://www.terabyteshop.com.br/produto/22809/"
    "placa-mae-gigabyte-b650m-aorus-elite-chipset-b650-amd-am5-atx-ddr5"
)

OUT = Path(__file__).resolve().parent / "_terabyte_probe"


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _signals(
    html: str, *, status: int, final_url: str, bytes_n: int, ms: float
) -> dict:
    lower = html.casefold()
    title = _title(html)
    return {
        "status": status,
        "final_url": final_url,
        "title": title[:200],
        "bytes": bytes_n,
        "duration_ms": round(ms, 1),
        "challenge_just_a_moment": "just a moment" in title.casefold()
        or "just a moment" in lower[:8000],
        "cloudflare_marker": "cloudflare" in lower[:20000],
        "challenge_platform": "challenge-platform" in lower,
        "cf_challenge": "cf-challenge" in lower,
        "json_ld_product": bool(
            re.search(r'"@type"\s*:\s*"Product"', html)
            or re.search(r'"@type"\s*:\s*\[\s*"Product"', html)
        ),
        "application_ld_json": "application/ld+json" in lower,
        "price_in_jsonld": bool(re.search(r'"price"\s*:\s*"?[0-9]', html)),
        "h1_present": bool(re.search(r"<h1[\s>]", html, re.I)),
        "produto_path": "/produto/" in (final_url or "").casefold(),
        "specs_tableish": any(
            token in lower
            for token in (
                "especifica",
                "ficha técnica",
                "ficha tecnica",
                "technical-spec",
                "product-spec",
            )
        ),
        "img_count_guess": len(re.findall(r"<img[\s>]", html, re.I)),
        "inline_price_js": bool(
            re.search(r"['\"]price['\"]\s*:\s*[0-9]+(?:\.[0-9]+)?", html)
        ),
        "countdown_ctd": bool(re.search(r"countdown\(", html, re.I)),
        "head_snippet": html[:500].replace("\n", " ")[:500],
    }


def curl_cffi_fetch(url: str) -> dict:
    from curl_cffi import requests as curl_requests

    t0 = time.perf_counter()
    resp = curl_requests.get(
        url,
        impersonate="chrome",
        headers={
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        },
        timeout=45,
        allow_redirects=True,
    )
    ms = (time.perf_counter() - t0) * 1000
    body = resp.content or b""
    text = body.decode("utf-8", errors="replace")
    label = "tracked" if "gclid" in url else "clean"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"curl_cffi_{label}.html").write_text(text, encoding="utf-8")
    sig = _signals(
        text,
        status=int(resp.status_code),
        final_url=str(resp.url),
        bytes_n=len(body),
        ms=ms,
    )
    sig["label"] = label
    sig["host"] = urlparse(str(resp.url)).hostname
    return sig


def main() -> None:
    results = {
        "curl_cffi_tracked": curl_cffi_fetch(URL_TRACKED),
        "curl_cffi_clean": curl_cffi_fetch(URL_CLEAN),
    }
    out_path = OUT / "http_baseline.json"
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
