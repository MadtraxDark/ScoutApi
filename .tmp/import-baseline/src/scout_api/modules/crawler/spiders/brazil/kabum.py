import json
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any, Literal
from urllib.parse import urlparse

from scrapy.http import Response
from scrapy.selector import Selector

from ...core.exceptions import ParseError
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


class KabumSpider(BaseStoreSpider):
    name = "kabum"
    store, country, currency = "kabum", "BR", "BRL"
    allowed_domains = ["kabum.com.br"]
    start_urls: list[str] = []

    def extract_offer(self, response: Response) -> ProductOffer:
        product = self._product(response)
        json_ld = self.json_ld(response)
        prices = self._dict(product.get("prices"))
        prime = self._dict(product.get("prime"))
        installment = self._dict(product.get("installment"))

        price_raw = product.get("price") or prices.get("price")
        price_source = "product-state"
        if price_raw is None:
            price_raw = self._json_ld_offer(json_ld).get("price")
            price_source = "json-ld-offer"
        price = self._state_money(price_raw, response, price_source)

        pix_raw = prices.get("priceWithDiscount") or prime.get("priceWithDiscount")
        pix_source = "product-prices-state" if pix_raw is not None else "not-found"
        pix_price = self._optional_money(pix_raw)
        original_raw = prices.get("oldPrice")
        original_price = self._optional_money(original_raw)
        if original_price is not None and original_price <= price:
            original_price = None

        availability, availability_source = self._availability(
            response, product, json_ld
        )
        product_id = (
            product.get("id") or json_ld.get("sku") or self._url_id(response.url)
        )
        if not product_id:
            raise ParseError("Identificador do produto não encontrado")
        seller = product.get("sellerName") or self._dict(
            product.get("marketplace")
        ).get("sellerName")
        sku = self._first_value(product, "sku", "productSku", "reference")
        discount = self._optional_money(prices.get("discountPercentage"))
        if discount is None and original_price is not None:
            discount = ((original_price - price) * 100 / original_price).quantize(
                Decimal("0.01")
            )

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=str(product_id).strip(),
            sku=self._string(sku),
            seller=self._string(seller),
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=self.currency,
            price=price,
            pix_price=pix_price,
            original_price=original_price,
            discount_percentage=discount,
            installment_price=self._optional_money(installment.get("amount")),
            installment_count=self._optional_int(installment.get("installment")),
            availability=availability,
            available=availability == "available",
            metadata={
                "source": {
                    "price": price_source,
                    "pix_price": pix_source,
                    "original_price": "product-prices-state"
                    if original_price is not None
                    else "not-found",
                    "installment": "product-installment-state"
                    if installment
                    else "not-found",
                    "availability": availability_source,
                    "seller": "product-marketplace-state" if seller else "not-found",
                }
            },
        )

    def extract_details(self, response: Response) -> ProductDetails:
        product = self._product(response)
        json_ld = self.json_ld(response)
        title = (
            product.get("title")
            or json_ld.get("name")
            or self.first(response, ["h1::text", "title::text"])
        )
        product_id = (
            product.get("id") or json_ld.get("sku") or self._url_id(response.url)
        )
        if not title or not product_id:
            raise ParseError("Identidade do produto não encontrada")

        specifications = self._specifications(product)
        brand = self._dict(product.get("brands")).get("name") or self._brand(json_ld)
        model = self._value_for_label(specifications, "modelo")
        gtin = self._first_value(
            product, "gtin", "ean", "gtin13"
        ) or self._value_for_label(specifications, "ean", "gtin", "código de barras")
        description = product.get("description") or json_ld.get("description")
        title_text = str(title).strip()
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={
                "brand": brand,
                "model": model,
                "color": self._first_value(product, "color", "colour"),
            },
            title=title_text,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        variant = self._string(self._first_value(product, "variant", "color"))
        if not variant:
            variant = format_identity_variant(resolved)
        attribute_sources = resolved.found_sources()
        metadata_source = {
            "title": "product-state" if product.get("title") else "json-ld-or-h1",
            "description": "product-state" if product.get("description") else "json-ld",
            "specifications": "product-technical-information-state"
            if specifications
            else "not-found",
            "brand": attribute_sources.get(
                "brand",
                "product-brand-state" if brand else "not-found",
            ),
            "model": attribute_sources.get(
                "model",
                "technical-information-state" if model else "not-found",
            ),
            "gtin": "technical-information-state" if gtin else "not-found",
            "images": "product-gallery-state" if product.get("medias") else "not-found",
            **{
                key: source
                for key, source in attribute_sources.items()
                if key not in {"brand", "model"}
            },
        }
        if resolved.category:
            metadata_source["category"] = resolved.category
        return ProductDetails(
            product_id=str(product_id).strip(),
            sku=self._string(
                self._first_value(product, "sku", "productSku", "reference")
            ),
            gtin=self._string(gtin),
            title=title_text,
            brand=self._string(brand) or resolved.value("brand"),
            model=resolved.value("model"),
            variant=variant,
            description=self._html_text(description),
            specifications=specifications,
            metadata={"source": metadata_source},
        )

    def extract_images(self, response: Response) -> list[str]:
        product = self._product(response)
        media = product.get("medias")
        candidates: list[str] = []
        if isinstance(media, list):
            for entry in media:
                images = self._dict(entry).get("images")
                if isinstance(images, dict):
                    for size in (
                        "gg",
                        "xlarge",
                        "g",
                        "large",
                        "m",
                        "medium",
                        "p",
                        "small",
                    ):
                        value = images.get(size)
                        if isinstance(value, str) and value.strip():
                            candidates.append(value)
                            break
        urls = self._dedupe_image_variants(candidates, response.url)
        if urls:
            return urls

        # Rendered fallback is restricted to the product gallery's accessible name.
        rendered = response.css(
            "img[alt^='Imagem '][alt*=' do produto']::attr(src), "
            "img[alt^='Imagem '][alt*=' do produto']::attr(data-src)"
        ).getall()
        return self._dedupe_image_variants(rendered, response.url)

    @staticmethod
    def _product(response: Response) -> dict[str, Any]:
        for raw in response.css("script#__NEXT_DATA__::text").getall():
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                continue
            props = state.get("props", {}) if isinstance(state, dict) else {}
            page_props = props.get("pageProps", {}) if isinstance(props, dict) else {}
            product = (
                page_props.get("product") if isinstance(page_props, dict) else None
            )
            if isinstance(product, dict):
                return product
        return {}

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _first_value(data: dict[str, Any], *keys: str) -> Any:
        return next(
            (data[key] for key in keys if data.get(key) not in (None, "")), None
        )

    @staticmethod
    def _json_ld_offer(data: dict[str, Any]) -> dict[str, Any]:
        return data.get("offers", {}) if isinstance(data.get("offers"), dict) else {}

    def _state_money(self, raw: Any, response: Response, source: str) -> Decimal:
        if raw is None:
            return parse_money(None, self.currency)
        try:
            value = Decimal(str(raw))
            if value <= 0:
                raise InvalidOperation
            return value.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError, TypeError):
            return parse_money(str(raw), self.currency)

    @staticmethod
    def _optional_money(raw: Any) -> Decimal | None:
        if raw is None or raw == "":
            return None
        try:
            value = Decimal(str(raw))
            return value.quantize(Decimal("0.01")) if value > 0 else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _optional_int(raw: Any) -> int | None:
        try:
            value = int(raw)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _availability(
        response: Response, product: dict[str, Any], json_ld: dict[str, Any]
    ) -> tuple[Availability, str]:
        if (
            product.get("available") is False
            or product.get("flags", {}).get("isAvailable") is False
        ):
            return "out_of_stock", "product-availability-state"
        if (
            product.get("available") is True
            or product.get("flags", {}).get("isAvailable") is True
        ):
            return "available", "product-availability-state"
        availability = str(
            KabumSpider._json_ld_offer(json_ld).get("availability", "")
        ).lower()
        if "outofstock" in availability or "soldout" in availability:
            return "out_of_stock", "json-ld-offer"
        if "instock" in availability:
            return "available", "json-ld-offer"
        text = " ".join(response.css("body ::text").getall()).lower()
        if re.search(r"produto\s+(esgotado|indisponível)|fora de estoque", text):
            return "out_of_stock", "rendered-stock-message"
        if response.css("#purchase").get() or response.css("button#purchase").get():
            return "available", "purchase-controls"
        return "unavailable", "no-availability-signal"

    @staticmethod
    def _specifications(product: dict[str, Any]) -> dict[str, Any]:
        technical = product.get("technicalInformation")
        if isinstance(technical, dict):
            return technical
        if not isinstance(technical, str):
            return {}
        technical = re.sub(r"<br\s*/?>", "\n", technical, flags=re.I)
        text = Selector(text=technical).xpath("string(.)").get() or ""
        result: dict[str, str] = {}
        for match in re.finditer(r"[-–]\s*([^:\n]+):\s*([^\n]+)", text):
            key, value = (part.strip() for part in match.groups())
            if key and value:
                result[key] = value
        return result

    @staticmethod
    def _value_for_label(values: dict[str, Any], *labels: str) -> Any:
        wanted = {label.casefold() for label in labels}
        for key, value in values.items():
            if str(key).casefold().strip() in wanted:
                return value
        return None

    @staticmethod
    def _brand(json_ld: dict[str, Any]) -> Any:
        brand = json_ld.get("brand")
        return brand.get("name") if isinstance(brand, dict) else brand

    @staticmethod
    def _html_text(value: Any) -> str | None:
        if not value:
            return None
        text = Selector(text=unescape(str(value))).xpath("string(.)").get() or ""
        return " ".join(text.split()) or None

    @staticmethod
    def _string(value: Any) -> str | None:
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _url_id(url: str) -> str | None:
        match = re.search(r"/produto/(\d+)", urlparse(url).path)
        return match.group(1) if match else None

    @staticmethod
    def _dedupe_image_variants(values: list[str], base_url: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            absolute = str(value).strip()
            if not absolute:
                continue
            absolute = BaseStoreSpider.normalize_image_urls(
                absolute, base_url=base_url
            )[0]
            key = re.sub(
                r"/(?:small|medium|large|xlarge)/", "/<size>/", absolute, flags=re.I
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(absolute)
        return result
