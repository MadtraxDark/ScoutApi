"""One-off probe: TerabyteShop PDP 24707 price signals (no production edits)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scrapy.http import HtmlResponse

from scout_api.modules.crawler.services.curl_cffi_fetcher import CurlCffiHtmlFetcher
from scout_api.modules.crawler.spiders.brazil.terabyteshop import TerabyteShopSpider

URL = (
    "https://www.terabyteshop.com.br/produto/24707/"
    "placa-mae-gigabyte-a520m-k-v2-chipset-a520-amd-am4-matx-ddr4"
)
OUT = Path(__file__).resolve().parent
FULL = OUT / "24707_full.html"
BOX = OUT / "24707_price_box.html"

_JQ_TEXT_RE = re.compile(
    r"""\$\(\s*['"](?P<sel>\.[^'"]+)['"]\s*\)\.text\(\s*['"](?P<val>[^'"]+)['"]\s*\)""",
    re.I,
)


def _collapse(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def main() -> None:
    if FULL.exists() and FULL.stat().st_size > 1000:
        html = FULL.read_text(encoding="utf-8")
        url = URL
        status = "cached"
    else:
        fetcher = CurlCffiHtmlFetcher(impersonate="chrome", timeout=45.0, max_retries=2)
        resp = fetcher.fetch(URL)
        html = resp.text or ""
        url = resp.url
        status = resp.status
        FULL.write_text(html, encoding="utf-8")

    print("STATUS", status)
    print("URL", url)
    print("HTML_LEN", len(html))
    print(
        "CLOUDFLARE",
        "just a moment" in html.casefold()
        or "cf-browser-verification" in html.casefold(),
    )

    from parsel import Selector

    sel = Selector(text=html)

    parts: list[str] = []
    seen: set[str] = set()
    for css in ("#topopreco", ".info-price", "p.precode", ".precotopo"):
        for chunk in sel.css(css).getall():
            key = chunk[:240]
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"<!-- {css} -->\n{chunk}")
    price_box = (
        "\n\n".join(parts)
        if parts
        else "<!-- EMPTY: no price box selectors matched -->"
    )
    BOX.write_text(price_box, encoding="utf-8")
    print("PRICE_BOX_LEN", len(price_box), "PARTS", len(parts))

    signals: dict = {}

    def t(css: str) -> str | None:
        return _collapse(sel.css(css).get())

    def h(css: str) -> str | None:
        nodes = sel.css(css).getall()
        return nodes[0] if nodes else None

    signals["#valVista text"] = t("#valVista::text")
    signals["p.valVista text"] = t("p.valVista::text")
    signals["#valVista html"] = h("#valVista")
    signals["#valParc text"] = t("#valParc::text")
    signals["span.valParc text"] = t("span.valParc::text")
    signals["#valParc html"] = h("#valParc")
    signals["#Parc text"] = t("#Parc::text")
    signals["#nParc text"] = t("#nParc::text")
    signals["#Parc html"] = h("#Parc")
    signals["#nParc html"] = h("#nParc")
    signals["p.precode del"] = t("p.precode del::text")
    signals[".precode del"] = t(".precode del::text")
    signals["#topopreco del"] = t("#topopreco del::text")
    signals[".info-price del"] = t(".info-price del::text")
    signals["p.precode html"] = h("p.precode")

    hay = "\n".join(sel.css("#topopreco, .info-price, p.precode, .precotopo").getall())
    signals["De R$ in price box"] = re.findall(
        r"(?:de)\s*:?\s*(?:<[^>]+>\s*)*R\$\s*([0-9\.\,]+)",
        hay,
        flags=re.I,
    )

    # Surrounding text near valVista / valParc for Pix labels
    for nid in ("valVista", "valParc", "topopreco"):
        idx = html.casefold().find(f'id="{nid.lower()}"')
        if idx < 0:
            idx = html.casefold().find(f"id='{nid.lower()}'")
        if idx >= 0:
            window = html[max(0, idx - 300) : idx + 400]
            signals[f"window around {nid}"] = _collapse(window)

    ld_prices = []
    for script in sel.css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(script)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t_ = item.get("@type")
            types = t_ if isinstance(t_, list) else [t_]
            if not any(str(x).casefold() == "product" for x in types if x):
                continue
            offers = item.get("offers")
            if isinstance(offers, list):
                for o in offers:
                    if isinstance(o, dict):
                        ld_prices.append(
                            {
                                k: o.get(k)
                                for k in (
                                    "price",
                                    "priceCurrency",
                                    "availability",
                                    "@type",
                                )
                            }
                        )
            elif isinstance(offers, dict):
                ld_prices.append(
                    {
                        k: offers.get(k)
                        for k in ("price", "priceCurrency", "availability", "@type")
                    }
                )
    signals["json-ld Product.offers"] = ld_prices

    jq_hits = []
    for m in _JQ_TEXT_RE.finditer(html):
        seln = m.group("sel")
        if any(
            x in seln.casefold()
            for x in ("valparc", "val-prod", "precode", "valvista", "nparc", "parc")
        ):
            jq_hits.append({"sel": seln, "val": m.group("val")})
    signals["jquery .text assigns (price-related)"] = jq_hits

    broad = []
    for kind, needle in (
        ("valParc", ".valParc"),
        ("val-prod", ".val-prod"),
        ("precode", ".precode"),
        ("valVista", ".valVista"),
        ("Parc", ".Parc"),
        ("nParc", ".nParc"),
    ):
        for m in re.finditer(
            re.escape("$(")
            + r"""\s*['"]"""
            + re.escape(needle)
            + r"""['"]\s*\)\.[a-zA-Z]+\([^;]{0,100}""",
            html,
            flags=re.I,
        ):
            broad.append({"kind": kind, "snippet": m.group(0)[:140]})
    signals["jquery broad snippets"] = broad

    label_bits = []
    for css in ("#topopreco", ".info-price"):
        node = sel.css(css).get() or ""
        folded = node.casefold()
        for marker in (
            "pix",
            "à vista",
            "a vista",
            "boleto",
            "cartão",
            "cartao",
            "parcel",
            "de ",
            "por ",
        ):
            if marker in folded:
                label_bits.append(f"{css} contains {marker!r}")
    signals["label markers"] = label_bits

    print("=== SIGNALS ===")
    print(json.dumps(signals, ensure_ascii=False, indent=2, default=str))

    response = HtmlResponse(
        url=url,
        body=html.encode("utf-8"),
        encoding="utf-8",
        status=200,
    )
    spider = TerabyteShopSpider()
    offer = spider.extract_offer(response)
    result = {
        "price": str(offer.price) if offer.price is not None else None,
        "pix_price": str(offer.pix_price) if offer.pix_price is not None else None,
        "original_price": (
            str(offer.original_price) if offer.original_price is not None else None
        ),
        "installment_count": offer.installment_count,
        "installment_price": (
            str(offer.installment_price)
            if offer.installment_price is not None
            else None
        ),
        "discount_percentage": (
            str(offer.discount_percentage)
            if offer.discount_percentage is not None
            else None
        ),
        "available": offer.available,
        "availability": offer.availability,
        "metadata.source": offer.metadata.get("source") if offer.metadata else None,
        "metadata.pricing": offer.metadata.get("pricing") if offer.metadata else None,
    }
    print("=== SPIDER ===")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    (OUT / "24707_signals.json").write_text(
        json.dumps(
            {"signals": signals, "spider": result}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
