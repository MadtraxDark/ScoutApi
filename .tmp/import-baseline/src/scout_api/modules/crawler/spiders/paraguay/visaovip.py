"""Visão VIP (Paraguay) store adapter — Next.js RSC flight product payload."""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

from scrapy.http import Response

from ...core.exceptions import MissingPriceError, ParseError
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.parsing import parse_money
from ...utils.product_attributes import (
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]

_PRODUCT_PATH_ID = re.compile(r"/prod/.+/(\d+)/?$", re.IGNORECASE)
_NEXT_FLIGHT_PUSH = re.compile(
    r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)",
    re.DOTALL,
)
_PRODUCT_OBJECT_START = re.compile(r'\{"productCode":\d+,"companyCode":')
_GALLERY_ARRAY = re.compile(
    r'"productGalleryImages":(\["https://cdn\.visaovip\.com[^]]+\])'
)
_SPEC_REF_LABELS = frozenset(
    {
        "referencia",
        "referência",
        "reference",
        "sku",
        "mpn",
        "codigo do fabricante",
        "código do fabricante",
        "part number",
    }
)
_SPEC_GTIN_LABELS = frozenset(
    {
        "ean",
        "gtin",
        "upc",
        "codigo de barras",
        "código de barras",
        "barcode",
    }
)
_SOFT_404_TITLE = re.compile(
    r"produto\s*-\s*vis[aã]ovip",
    re.IGNORECASE,
)
# Hero U$ amount followed by storefront companion G$ / R$ (not our FX).
_DISPLAY_PRICE_BLOCK = re.compile(
    r"U\$\s*(?P<usd>[\d.,]+)\s*</[^>]+>"
    r".{0,600}?"
    r"G\$\s*(?P<pyg>[\d.]+)"
    r".{0,200}?"
    r"R\$\s*(?P<brl>[\d.,]+)",
    re.DOTALL | re.IGNORECASE,
)
_OG_CURRENCY_CODES = frozenset({"USD", "BRL", "PYG", "EUR"})


class VisaoVipSpider(BaseStoreSpider):
    """Parse Visão VIP PDPs from Next.js App Router RSC flight payloads.

    The storefront is a Ciudad del Este (PY) retailer advertising USD (``U$``)
    prices. Availability reflects stock at the Paraguay storefront, not
    shipping to Brazil.
    """

    name = "visaovip"
    store, country, currency = "visaovip", "PY", "USD"
    allowed_domains = ["visaovip.com", "www.visaovip.com"]
    start_urls: list[str] = []

    def extract_offer(self, response: Response) -> ProductOffer:
        product = self._product(response)
        product_id = self._product_id(product, response.url)
        if not product_id:
            raise ParseError("Identificador do produto não encontrado")

        price, original_price, price_source, original_source = self._prices(product)
        discount = None
        if original_price is not None and original_price > price:
            discount = ((original_price - price) * 100 / original_price).quantize(
                Decimal("0.01")
            )

        currency, currency_source = self._offer_currency(response)
        availability, availability_source = self._availability(response, product)
        sku, sku_source = self._sku(product)
        seller = self._seller(response)
        display_prices, display_source = self._display_prices(response, price)

        metadata: dict[str, Any] = {
            "shipping_to_brazil": False,
            "source": {
                "product_data": "rsc-flight",
                "price": price_source,
                "original_price": original_source,
                "pix_price": "not-found",
                "installment": "not-found",
                "currency": currency_source,
                "display_prices": display_source,
                "availability": availability_source,
                "seller": "storefront-direct" if seller else "not-found",
                "sku": sku_source,
            },
        }
        if display_prices:
            metadata["display_prices"] = display_prices

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=product_id,
            sku=sku,
            seller=seller,
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=currency,
            price=price,
            pix_price=None,
            original_price=original_price,
            discount_percentage=discount,
            installment_price=None,
            installment_count=None,
            availability=availability,
            available=availability == "available",
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        product = self._product(response)
        product_id = self._product_id(product, response.url)
        title = self._string(product.get("productName")) or self.first(
            response, ["h1::text", "title::text"]
        )
        if not title or not product_id:
            raise ParseError("Identidade do produto não encontrada")

        specifications = self._specifications(product)
        brand = self._string(product.get("manufactureName")) or self._value_for_label(
            specifications, "marca", "brand"
        )
        model = self._value_for_label(specifications, "modelo", "model")
        gtin = self._gtin(product, specifications)
        description = self._string(product.get("productComents")) or self._string(
            product.get("productDescription")
        )
        title_text = title.strip()
        color = self._value_for_label(specifications, "cor", "color", "colour")
        identity_specs = {
            key: value
            for key, value in specifications.items()
            # Memory bus / clock-rate rows confuse shared RAM capacity mapping.
            if not self._is_non_capacity_memory_label(str(key))
        }
        resolved = resolve_product_identity(
            specifications=identity_specs,
            structured={
                "brand": brand,
                "model": model,
                "color": color,
            },
            title=title_text,
        )
        merged = merge_specification_gaps(identity_specs, resolved)
        for key, value in specifications.items():
            merged.setdefault(key, value)
        specifications = merged
        variant = self._string(color)
        if not variant:
            variant = format_identity_variant(resolved)

        attribute_sources = resolved.found_sources()
        metadata_source = {
            "title": "product-flight-state"
            if product.get("productName")
            else "h1-or-title",
            "description": "product-flight-state" if description else "not-found",
            "specifications": "product-flight-state" if specifications else "not-found",
            "brand": attribute_sources.get(
                "brand",
                "product-manufacture-state" if brand else "not-found",
            ),
            "model": attribute_sources.get(
                "model",
                "product-specifications" if model else "not-found",
            ),
            "gtin": "product-specifications" if gtin else "not-found",
            "images": "product-gallery-state"
            if product.get("productFrontImage") or product.get("productGalleryImages")
            else "not-found",
            **{
                key: source
                for key, source in attribute_sources.items()
                if key not in {"brand", "model"}
            },
        }
        if resolved.category:
            metadata_source["category"] = resolved.category

        sku, _sku_source = self._sku(product)
        return ProductDetails(
            product_id=product_id,
            sku=sku,
            gtin=gtin,
            title=title_text,
            brand=self._string(brand) or resolved.value("brand"),
            model=resolved.value("model"),
            variant=variant,
            description=description,
            specifications=specifications,
            metadata={"source": metadata_source},
        )

    def extract_images(self, response: Response) -> list[str]:
        product = self._product(response)
        candidates: list[str] = []
        front = product.get("productFrontImage")
        if isinstance(front, str) and front.strip():
            candidates.append(front.strip())
        gallery = product.get("productGalleryImages")
        if isinstance(gallery, list):
            for entry in gallery:
                if isinstance(entry, str) and entry.strip():
                    candidates.append(entry.strip())
        return self.normalize_image_urls(candidates, base_url=response.url)

    def _product(self, response: Response) -> dict[str, Any]:
        self._ensure_product_page(response)
        blob = self._flight_blob(response.text or "")
        product = self._parse_product_object(blob)
        if not product:
            raise ParseError("Payload do produto Visão VIP não encontrado")
        gallery = product.get("productGalleryImages")
        if isinstance(gallery, str) or gallery is None:
            resolved = self._gallery_from_blob(blob)
            if resolved:
                product["productGalleryImages"] = resolved
        return product

    def _ensure_product_page(self, response: Response) -> None:
        title = " ".join(response.css("title::text").getall()).strip()
        body = response.text or ""
        if _SOFT_404_TITLE.search(title) and "productCode" not in body:
            raise ParseError("Página Visão VIP não encontrada (soft-404)")
        if response.status == 404:
            raise ParseError("Página Visão VIP não encontrada (HTTP 404)")

    @classmethod
    def _flight_blob(cls, html: str) -> str:
        chunks: list[str] = []
        for match in _NEXT_FLIGHT_PUSH.finditer(html):
            chunks.append(cls._decode_js_string(match.group(1)))
        return "\n".join(chunks)

    @staticmethod
    def _decode_js_string(body: str) -> str:
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

    @classmethod
    def _parse_product_object(cls, blob: str) -> dict[str, Any]:
        match = _PRODUCT_OBJECT_START.search(blob)
        if not match:
            return {}
        start = match.start()
        depth = 0
        end = start
        for index, char in enumerate(blob[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        try:
            value = json.loads(blob[start:end])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _gallery_from_blob(blob: str) -> list[str]:
        match = _GALLERY_ARRAY.search(blob)
        if not match:
            return []
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item.strip()]

    def _product_id(self, product: dict[str, Any], url: str) -> str | None:
        code = product.get("productCode")
        if code is not None and str(code).strip():
            return str(code).strip()
        match = _PRODUCT_PATH_ID.search(urlparse(url).path or "")
        return match.group(1) if match else None

    def _prices(
        self, product: dict[str, Any]
    ) -> tuple[Decimal, Decimal | None, str, str]:
        list_price = self._money(product.get("productPrice"), required=True)
        promo_raw = product.get("productPromotionPrice")
        promo = self._optional_money(promo_raw)
        is_promo = bool(product.get("isProductPromotion")) and promo is not None

        if is_promo and promo is not None and promo > 0:
            # Store cards: strikethrough = productPrice, highlight = promotion.
            if list_price > promo:
                return (
                    promo,
                    list_price,
                    "product-promotion-price",
                    "product-price-state",
                )
            return promo, None, "product-promotion-price", "not-found"

        return list_price, None, "product-price-state", "not-found"

    def _offer_currency(self, response: Response) -> tuple[str, str]:
        """Prefer Open Graph currency when present; otherwise spider default (USD)."""
        og = (
            (
                response.css(
                    "meta[property='product:price:currency']::attr(content)"
                ).get()
                or ""
            )
            .strip()
            .upper()
        )
        if og in _OG_CURRENCY_CODES:
            return og, "og-price-currency"
        return self.currency, "store-default"

    def _display_prices(
        self, response: Response, primary_usd: Decimal
    ) -> tuple[dict[str, str] | None, str]:
        """Capture storefront G$/R$ companions shown next to the primary U$ price.

        These are Visão VIP display conversions (not ScoutApiV2 FX). Never used
        as ``price``/``currency``. Pix is not exposed on this storefront.
        """
        html = response.text or ""
        if not html:
            return None, "not-found"

        target = primary_usd.quantize(Decimal("0.01"))
        for match in _DISPLAY_PRICE_BLOCK.finditer(html):
            try:
                usd = parse_money(match.group("usd"), "USD").quantize(Decimal("0.01"))
            except MissingPriceError:
                continue
            if usd != target:
                continue
            extras: dict[str, str] = {}
            try:
                pyg = parse_money(match.group("pyg"), "PYG")
                extras["PYG"] = format(pyg, "f")
            except MissingPriceError:
                pass
            try:
                brl = parse_money(match.group("brl"), "BRL")
                extras["BRL"] = format(brl, "f")
            except MissingPriceError:
                pass
            if extras:
                return extras, "pdp-html-companion"
        return None, "not-found"

    def _money(self, raw: Any, *, required: bool) -> Decimal:
        value = self._optional_money(raw)
        if value is None:
            if required:
                raise ParseError(f"Preço Visão VIP inválido: {raw!r}")
            raise ParseError("Preço Visão VIP ausente")
        return value

    @staticmethod
    def _optional_money(raw: Any) -> Decimal | None:
        if raw is None or raw == "":
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            return None
        if value <= 0:
            return None
        return value.quantize(Decimal("0.01"))

    @staticmethod
    def _availability(
        response: Response, product: dict[str, Any]
    ) -> tuple[Availability, str]:
        balance = product.get("isProductWithBalance")
        if balance is False:
            return "out_of_stock", "product-balance-state"
        if balance is True:
            return "available", "product-balance-state"

        og = (
            response.css("meta[property='og:availability']::attr(content)").get() or ""
        ).casefold()
        if "instock" in og:
            return "available", "og-availability"
        if any(marker in og for marker in ("outofstock", "oos", "soldout")):
            return "out_of_stock", "og-availability"

        return "unavailable", "no-availability-signal"

    def _sku(self, product: dict[str, Any]) -> tuple[str | None, str]:
        specs = self._specifications(product)
        reference = self._value_for_label(specs, *_SPEC_REF_LABELS)
        if reference:
            return self._string(reference), "product-specifications"
        # Fallback: store código is the durable public id when no manufacturer MPN.
        code = product.get("productCode")
        if code is not None and str(code).strip():
            return str(code).strip(), "product-code-fallback"
        return None, "not-found"

    def _gtin(
        self, product: dict[str, Any], specifications: dict[str, Any]
    ) -> str | None:
        for key in ("gtin", "ean", "upc", "barcode"):
            value = self._string(product.get(key))
            if value:
                return value
        return self._string(self._value_for_label(specifications, *_SPEC_GTIN_LABELS))

    @staticmethod
    def _specifications(product: dict[str, Any]) -> dict[str, Any]:
        rows = product.get("productSpecifications")
        if not isinstance(rows, list):
            return {}
        result: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("specificationName")
            value = row.get("specificationValue")
            if name is None or value is None:
                continue
            key = str(name).strip()
            text = str(value).strip()
            if key and text:
                result[key] = text
        return result

    @staticmethod
    def _value_for_label(values: dict[str, Any], *labels: str) -> Any:
        wanted = {VisaoVipSpider._fold(label) for label in labels}
        for key, value in values.items():
            if VisaoVipSpider._fold(str(key)) in wanted:
                return value
        return None

    @staticmethod
    def _is_non_capacity_memory_label(label: str) -> bool:
        folded = VisaoVipSpider._fold(label)
        return any(
            marker in folded
            for marker in (
                "barramento",
                "velocidade da memoria",
                "clock",
                "frequencia",
            )
        )

    @staticmethod
    def _fold(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return ascii_text.casefold().strip()

    @staticmethod
    def _seller(response: Response) -> str:
        site = response.css("meta[property='og:site_name']::attr(content)").get()
        if site and site.strip():
            return site.strip()
        return "Visãovip"

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
