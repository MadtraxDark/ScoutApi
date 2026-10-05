"""TerabyteShop BR — parse-only adapter with timed campaign countdown."""

from __future__ import annotations

import html as html_lib
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from scrapy.http import Response

from ...core.exceptions import MissingPriceError, ParseError, RequestError
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.parsing import parse_money
from ...utils.product_attributes import (
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ...utils.timed_promotion import terabyte_promotion_from_html
from ..base import BaseStoreSpider

logger = logging.getLogger(__name__)

Availability = Literal["available", "out_of_stock", "unavailable"]

_PRODUCT_ID_RE = re.compile(r"/produto/(\d+)", re.I)
_TITLE_STORE_SUFFIX_RE = re.compile(r"\s*\|\s*Terabyte\s*$", re.I)
_SPEC_PAIR_RE = re.compile(
    r"<p>\s*<strong>\s*([^:<]{1,80})\s*:?\s*</strong>\s*(?:<br\s*/?>)?\s*(.*?)\s*</p>",
    re.I | re.S,
)
_GALLERY_IMG_RE = re.compile(
    r"""(?:data-zoom-image|data-src|src)=["'](https?://img\.terabyteshop\.com\.br/produto/g/[^"']+)["']""",
    re.I,
)
_NPARC_RE = re.compile(r"""id=["']nParc["'][^>]*>\s*(\d+)\s*x?""", re.I)
_PARC_RE = re.compile(
    r"""id=["']Parc["'][^>]*>\s*R\$\s*([0-9\.\,]+)""",
    re.I,
)
_JQ_TEXT_RE = re.compile(
    r"""\$\(\s*['"](?P<sel>\.[^'"]+)['"]\s*\)\.text\(\s*['"](?P<val>[^'"]+)['"]\s*\)""",
    re.I,
)
_VALPARC_MONEY_RE = re.compile(
    r"""id=["']valParc["'][^>]*>\s*R\$\s*([0-9\.\,]+)""",
    re.I,
)
_VALVISTA_MONEY_RE = re.compile(
    r"""id=["']valVista["'][^>]*>\s*R\$\s*([0-9\.\,]+)""",
    re.I,
)


class TerabyteShopSpider(BaseStoreSpider):
    name = "terabyteshop"
    store, country, currency = "terabyteshop", "BR", "BRL"
    allowed_domains = ["terabyteshop.com.br"]
    start_urls: list[str] = []

    def prepare_fetch_url(self, url: str) -> str:
        """Drop ad/tracking params so fetch/cache fingerprints stay stable."""
        return canonicalize_url(url)

    def extract_offer(self, response: Response) -> ProductOffer:
        self._ensure_product_page(response)
        json_ld = self.json_ld(response)
        if not json_ld and not self._dom_has_price(response):
            raise ParseError("PDP TerabyteShop incompleta (sem Product/preço)")

        offer_ld = self._offers_object(json_ld)
        card, card_source = self._card_price(response)
        pix_price, pix_source = self._pix_price(response, offer_ld)
        if card is not None:
            price, price_source = card, card_source
        elif pix_price is not None:
            # Last resort (Pichau-aligned): only à-vista/Pix published.
            price, price_source = pix_price, f"{pix_source}-as-price-fallback"
        else:
            price, price_source = self._price_fallback(response, offer_ld)
        original, original_source, original_raw = self._original_price(response)
        installment_count, installment_price, installment_source = self._installments(
            response
        )
        product_id = self._product_id(response, json_ld)
        sku = self._sku(json_ld, product_id)
        availability, availability_source = self._availability(offer_ld, response)
        promo = terabyte_promotion_from_html(response.text or "", product_id=product_id)
        pricing_meta: dict[str, Any] = {
            "card_price": str(card) if card is not None else None,
            "pix_price": str(pix_price) if pix_price is not None else None,
        }
        if original is not None:
            pricing_meta["original_price"] = str(original)
            if original_raw:
                pricing_meta["original_price_raw"] = original_raw
        if card is not None and pix_price is not None and card > pix_price:
            pricing_meta["pix_savings"] = str(
                (card - pix_price).quantize(Decimal("0.01"))
            )
        warnings = self._price_consistency_warnings(
            original=original, pix=pix_price, card=card or price
        )
        if warnings:
            pricing_meta["consistency_warnings"] = warnings
        metadata: dict[str, Any] = {
            "source": {
                "price": price_source,
                "pix_price": pix_source,
                "card_price": card_source if card is not None else "absent",
                "original_price": original_source,
                "installment": installment_source,
                "availability": availability_source,
                "product_id": self._product_id_source(response, json_ld),
                "promotion": promo["source"] if promo else "absent",
            },
            "product_data_source": "json-ld" if json_ld else "dom",
            "pricing": pricing_meta,
        }
        if promo:
            metadata["promotion"] = promo

        logger.info(
            "terabyteshop_offer store=%s product_id=%s price_source=%s "
            "pix_source=%s card_source=%s original_source=%s "
            "installment_source=%s availability_source=%s warnings=%s",
            self.store,
            product_id,
            price_source,
            pix_source,
            card_source if card is not None else "absent",
            original_source,
            installment_source,
            availability_source,
            warnings,
        )

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=product_id,
            sku=sku,
            seller="TerabyteShop",
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=self.currency,
            price=price,
            original_price=original,
            discount_percentage=self._discount(
                price=price, original=original, pix_price=pix_price
            ),
            pix_price=pix_price,
            installment_price=installment_price,
            installment_count=installment_count,
            available=availability == "available",
            availability=availability,
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        self._ensure_product_page(response)
        json_ld = self.json_ld(response)
        title, title_source = self._title(response, json_ld)
        if not title:
            raise ParseError("Título do produto não encontrado")
        product_id = self._product_id(response, json_ld)
        brand, brand_source = self._brand(response, json_ld)
        gtin = self._gtin(json_ld)
        mpn = self._string(json_ld.get("mpn"))
        specifications, spec_source = self._specifications(response)
        structured: dict[str, Any] = {}
        if brand:
            structured["brand"] = brand
        if mpn:
            structured["model"] = mpn
        if gtin:
            structured["gtin"] = gtin
        resolved = resolve_product_identity(
            specifications=specifications,
            structured=structured,
            title=title,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        model = mpn or resolved.value("model")
        return ProductDetails(
            product_id=product_id,
            sku=self._sku(json_ld, product_id),
            title=title,
            brand=brand or resolved.value("brand"),
            model=model,
            variant=format_identity_variant(resolved),
            gtin=gtin or resolved.value("gtin"),
            specifications=specifications,
            images=[],
            metadata={
                "source": {
                    "title": title_source,
                    "brand": brand_source,
                    "model": "json-ld.mpn" if mpn else "identity-resolver",
                    "gtin": "json-ld" if gtin else "identity-resolver",
                    "specifications": spec_source,
                    "product_id": self._product_id_source(response, json_ld),
                },
                "spec_count": len(specifications),
            },
        )

    def extract_images(self, response: Response) -> list[str]:
        gallery = self._gallery_images(response)
        if gallery:
            return gallery
        return self.normalize_image_urls(
            self.json_ld(response).get("image"), base_url=response.url
        )

    def _ensure_product_page(self, response: Response) -> None:
        if "/produto/" not in (response.url or "").casefold():
            raise ParseError("URL não parece PDP TerabyteShop")
        text = (response.text or "").casefold()
        title = ""
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", response.text or "", re.I | re.S
        )
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()
        # Safety net only — fetch layer must escalate before parse. Never map
        # challenge HTML to a product / OOS.
        if ("cloudflare" in text and "just a moment" in text) or (
            "just a moment" in title.casefold()
        ):
            raise RequestError(
                "TerabyteShop apresentou challenge Cloudflare",
                code="UPSTREAM_BLOCKED",
                url=response.url,
            )

    def _product_id(self, response: Response, json_ld: dict[str, Any]) -> str:
        match = _PRODUCT_ID_RE.search(response.url or "")
        if match:
            return match.group(1)
        for key in ("productID", "productId", "sku"):
            value = self._string(json_ld.get(key))
            if value and value.isdigit():
                return value
        raise ParseError("product_id TerabyteShop não encontrado")

    def _product_id_source(self, response: Response, json_ld: dict[str, Any]) -> str:
        if _PRODUCT_ID_RE.search(response.url or ""):
            return "url"
        for key in ("productID", "productId", "sku"):
            value = self._string(json_ld.get(key))
            if value and value.isdigit():
                return f"json-ld.{key}"
        return "missing"

    def _sku(self, json_ld: dict[str, Any], product_id: str) -> str:
        """Prefer numeric catalog id; Terabyte often puts the brand in JSON-LD sku."""
        raw = self._string(json_ld.get("sku"))
        if raw and raw.isdigit():
            return raw
        mpn = self._string(json_ld.get("mpn"))
        brand = None
        brand_raw = json_ld.get("brand")
        if isinstance(brand_raw, dict):
            brand = self._string(brand_raw.get("name"))
        elif isinstance(brand_raw, str):
            brand = brand_raw.strip() or None
        if raw and brand and raw.casefold() == brand.casefold():
            return product_id
        if raw and raw.casefold() != (mpn or "").casefold():
            # Non-numeric manufacturer-ish sku that isn't just the brand name.
            if brand is None or raw.casefold() != brand.casefold():
                if not raw.isalpha():
                    return raw
        return product_id

    def _title(
        self, response: Response, json_ld: dict[str, Any]
    ) -> tuple[str | None, str]:
        candidates: list[tuple[str | None, str]] = [
            (self._string(json_ld.get("name")), "json-ld"),
            (self.first(response, ["h1::text"]), "h1"),
            (self.first(response, ["title::text"]), "title"),
        ]
        for raw, source in candidates:
            cleaned = self._clean_title(raw)
            if cleaned:
                return cleaned, source
        return None, "missing"

    @staticmethod
    def _clean_title(value: str | None) -> str | None:
        if not value:
            return None
        text = _TITLE_STORE_SUFFIX_RE.sub("", value).strip()
        return text or None

    def _brand(
        self, response: Response, json_ld: dict[str, Any]
    ) -> tuple[str | None, str]:
        brand_raw = json_ld.get("brand")
        if isinstance(brand_raw, dict):
            name = self._string(brand_raw.get("name"))
            if name:
                return name, "json-ld"
        elif isinstance(brand_raw, str) and brand_raw.strip():
            return brand_raw.strip(), "json-ld"
        specs, _ = self._specifications(response)
        for key, value in specs.items():
            if key.casefold() in {"marca", "brand"}:
                return value, "html-accordion"
        return None, "missing"

    def _gtin(self, json_ld: dict[str, Any]) -> str | None:
        for key in ("gtin13", "gtin14", "gtin12", "gtin8", "gtin", "ean"):
            value = self._string(json_ld.get(key))
            if value and value.isdigit() and 8 <= len(value) <= 14:
                return value
        return None

    def _card_price(self, response: Response) -> tuple[Decimal | None, str]:
        """Total no cartão (`#valParc`) — maps to ProductOffer.price cross-store."""
        for raw, source in (
            (response.css("#valParc::text, span.valParc::text").get(), "dom-valParc"),
            (self._jquery_text(response.text or "", ".valParc"), "js-valParc"),
        ):
            money = self._try_money(raw)
            if money is not None:
                return money, source
        match = _VALPARC_MONEY_RE.search(response.text or "")
        if match:
            money = self._try_money(match.group(1))
            if money is not None:
                return money, "dom-valParc-attr"
        return None, "absent"

    def _pix_price(
        self, response: Response, offer_ld: dict[str, Any]
    ) -> tuple[Decimal | None, str]:
        """Explicit Pix/boleto à vista (`#valVista`). Never invent from %.

        On TerabyteShop, JSON-LD ``offers.price`` tracks the à-vista/Pix total —
        use it only as corroboration when the payment-method label is present.
        """
        if not self._has_pix_avista_label(response):
            return None, "absent"

        for raw, source in (
            (
                response.css("#valVista::text, p.valVista::text").get(),
                "dom-valVista-pix",
            ),
            (self._jquery_text(response.text or "", ".val-prod"), "js-valVista-pix"),
        ):
            money = self._try_money(raw)
            if money is not None:
                return money, source
        match = _VALVISTA_MONEY_RE.search(response.text or "")
        if match:
            money = self._try_money(match.group(1))
            if money is not None:
                return money, "dom-valVista-attr"

        ld_raw = offer_ld.get("price")
        money = self._try_money(ld_raw)
        if money is not None:
            return money, "json-ld-avista-pix"
        return None, "absent"

    def _price_fallback(
        self, response: Response, offer_ld: dict[str, Any]
    ) -> tuple[Decimal, str]:
        """Last commercial total when neither card nor Pix blocks are present."""
        raw = offer_ld.get("price")
        money = self._try_money(raw)
        if money is not None:
            return money, "json-ld"
        match = re.search(
            r"['\"]price['\"]\s*:\s*([0-9]+(?:\.[0-9]+)?)",
            response.text or "",
        )
        if match:
            money = self._try_money(match.group(1))
            if money is not None:
                return money, "inline-js"
        raise ParseError("Preço TerabyteShop não encontrado")

    def _has_pix_avista_label(self, response: Response) -> bool:
        text = response.text or ""
        folded = text.casefold()
        window = folded
        idx = folded.find("valvista")
        if idx < 0:
            idx = folded.find("val-prod")
        if idx >= 0:
            window = folded[max(0, idx - 240) : idx + 480]
        markers = (
            "pix",
            "à vista",
            "a vista",
            "boleto",
            "desconto à vista",
            "desconto a vista",
        )
        return any(marker in window for marker in markers)

    def _original_price(
        self, response: Response
    ) -> tuple[Decimal | None, str, str | None]:
        """Struck / \"De\" reference price from the main product price box.

        Validity is semantic (explicit De/strikethrough), not numeric order vs
        card/installment totals. ``original < card`` is allowed.
        """
        del_text = response.css(
            "p.precode del::text, .precode del::text, "
            "#topopreco del::text, .info-price del::text"
        ).get()
        if del_text:
            money = self._try_money(del_text)
            if money is not None:
                return money, "dom-precode-del", del_text.strip()

        # Weak "De R$" fallback — scoped to the primary price box only so
        # related-product cards cannot supply a false original.
        chunks = response.css("#topopreco, .info-price, p.precode, .precotopo").getall()
        haystack = "\n".join(chunks) if chunks else ""
        for match in re.finditer(
            r"(?:de)\s*:?\s*(?:<[^>]+>\s*)*R\$\s*([0-9\.\,]+)",
            haystack,
            flags=re.I,
        ):
            raw = match.group(1)
            money = self._try_money(raw)
            if money is not None:
                return money, "dom-de-por", raw
        return None, "absent", None

    @staticmethod
    def _price_consistency_warnings(
        *,
        original: Decimal | None,
        pix: Decimal | None,
        card: Decimal | None,
    ) -> list[str]:
        """Observability only — never discard a semantically marked original."""
        warnings: list[str] = []
        if original is None:
            return warnings
        if pix is not None and original < pix:
            warnings.append("original_lt_pix")
        if card is not None and original < card:
            warnings.append("original_lt_card")
        if pix is not None and card is not None and pix > card:
            warnings.append("pix_gt_card")
        return warnings

    def _installments(
        self, response: Response
    ) -> tuple[int | None, Decimal | None, str]:
        text = response.text or ""
        count: int | None = None
        installment: Decimal | None = None

        count_raw = response.css("#nParc::text").get()
        if count_raw:
            digits = re.search(r"(\d+)", count_raw)
            if digits:
                count = int(digits.group(1))
        if count is None:
            js_count = self._jquery_text(text, ".nParc")
            if js_count:
                digits = re.search(r"(\d+)", js_count)
                if digits:
                    count = int(digits.group(1))
        if count is None:
            match = _NPARC_RE.search(text)
            if match:
                count = int(match.group(1))

        price_raw = response.css("#Parc::text").get()
        installment = self._try_money(price_raw)
        if installment is None:
            installment = self._try_money(self._jquery_text(text, ".Parc"))
        if installment is None:
            match = _PARC_RE.search(text)
            if match:
                installment = self._try_money(match.group(1))

        if count and installment and count > 0 and installment > 0:
            return count, installment, "dom-nParc-Parc"
        return None, None, "absent"

    def _try_money(self, raw: Any) -> Decimal | None:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
                value = Decimal(text)
            else:
                value = parse_money(text, self.currency)
        except (MissingPriceError, InvalidOperation, ValueError, TypeError):
            return None
        if value <= 0:
            return None
        return value.quantize(Decimal("0.01"))

    @staticmethod
    def _jquery_text(html: str, selector: str) -> str | None:
        needle = selector.casefold()
        for match in _JQ_TEXT_RE.finditer(html or ""):
            if match.group("sel").casefold() == needle:
                return match.group("val").strip() or None
        return None

    def _availability(
        self, offer_ld: dict[str, Any], response: Response
    ) -> tuple[Availability, str]:
        avail = str(offer_ld.get("availability") or "").casefold()
        if "outofstock" in avail:
            return "out_of_stock", "json-ld"
        if "instock" in avail:
            return "available", "json-ld"
        text = (response.text or "").casefold()
        if "indispon" in text and "adicionar" not in text:
            return "out_of_stock", "dom-heuristic"
        if self._dom_has_price(response) or offer_ld.get("price") is not None:
            return "available", "assumed-with-price"
        raise ParseError("Disponibilidade TerabyteShop não determinada")

    def _specifications(self, response: Response) -> tuple[dict[str, str], str]:
        chunks: list[str] = response.css(
            "div.especificacoes .panel-body, div.especificacoes"
        ).getall()
        if not chunks:
            chunks = [response.text or ""]
        folded_keys: set[str] = set()
        ordered: dict[str, str] = {}
        for chunk in chunks:
            for match in _SPEC_PAIR_RE.finditer(chunk):
                label = self._normalize_spec_text(match.group(1))
                value = self._normalize_spec_text(match.group(2))
                if not label or not value:
                    continue
                key = label.casefold()
                if key in folded_keys:
                    continue
                folded_keys.add(key)
                ordered[label] = value
        if ordered:
            return ordered, "html-accordion"
        return {}, "absent"

    def _gallery_images(self, response: Response) -> list[str]:
        urls = _GALLERY_IMG_RE.findall(response.text or "")
        return self.normalize_image_urls(urls, base_url=response.url)

    @staticmethod
    def _normalize_spec_text(raw: str) -> str:
        text = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_lib.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return text

    @staticmethod
    def _dom_has_price(response: Response) -> bool:
        return bool(
            response.css(
                "#valVista::text, p.valVista::text, #valParc::text, span.valParc::text"
            ).get()
        )

    def _offers_object(self, json_ld: dict[str, Any]) -> dict[str, Any]:
        offers = json_ld.get("offers")
        if isinstance(offers, list) and offers:
            first = offers[0]
            return first if isinstance(first, dict) else {}
        return offers if isinstance(offers, dict) else {}

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _discount(
        *,
        price: Decimal,
        original: Decimal | None,
        pix_price: Decimal | None,
    ) -> Decimal | None:
        """Promotional discount: original → sale (Pix/à vista when present).

        Does not encode card markup or Pix payment savings.
        """
        if original is None or original <= 0:
            return None
        sale = pix_price if pix_price is not None else price
        if sale >= original:
            return None
        return ((original - sale) / original * Decimal("100")).quantize(Decimal("0.01"))
