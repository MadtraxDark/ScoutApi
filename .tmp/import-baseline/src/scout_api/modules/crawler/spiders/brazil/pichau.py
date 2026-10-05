"""Pichau BR — Magento product embedded in Next.js RSC flight data."""

from __future__ import annotations

import json
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
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]
logger = logging.getLogger(__name__)

_NEXT_FLIGHT_PUSH = re.compile(
    r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)",
    re.DOTALL,
)

# Magento GraphQL / RSC keys that are not customer-facing specs.
_SPEC_SKIP_KEYS = frozenset(
    {
        "__typename",
        "id",
        "sku",
        "name",
        "url_key",
        "description",
        "short_description",
        "meta_title",
        "meta_keyword",
        "meta_description",
        "image",
        "media_gallery",
        "price_range",
        "pichau_prices",
        "special_price",
        "stock_status",
        "reviews",
        "amasty_label",
        "mysales_promotion",
        "marcas",
        "marcas_info",
        "hide_from_search",
        "is_openbox",
        "openbox_state",
        "openbox_condition",
        "pichau_prevenda",
        "product_page_layout",
        "quantity",
        "categories",
        "distribution_center_name",
        "informacoes_adicionais",
        "family",
        "kind",
    }
)

# Prefer human labels for common Magento attribute codes.
_SPEC_LABELS = {
    "potencia": "Potência",
    "garantia": "Garantia",
    "codigo_barra": "Código de barras",
    "codigo_ncm": "NCM",
    "socket": "Socket",
    "tipo_de_memoria": "Tipo de memória",
    "caracteristicas": "Características",
    "product_set_name": "Categoria",
    "slots_memoria": "Slots de memória",
    "formato_placa": "Formato",
    "plataforma": "Plataforma",
    "portas_sata": "Portas SATA",
}


class PichauSpider(BaseStoreSpider):
    name = "pichau"
    store, country, currency = "pichau", "BR", "BRL"
    allowed_domains = ["pichau.com.br"]
    start_urls: list[str] = []

    def extract_offer(self, response: Response) -> ProductOffer:
        self._ensure_product_page(response)
        product, product_source = self._product_blob(response)
        json_ld = self.json_ld(response)
        offer_ld = self._offers_object(json_ld)

        prices = product.get("pichau_prices")
        prices = prices if isinstance(prices, dict) else {}
        avista = self._money(prices.get("avista"))
        final_price = self._money(
            prices.get("final_price") or product.get("special_price")
        )
        base_price = self._money(prices.get("base_price"))
        ld_price = self._money(offer_ld.get("price"))

        # Card / primary commercial price (not PIX, not installment).
        if final_price is not None:
            price, price_source = final_price, "pichau_prices.final_price"
        elif ld_price is not None:
            price, price_source = ld_price, "json-ld"
        elif avista is not None:
            # Last resort when only PIX total is published.
            price, price_source = avista, "pichau_prices.avista"
        else:
            raise ParseError("Preço Pichau não encontrado")

        pix_price = avista
        pix_source = "pichau_prices.avista" if avista is not None else "missing"

        original = None
        original_source = "missing"
        for candidate, source in (
            (base_price, "pichau_prices.base_price"),
            (
                final_price if price != final_price else None,
                "pichau_prices.final_price",
            ),
            (ld_price if price != ld_price else None, "json-ld"),
        ):
            if candidate is not None and candidate > price:
                original = candidate
                original_source = source
                break

        installment_count = self._optional_int(prices.get("max_installments"))
        installment_price = self._money(prices.get("min_installment_price"))
        if installment_count is None or installment_price is None:
            installment_count = None
            installment_price = None
            installment_source = "missing"
        else:
            installment_source = "pichau_prices.installments"

        sku = self._string(product.get("sku")) or self._sku_from_url(response.url)
        product_id = (
            self._string(product.get("id"))
            or sku
            or self._string(json_ld.get("sku"))
            or "unknown"
        )
        availability, availability_source = self._availability(
            product, offer_ld, response
        )

        parse_quality = (
            "rsc-complete"
            if product_source.startswith("rsc") and prices
            else "json-ld-fallback"
            if price_source == "json-ld"
            else "partial"
        )
        if parse_quality != "rsc-complete":
            logger.info(
                "pichau_parse_quality=%s product_source=%s price_source=%s "
                "pix_price_source=%s structured_prices=%s url=%s",
                parse_quality,
                product_source,
                price_source,
                pix_source,
                bool(prices),
                response.url,
            )

        metadata: dict[str, Any] = {
            "source": {
                "price": price_source,
                "pix_price": pix_source,
                "original_price": original_source,
                "installment": installment_source,
                "availability": availability_source,
                "product": product_source,
                "promotion": "absent-no-structured-expiry",
            },
            "parse_quality": parse_quality,
            "pichau_prices": {
                key: prices.get(key)
                for key in (
                    "avista",
                    "avista_discount",
                    "avista_method",
                    "base_price",
                    "final_price",
                    "max_installments",
                    "min_installment_price",
                )
                if key in prices
            },
            "special_price": product.get("special_price"),
            "timed_promotion": False,
        }

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=str(product_id),
            sku=sku,
            seller="Pichau",
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=self.currency,
            price=price,
            original_price=original,
            discount_percentage=self._discount(price, original),
            pix_price=pix_price,
            installment_price=installment_price,
            installment_count=installment_count,
            available=availability == "available",
            availability=availability,
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        self._ensure_product_page(response)
        product, product_source = self._product_blob(response)
        json_ld = self.json_ld(response)
        title = (
            self._string(product.get("name"))
            or self._string(json_ld.get("name"))
            or self.first(response, ["h1::text", "title::text"])
        )
        if not title:
            raise ParseError("Título do produto não encontrado")

        brand = None
        brand_source = "missing"
        marcas = product.get("marcas_info")
        if isinstance(marcas, dict):
            brand = self._string(marcas.get("name"))
            if brand:
                brand_source = "rsc-marcas_info"
        if not brand:
            ld_brand = json_ld.get("brand")
            if isinstance(ld_brand, dict):
                brand = self._string(ld_brand.get("name"))
            else:
                brand = self._string(ld_brand)
            if brand:
                brand_source = "json-ld"

        sku = self._string(product.get("sku")) or self._sku_from_url(response.url)
        product_id = self._string(product.get("id")) or sku or "unknown"
        gtin = self._string(product.get("codigo_barra")) or self._string(
            json_ld.get("gtin") or json_ld.get("gtin13") or json_ld.get("ean")
        )

        specifications = self._specifications(product, sku=sku, gtin=gtin)
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={"brand": brand},
            title=title,
        )
        specifications = merge_specification_gaps(specifications, resolved)

        description = None
        desc_obj = product.get("description")
        if isinstance(desc_obj, dict):
            # Magento often stores a flight reference ($13); ignore non-text.
            html = desc_obj.get("html")
            if (
                isinstance(html, str)
                and html.strip()
                and not html.strip().startswith("$")
            ):
                description = html.strip()
        if description is None:
            description = self._string(json_ld.get("description"))

        attribute_sources = resolved.found_sources()
        metadata_source = {
            "title": "rsc" if product.get("name") else "json-ld-or-h1",
            "brand": attribute_sources.get("brand", brand_source),
            "model": attribute_sources.get("model", "not-found"),
            "gtin": "rsc-codigo_barra" if product.get("codigo_barra") else "not-found",
            "specifications": "rsc-attributes" if specifications else "missing",
            "product": product_source,
            **{
                key: source
                for key, source in attribute_sources.items()
                if key not in {"brand", "model"}
            },
        }
        if resolved.category:
            metadata_source["category"] = resolved.category

        return ProductDetails(
            product_id=str(product_id),
            sku=sku,
            gtin=gtin or resolved.value("gtin"),
            title=title,
            brand=brand or resolved.value("brand"),
            model=resolved.value("model"),
            variant=format_identity_variant(resolved),
            description=description,
            specifications=specifications,
            images=[],
            metadata={"source": metadata_source},
        )

    def extract_images(self, response: Response) -> list[str]:
        product, _ = self._product_blob(response)
        urls: list[str] = []
        gallery = product.get("media_gallery")
        if isinstance(gallery, list):
            for item in gallery:
                if isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]))
                elif isinstance(item, str):
                    urls.append(item)
        if not urls:
            image = product.get("image")
            if isinstance(image, dict) and image.get("url"):
                urls.append(str(image["url"]))
        if not urls:
            urls = list(
                self.normalize_image_urls(
                    self.json_ld(response).get("image"), base_url=response.url
                )
            )
            return urls
        return self.normalize_image_urls(urls, base_url=response.url)

    def _ensure_product_page(self, response: Response) -> None:
        text = (response.text or "").casefold()
        title = " ".join(response.css("title::text").getall()).strip().casefold()
        if "just a moment" in text and "cloudflare" in text:
            raise RequestError(
                "Pichau apresentou challenge Cloudflare",
                code="UPSTREAM_BLOCKED",
                url=response.url,
            )
        if "site em manutenção" in text or "pru pru" in text:
            raise ParseError("Pichau em manutenção / HTML não-PDP")
        if response.status == 404 or "página não encontrada" in title or (
            "pagina nao encontrada" in title
        ):
            raise ParseError("Página de produto Pichau não encontrada")
        if "404" in title and "pichau" in title:
            raise ParseError("Página de produto Pichau não encontrada")

    def _product_blob(self, response: Response) -> tuple[dict[str, Any], str]:
        text = response.text or ""

        flight = self._flight_blob(text)
        product = self._parse_product_object(flight) if flight else {}
        if product:
            return product, "rsc-flight"

        product = self._parse_product_object(text)
        if product:
            return product, "rsc-unescaped-html"

        fallback = self._token_fallback(text)
        if fallback:
            return fallback, "rsc-token-fallback"
        return {}, "missing"

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
        if not blob:
            return {}
        for match in re.finditer(r'"product"\s*:\s*\{', blob):
            start = match.end() - 1
            depth = 0
            end = start
            in_string = False
            escape = False
            for index, char in enumerate(blob[start:], start):
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end <= start:
                continue
            try:
                value = json.loads(blob[start:end])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and (
                value.get("pichau_prices") or value.get("sku") or value.get("id")
            ):
                return value
        return {}

    def _token_fallback(self, text: str) -> dict[str, Any]:
        # Match both raw and JS-escaped quotes in flight HTML.
        def search(pattern: str) -> re.Match[str] | None:
            return re.search(pattern, text) or re.search(
                pattern.replace('"', r'\\"'), text
            )

        sku_match = search(r'"sku"\s*:\s*"([^"]+)"')
        avista_match = search(r'"avista"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
        final_match = search(r'"final_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
        base_match = search(r'"base_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
        special_match = search(r'"special_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
        name_match = search(r'"name"\s*:\s*"([^"]{5,200})"')
        id_match = search(r'"id"\s*:\s*([0-9]+)')
        if not (sku_match or avista_match or final_match or special_match):
            return {}
        out: dict[str, Any] = {}
        if sku_match:
            out["sku"] = sku_match.group(1)
        if id_match:
            out["id"] = int(id_match.group(1))
        if name_match:
            candidate = name_match.group(1)
            # Prefer Magento product titles, skip tiny UI labels.
            if len(candidate) >= 8:
                out["name"] = candidate
        prices: dict[str, Any] = {}
        if avista_match:
            prices["avista"] = float(avista_match.group(1))
        if final_match:
            prices["final_price"] = float(final_match.group(1))
        if base_match:
            prices["base_price"] = float(base_match.group(1))
        if prices:
            out["pichau_prices"] = prices
        if special_match:
            out["special_price"] = float(special_match.group(1))
        return out

    def _specifications(
        self,
        product: dict[str, Any],
        *,
        sku: str | None,
        gtin: str | None,
    ) -> dict[str, Any]:
        specs: dict[str, Any] = {}
        if sku:
            specs["mpn"] = sku
            specs["sku"] = sku
        if gtin:
            specs["gtin"] = gtin
            specs["Código de barras"] = gtin

        for key, value in product.items():
            if key in _SPEC_SKIP_KEYS or self._is_empty_spec_value(value):
                continue
            key_fold = str(key).casefold()
            if "clube" in key_fold or key_fold.startswith("is_"):
                continue
            if isinstance(value, (dict, list)):
                continue
            # Magento select attributes often store option IDs (e.g. socket=539).
            if str(key).casefold() in {
                "socket",
                "plataforma",
                "formato_placa",
                "tipo_de_memoria",
            } and str(value).strip().isdigit():
                continue
            label = _SPEC_LABELS.get(key, key.replace("_", " ").strip().title())
            if isinstance(value, bool):
                specs[label] = "sim" if value else "não"
            else:
                text = str(value).strip()
                if text and not text.startswith("$"):
                    specs[label] = text

        categories = product.get("categories")
        if isinstance(categories, list):
            names: list[str] = []
            for item in categories:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item.get("name")).strip()
                # Skip promo/coupon category buckets (e.g. PRUMO10OFF).
                if re.fullmatch(r"[A-Z0-9]{4,}(?:OFF)?", name):
                    continue
                if name.casefold() in {"destaques", "ofertas", "promocao", "promoção"}:
                    continue
                names.append(name)
            if names:
                specs.setdefault("Categorias", " > ".join(names[:4]))

        return specs

    def _availability(
        self,
        product: dict[str, Any],
        offer_ld: dict[str, Any],
        response: Response,
    ) -> tuple[Availability, str]:
        stock = product.get("stock_status")
        if isinstance(stock, str):
            normalized = stock.casefold()
            if normalized in {"out_of_stock", "outofstock"}:
                return "out_of_stock", "rsc-stock_status"
            if normalized in {"in_stock", "instock"}:
                return "available", "rsc-stock_status"
        quantity = product.get("quantity")
        if quantity in (0, "0", False):
            return "out_of_stock", "rsc-quantity"
        avail = str(offer_ld.get("availability") or "").casefold()
        if "outofstock" in avail:
            return "out_of_stock", "json-ld"
        if "instock" in avail:
            return "available", "json-ld"
        return "available", "assumed-with-price"

    def _offers_object(self, json_ld: dict[str, Any]) -> dict[str, Any]:
        offers = json_ld.get("offers")
        if isinstance(offers, list) and offers:
            first = offers[0]
            return first if isinstance(first, dict) else {}
        return offers if isinstance(offers, dict) else {}

    @staticmethod
    def _sku_from_url(url: str) -> str | None:
        slug = url.rstrip("/").split("/")[-1]
        match = re.search(r"([A-Z]{1,5}-?\d{4,}[A-Z0-9\-]*)", slug, re.I)
        return match.group(1).upper() if match else None

    @staticmethod
    def _money(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            amount = Decimal(str(value))
            return amount.quantize(Decimal("0.01")) if amount > 0 else None
        except (InvalidOperation, ValueError, TypeError):
            pass
        try:
            return parse_money(str(value), "BRL")
        except MissingPriceError:
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _is_empty_spec_value(value: Any) -> bool:
        if value in (None, "", [], {}, False, 0, "0"):
            return True
        if isinstance(value, str) and value.strip() in {"", "0", "null", "None"}:
            return True
        return False

    @staticmethod
    def _discount(price: Decimal, original: Decimal | None) -> Decimal | None:
        if original is None or original <= 0 or price >= original:
            return None
        return ((original - price) / original * Decimal("100")).quantize(
            Decimal("0.01")
        )
