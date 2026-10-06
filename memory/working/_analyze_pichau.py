"""Analyze live Pichau Ryzen PDP HTML (no spider changes)."""

from __future__ import annotations

import json
import re
from pathlib import Path

CDP_JSON = Path(
    r"C:\Users\Thyago\.cursor\browser-logs"
    r"\cdp-response-Runtime.evaluate-2026-09-23T01-38-15-781Z.json"
)
OUT_HTML = Path("data/_pichau_ryzen_live.html")
OUT_SNIPS = Path("memory/working/_pichau_analysis_snips.json")


def main() -> None:
    raw = json.loads(CDP_JSON.read_text(encoding="utf-8"))
    html = raw["result"]["value"]
    OUT_HTML.write_text(html, encoding="utf-8")
    size = OUT_HTML.stat().st_size
    print(f"saved={OUT_HTML} bytes={size} chars={len(html)}")

    terms = [
        "application/ld+json",
        "__next_f",
        "product",
        '\\"product\\"',
        '"product"',
        "avista",
        "à vista",
        "pichau_prices",
        "marcas_info",
        "caracteristicas",
        "sku",
        "100-100000651",
        "100-100000651POF",
        "PIX",
    ]
    counts = {t: html.count(t) for t in terms}
    for t, c in counts.items():
        print(f"COUNT\t{c}\t{t!r}")

    # Escaped RSC blob vs plain
    needles = {
        "escaped_product_colon": '\\"product\\":',
        "escaped_product": '\\"product\\"',
        "plain_product_colon": '"product":',
        "plain_product": '"product"',
        "next_f": "__next_f",
        "pichau_prices": "pichau_prices",
        "marcas_info": "marcas_info",
        "caracteristicas": "caracteristicas",
        "sku_frag": "100-100000651",
        "avista": "avista",
    }
    idxs = {k: html.find(v) for k, v in needles.items()}
    for k, i in idxs.items():
        print(f"IDX\t{k}\t{i}")

    def snip_at(idx: int, label: str, radius: int = 220) -> str | None:
        if idx < 0:
            print(f"SNIP_{label}=NONE")
            return None
        a, b = max(0, idx - radius), min(len(html), idx + radius)
        s = html[a:b].replace("\n", " ")
        print(f"SNIP_{label}=...{s}...")
        return s

    snips: dict[str, str | None] = {}
    for label, _needle in needles.items():
        snips[label] = snip_at(idxs[label], label)

    # Extra: first few \"product\": occurrences count
    esc_colon = html.count('\\"product\\":')
    plain_colon = html.count('"product":')
    print(f"COUNT_escaped_product_colon={esc_colon}")
    print(f"COUNT_plain_product_colon={plain_colon}")

    # JSON-LD
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    )
    print(f"JSONLD_BLOCKS={len(blocks)}")
    jsonld_info: list[dict] = []
    for i, block in enumerate(blocks):
        block = block.strip()
        try:
            data = json.loads(block)
        except Exception as exc:  # noqa: BLE001
            print(f"JSONLD_{i}_PARSE_ERR={exc}")
            print(f"JSONLD_{i}_HEAD={block[:400].replace(chr(10), ' ')}")
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            typ = obj.get("@type")
            entry: dict = {"index": i, "type": typ}
            print(f"JSONLD_{i}_TYPE={typ}")
            if typ == "Product" or (isinstance(typ, list) and "Product" in typ):
                entry["name"] = obj.get("name")
                entry["sku"] = obj.get("sku")
                offers = obj.get("offers")
                if isinstance(offers, dict):
                    entry["price"] = offers.get("price")
                    entry["currency"] = offers.get("priceCurrency")
                    entry["availability"] = offers.get("availability")
                    print(f"JSONLD_{i}_PRICE={offers.get('price')}")
                    print(f"JSONLD_{i}_CURRENCY={offers.get('priceCurrency')}")
                    print(f"JSONLD_{i}_AVAIL={offers.get('availability')}")
                elif isinstance(offers, list):
                    entry["offers"] = [
                        {
                            "price": o.get("price"),
                            "currency": o.get("priceCurrency"),
                        }
                        for o in offers
                        if isinstance(o, dict)
                    ]
                    for j, o in enumerate(entry["offers"]):
                        print(f"JSONLD_{i}_OFFER{j}_PRICE={o.get('price')}")
            jsonld_info.append(entry)

    # avista / pix contexts
    avista_snips = []
    for m in re.finditer(r".{0,90}avista.{0,120}", html, flags=re.I):
        avista_snips.append(m.group(0).replace("\n", " "))
        if len(avista_snips) >= 10:
            break
    print(f"AVISTA_SNIPS={len(avista_snips)}")
    for i, s in enumerate(avista_snips):
        print(f"AVISTA_{i}={s}")

    # price values near pichau_prices blob
    price_near = []
    for m in re.finditer(
        r"pichau_prices.{0,800}",
        html,
        flags=re.I | re.S,
    ):
        chunk = m.group(0).replace("\n", " ")
        nums = re.findall(r"\d+[.,]\d{2}", chunk)
        price_near.append({"chunk": chunk[:500], "nums": nums[:20]})
        print(f"PICHAU_PRICES_CHUNK={chunk[:500]}")
        print(f"PICHAU_PRICES_NUMS={nums[:20]}")
        if len(price_near) >= 3:
            break

    # API endpoints (no inventing auth)
    api_abs = sorted(
        set(
            re.findall(
                r"https?://[^\s\"'<>]+",
                html,
            )
        )
    )
    interesting_abs = [
        u
        for u in api_abs
        if any(
            k in u.lower()
            for k in (
                "graphql",
                "/rest/",
                "magento",
                "_next/data",
                "/v1/",
                "api.",
                "catalog",
            )
        )
        and len(u) < 280
    ][:60]
    print(f"API_ABS={len(interesting_abs)}")
    for u in interesting_abs[:40]:
        print(f"API\t{u}")

    rel = sorted(
        set(
            re.findall(
                r'["\'](/[^"\']*(?:graphql|rest/V1|_next/data|mage|catalog)[^"\']*)["\']',
                html,
                flags=re.I,
            )
        )
    )
    print(f"REL={len(rel)}")
    for u in rel[:40]:
        print(f"REL\t{u}")

    OUT_SNIPS.write_text(
        json.dumps(
            {
                "bytes": size,
                "chars": len(html),
                "counts": counts,
                "indexes": idxs,
                "escaped_product_colon_count": esc_colon,
                "plain_product_colon_count": plain_colon,
                "snips": snips,
                "jsonld": jsonld_info,
                "avista_snips": avista_snips,
                "pichau_prices_near": price_near,
                "api_abs": interesting_abs,
                "rel_endpoints": rel,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote={OUT_SNIPS}")
    print("DONE")


if __name__ == "__main__":
    main()
