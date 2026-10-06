"""Fetch Pichau Ryzen PDP, decode Next.js flight, dump Magento product blob."""

from __future__ import annotations

import json
import re
from pathlib import Path

URL = (
    "https://www.pichau.com.br/processador-amd-ryzen-7-5800x3d-8-core-16-threads"
    "-3-4ghz-4-5ghz-turbo-cache-100mb-am4-100-100000651pof"
)
OUT_HTML = Path("data/_pichau_ryzen_live.html")
OUT_JSON = Path("memory/working/pichau-ryzen-product-blob.json")

_NEXT_FLIGHT_PUSH = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
    re.DOTALL,
)
_SENSITIVE = re.compile(
    r"(cookie|token|authorization|password|secret|session|csrf|jwt|refresh|"
    r"access_token|set-cookie)",
    re.I,
)
_INTERESTING = (
    "socket",
    "core",
    "thread",
    "cache",
    "clock",
    "freq",
    "barra",
    "mpn",
    "ean",
    "upc",
    "gtin",
    "sku",
    "codigo",
    "atribut",
    "spec",
    "marca",
    "brand",
    "model",
    "serie",
    "am4",
    "am5",
    "tdp",
    "nucle",
    "boost",
    "turbo",
    "fabricante",
    "ghz",
    "mhz",
)
_MPN_LIKE = (
    "mpn",
    "ean",
    "upc",
    "gtin",
    "manufacturer",
    "part_number",
    "partnumber",
    "codigo_barra",
    "barcode",
    "isbn",
)


def decode_js_string(body: str) -> str:
    try:
        decoded = json.loads(f'"{body}"')
    except json.JSONDecodeError:
        return (
            body.replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
            .replace(r"\\", "\\")
        )
    return decoded if isinstance(decoded, str) else body


def flight_blob(html: str) -> tuple[str, int]:
    matches = list(_NEXT_FLIGHT_PUSH.finditer(html))
    blob = "\n".join(decode_js_string(m.group(1)) for m in matches)
    return blob, len(matches)


def extract_product(blob: str) -> dict:
    for match in re.finditer(r'"product"\s*:\s*\{', blob):
        start = match.end() - 1
        depth = 0
        end = start
        for i, ch in enumerate(blob[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            value = json.loads(blob[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and (
            value.get("pichau_prices") or value.get("sku") or value.get("id")
        ):
            return value
    return {}


def sanitize(obj: object) -> object:
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if _SENSITIVE.search(str(k)):
                continue
            out[k] = sanitize(v)
        return out
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    return obj


def collect_extra_attrs(clean: dict) -> dict:
    extra: dict = {}
    for container_key in (
        "custom_attributes",
        "attributes",
        "atributos",
        "specs",
        "specifications",
        "additional_attributes",
    ):
        c = clean.get(container_key)
        if isinstance(c, dict):
            for k, v in c.items():
                if v not in (None, "", [], {}):
                    extra[f"{container_key}.{k}"] = v
        elif isinstance(c, list):
            for item in c:
                if not isinstance(item, dict):
                    continue
                code = item.get("attribute_code") or item.get("code") or item.get("key")
                val = item.get("value") if "value" in item else item.get("valor")
                if code and val not in (None, "", [], {}):
                    extra[f"{container_key}.{code}"] = val
    return extra


def fetch_html() -> tuple[str, int]:
    try:
        from curl_cffi import requests as creq

        print("using curl_cffi", flush=True)
        r = creq.get(URL, impersonate="chrome", timeout=60)
        return r.text or "", int(r.status_code)
    except Exception as exc:  # noqa: BLE001
        print(f"curl_cffi failed ({exc!r}); falling back to requests", flush=True)
        import requests

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        r = requests.get(URL, headers=headers, timeout=60)
        return r.text or "", int(r.status_code)


def main() -> None:
    html, status = fetch_html()
    print(f"status={status} chars={len(html)}", flush=True)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"saved {OUT_HTML}", flush=True)

    blob, push_count = flight_blob(html)
    markers = len(list(re.finditer(r'"product"\s*:\s*\{', blob)))
    print(
        f"flight_push_count={push_count} blob_size={len(blob)} "
        f"product_markers={markers}",
        flush=True,
    )

    product = extract_product(blob)
    clean = sanitize(product)
    assert isinstance(clean, dict)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved {OUT_JSON} bytes={OUT_JSON.stat().st_size}", flush=True)

    extra_attrs = collect_extra_attrs(clean)
    non_null_interesting = sorted(
        {
            k
            for k, v in clean.items()
            if v not in (None, "", [], {}) and any(s in k.lower() for s in _INTERESTING)
        }
    )
    mpn_like: dict = {}
    for k, v in clean.items():
        if v not in (None, "", [], {}) and any(x in k.lower() for x in _MPN_LIKE):
            mpn_like[k] = v
    for k, v in extra_attrs.items():
        if any(x in k.lower() for x in _MPN_LIKE):
            mpn_like[k] = v

    media = clean.get("media_gallery") or clean.get("media_gallery_entries") or []
    cats = clean.get("categories") or clean.get("category") or clean.get("category_ids")

    # Non-null scalar/list attribute-looking keys (socket, cores, etc.)
    attr_values = {
        k: clean[k]
        for k in non_null_interesting
        if k
        not in {
            "sku",
            "marcas_info",
            "pichau_prices",
            "codigo_barra",
            "categories",
            "media_gallery",
            "media_gallery_entries",
        }
    }

    summary = {
        "flight_decode_works": bool(product.get("pichau_prices") or product.get("sku")),
        "flight_push_count": push_count,
        "flight_blob_size": len(blob),
        "sku": clean.get("sku"),
        "id": clean.get("id"),
        "name": clean.get("name"),
        "marcas_info": clean.get("marcas_info"),
        "pichau_prices": clean.get("pichau_prices"),
        "codigo_barra": clean.get("codigo_barra"),
        "categories": cats,
        "media_gallery_count": len(media) if isinstance(media, list) else media,
        "non_null_attribute_keys": non_null_interesting,
        "attribute_values": attr_values,
        "extra_attr_sample": {k: extra_attrs[k] for k in list(extra_attrs)[:40]},
        "mpn_like_fields": mpn_like,
        "top_level_key_count": len(clean),
        "top_level_keys_sample": sorted(clean.keys())[:100],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
