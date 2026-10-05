import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit

from scrapy.http import Response

from ...core.exceptions import ParseError
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.parsing import parse_money
from ...utils.product_attributes import (
    SOURCE_NOT_FOUND,
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]

_PRODUCT_PATH_ID = re.compile(
    r"/(?:produto|producto)/[^/]*?-(\d+)/?$",
    re.IGNORECASE,
)
_USD_TAX_FREE = re.compile(
    r"U\$?\s*([\d.,]+)\s*TAX\s*FREE",
    re.IGNORECASE,
)
_DISCOUNT_BADGE = re.compile(r"-\s*(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_SPEC_LINE = re.compile(
    r"<strong>\s*-\s*([^:<]+):\s*</strong>\s*([^<]+)",
    re.IGNORECASE,
)
_FLIX_EAN = re.compile(
    r"setAttribute\(\s*['\"]data-flix-ean['\"]\s*,\s*['\"](\d+)['\"]\s*\)",
    re.IGNORECASE,
)
_OOS_MARKERS = (
    "agotado",
    "sin stock",
    "out of stock",
    "esgotado",
    "sem estoque",
    "indisponivel",
    "indisponível",
    "no disponible",
    "não disponível",
    "nao disponivel",
)


class ShoppingChinaSpider(BaseStoreSpider):
    """Shopping China (Paraguay) adapter for in-store / tax-free price comparison.

    Availability reflects stock at the Ciudad del Este storefront, not shipping
    to Brazil. Primary ``price``/``currency`` follow whatever the requested page
    advertises (BRL on ``.com.br``, PYG on ``.com.py``); alternate figures such
    as USD tax-free stay in ``metadata.display_prices``.
    """

    name = "shoppingchina"
    store, country, currency = "shoppingchina", "PY", "PYG"
    allowed_domains = [
        "shoppingchina.com.py",
        "www.shoppingchina.com.py",
        "shoppingchina.com.br",
        "www.shoppingchina.com.br",
    ]
    start_urls: list[str] = []

    def prepare_fetch_url(self, url: str) -> str:
        # Keep requested host; only normalize locale path aliases (see below).
        return self._normalize_product_path(url)

    @staticmethod
    def _normalize_product_path(url: str) -> str:
        """Map legacy ``/produto/`` → ``/producto/`` on the ``.com.py`` host.

        ``quick_search`` still emits Portuguese ``/produto/`` slugs that soft-404
        on the live Paraguay storefront; Spanish ``/producto/`` is the PDP that
        carries price/offer markup. ``.com.br`` keeps ``/produto/``.
        """
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        path = parts.path or ""
        if host.endswith("shoppingchina.com.py") and "/produto/" in path.casefold():
            # Preserve original casing outside the segment we rewrite.
            rewritten = re.sub(
                r"/produto/",
                "/producto/",
                path,
                count=1,
                flags=re.IGNORECASE,
            )
            return parts._replace(path=rewritten).geturl()
        return url

    def extract_offer(self, response: Response) -> ProductOffer:
        self._ensure_product_page(response)
        data = self.json_ld(response)
        offer = self._json_ld_offer(data)
        product_id = self._product_id(data, response)
        price, currency, price_source = self._price_and_currency(response, offer)
        original_price = self._original_price(response, currency)
        if original_price is not None and original_price <= price:
            original_price = None
        discount = self._discount_percentage(response, price, original_price)
        availability, availability_source = self._availability(response, offer, price)
        display_prices = self._display_prices(response, currency, price)
        internal_id = self._internal_product_id(response)

        metadata: dict[str, Any] = {
            "source": {
                "price": price_source,
                "availability": availability_source,
                "seller": "storefront-direct",
                "shipping_to_brazil": False,
            },
            "shipping_to_brazil": False,
        }
        if display_prices:
            metadata["display_prices"] = display_prices
        if internal_id and internal_id != product_id:
            metadata["internal_product_id"] = internal_id

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=product_id,
            sku=product_id,
            seller="Shopping China",
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=currency,
            price=price,
            original_price=original_price,
            discount_percentage=discount,
            availability=availability,
            available=availability == "available",
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        self._ensure_product_page(response)
        data = self.json_ld(response)
        product_id = self._product_id(data, response)
        title = self._title(response, data)
        if not title:
            raise ParseError("Título do produto não encontrado")
        specifications = self._specifications(response)
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={"brand": self._brand(response, data)},
            title=title,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        gtin = self._gtin(response, product_id)
        description = self._description(response, data)
        attribute_sources = {
            key: resolved.get(key).source
            for key in (
                "brand",
                "model",
                "color",
                "storage",
                "ram",
                "vram",
                "size",
                "capacity",
                "screen_size",
                "voltage",
                "processor",
                "connectivity",
                "memory_type",
                "frequency",
                "cas_latency",
                "module_count",
                "module_capacity",
                "chipset",
                "socket",
                "wifi",
                "cores",
                "threads",
                "gpu_model",
                "wattage",
                "efficiency",
                "modularity",
                "form_factor",
                "interface",
                "pcie_generation",
                "refresh_rate",
                "resolution",
                "panel",
                "cooler_type",
                "radiator_size",
                "overclocked",
                "rpm",
            )
            if resolved.get(key).source != SOURCE_NOT_FOUND
        }
        metadata_source = {
            "product_id": "json-ld-or-url",
            "identity": "json-ld-or-html",
            "specifications": "description-spec-lines",
            "shipping_to_brazil": False,
            **attribute_sources,
        }
        if resolved.category:
            metadata_source["category"] = resolved.category
        return ProductDetails(
            product_id=product_id,
            sku=product_id,
            gtin=gtin,
            title=title,
            brand=resolved.value("brand"),
            model=resolved.value("model"),
            variant=format_identity_variant(resolved),
            description=description,
            specifications=specifications,
            metadata={
                "source": metadata_source,
                "shipping_to_brazil": False,
                "variant": {
                    key: value
                    for key, value in {
                        "color": resolved.value("color"),
                        "storage": resolved.value("storage"),
                        "size": resolved.value("size"),
                        "ram": resolved.value("ram"),
                    }.items()
                    if value
                },
            },
        )

    def extract_images(self, response: Response) -> list[str]:
        self._ensure_product_page(response)
        candidates = response.css(
            "#prodCarousel img::attr(src), "
            "#first-img::attr(src), "
            ".carousel-indicators img::attr(src), "
            "img.image-border::attr(src)"
        ).getall()
        if not candidates:
            candidates = self.normalize_image_urls(
                self.json_ld(response).get("image"), base_url=response.url
            )
        else:
            candidates = self.normalize_image_urls(candidates, base_url=response.url)
        return [url for url in candidates if self._is_product_image(url)]

    def _ensure_product_page(self, response: Response) -> None:
        path = (urlsplit(response.url).path or "").lower()
        if not any(marker in path for marker in ("/produto/", "/producto/")):
            if not self.json_ld(response):
                raise ParseError("Página de produto Shopping China não reconhecida")
        status = int(getattr(response, "status", 200) or 200)
        if status == 404:
            raise ParseError("Produto Shopping China não encontrado")
        title = " ".join(response.css("title::text").getall()).strip().casefold()
        if (
            "não encontrado" in title
            or "no encontrado" in title
            or "not found" in title
            or "error 404" in title
        ):
            raise ParseError("Produto Shopping China não encontrado")

    @classmethod
    def _json_ld_offer(cls, data: dict[str, Any]) -> dict[str, Any]:
        offers = data.get("offers")
        if isinstance(offers, dict):
            return offers
        if isinstance(offers, list):
            for item in offers:
                if isinstance(item, dict):
                    return item
        return {}

    def _product_id(self, data: dict[str, Any], response: Response) -> str:
        for key in ("@id", "sku", "productID", "productId"):
            value = data.get(key)
            if value is not None and str(value).strip().isdigit():
                return str(value).strip()
        match = _PRODUCT_PATH_ID.search(urlsplit(response.url).path or "")
        if match:
            return match.group(1)
        raise ParseError("Identificador do produto não encontrado")

    def _price_and_currency(
        self,
        response: Response,
        offer: dict[str, Any],
    ) -> tuple[Decimal, str, str]:
        # Prefer the visible primary heading (what the shopper sees on that URL),
        # then JSON-LD — never mix a value from one currency with another's code.
        visible = self._visible_primary_price(response)
        if visible is not None:
            amount, detected = visible
            return amount, detected, "rendered-primary-price"

        raw = offer.get("price")
        currency = str(offer.get("priceCurrency") or self.currency).upper()
        if raw is not None and str(raw).strip():
            return self._money(raw, currency), currency, "json-ld-offer"

        raise ParseError("Preço do produto não encontrado")

    def _visible_primary_price(self, response: Response) -> tuple[Decimal, str] | None:
        """Read the non-commented primary price heading on the product card."""
        html = response.text
        patterns = (
            (
                r"<h2[^>]*sc-text-(?:primary|danger)[^>]*>(.*?)</h2>",
                True,
            ),
            (
                r"<h2[^>]*>(.*?)</h2>",
                True,
            ),
        )
        for pattern, _ in patterns:
            for match in re.finditer(pattern, html, re.I | re.S):
                if self._is_inside_html_comment(html, match.start()):
                    continue
                text = self._strip_tags(match.group(1))
                parsed = self._parse_labeled_money(text)
                if parsed is not None:
                    return parsed
        return None

    def _original_price(self, response: Response, currency: str) -> Decimal | None:
        html = response.text
        for match in re.finditer(
            r"<h3[^>]*text-decoration-line-through[^>]*>(.*?)</h3>",
            html,
            re.I | re.S,
        ):
            if self._is_inside_html_comment(html, match.start()):
                continue
            text = self._strip_tags(match.group(1))
            parsed = self._parse_labeled_money(text)
            if parsed is None:
                continue
            amount, detected = parsed
            if detected == currency:
                return amount
        return None

    def _discount_percentage(
        self,
        response: Response,
        price: Decimal,
        original_price: Decimal | None,
    ) -> Decimal | None:
        badge = response.css(".btn-danger b::text, .btn-danger::text").get()
        if badge:
            match = _DISCOUNT_BADGE.search(badge)
            if match:
                try:
                    return Decimal(match.group(1).replace(",", ".")).quantize(
                        Decimal("0.01")
                    )
                except InvalidOperation:
                    pass
        if original_price is None or original_price <= price:
            return None
        return ((original_price - price) * 100 / original_price).quantize(
            Decimal("0.01")
        )

    def _display_prices(
        self, response: Response, primary_currency: str, _primary_price: Decimal
    ) -> dict[str, str]:
        """Alternate currencies shown by the storefront (never our own FX)."""
        extras: dict[str, str] = {}
        usd = self._usd_tax_free(response)
        if usd is not None and primary_currency != "USD":
            extras["USD"] = format(usd, "f")
        return extras

    def _usd_tax_free(self, response: Response) -> Decimal | None:
        text = " ".join(response.css("body ::text").getall())
        match = _USD_TAX_FREE.search(text)
        if not match:
            # BR locale often comments the U$ figure while keeping TAX FREE label.
            match = re.search(r"U\$\s*([\d.,]+)", response.text, re.I)
        if not match:
            return None
        try:
            return parse_money(match.group(1), "USD")
        except Exception:
            return None

    def _availability(
        self,
        response: Response,
        offer: dict[str, Any],
        price: Decimal,
    ) -> tuple[Availability, str]:
        structured = str(offer.get("availability", "")).casefold()
        if any(
            marker in structured for marker in ("outofstock", "soldout", "discontinued")
        ):
            return "out_of_stock", "json-ld-availability"

        if "instock" in structured:
            return "available", "json-ld-availability"

        if self._has_active_purchase_signal(response):
            return "available", "add-to-cart"

        page_text = " ".join(response.css("body ::text").getall()).casefold()
        if any(marker in page_text for marker in _OOS_MARKERS):
            return "out_of_stock", "page-stock-marker"

        # Paraguay comparison semantics: a priced offer remains available even
        # when the BR locale hides cart or there is no shipping to Brazil.
        if price > 0:
            return "available", "active-offer-price"
        return "unavailable", "no-clear-stock-signal"

    @staticmethod
    def _has_active_purchase_signal(response: Response) -> bool:
        if response.css("#buy-now, .buy-now-partial, [data-product-id]").get():
            return True
        labels = " ".join(
            response.css("button::text, a.btn::text, strong::text").getall()
        ).casefold()
        return any(
            marker in labels
            for marker in ("agregar", "adicionar", "comprar", "add to cart")
        )

    def _title(self, response: Response, data: dict[str, Any]) -> str | None:
        title = data.get("name") or self.first(
            response,
            [
                "h3.text-uppercase::text",
                "h1::text",
                "title::text",
            ],
        )
        return str(title).strip() if title else None

    def _brand(self, response: Response, data: dict[str, Any]) -> str | None:
        brand = data.get("brand")
        if isinstance(brand, dict):
            name = brand.get("name")
            if name:
                return str(name).strip()
        if isinstance(brand, str) and brand.strip():
            return brand.strip()
        alt = response.css("h6.text-secondary a img[alt]::attr(alt)").get()
        if alt and alt.strip():
            return alt.strip()
        return None

    def _description(self, response: Response, data: dict[str, Any]) -> str | None:
        blocks = response.css(".trix-content").getall()
        prose: list[str] = []
        for block in blocks:
            text = self._strip_tags(block)
            if not text:
                continue
            # Prefer narrative description over the bullet spec list.
            if text.lstrip().startswith("-"):
                continue
            prose.append(text)
        if prose:
            return prose[0]
        raw = data.get("description")
        return str(raw).strip() if raw else None

    def _specifications(self, response: Response) -> dict[str, Any]:
        specs: dict[str, Any] = {}
        html = "".join(response.css(".trix-content").getall()) or response.text
        for match in _SPEC_LINE.finditer(html):
            key = self._strip_tags(match.group(1)).strip(" -:\u00a0")
            value = self._strip_tags(match.group(2)).strip(" .\u00a0")
            if key and value:
                specs[key] = value
        return specs

    def _gtin(self, response: Response, product_id: str) -> str | None:
        match = _FLIX_EAN.search(response.text)
        if not match:
            return None
        ean = match.group(1)
        if ean == product_id:
            return None
        if len(ean) not in {8, 12, 13, 14}:
            return None
        return ean

    @staticmethod
    def _internal_product_id(response: Response) -> str | None:
        value = response.css("#buy-now::attr(data-product-id)").get()
        if value and value.strip().isdigit():
            return value.strip()
        return None

    @staticmethod
    def _is_product_image(url: str) -> bool:
        lower = url.casefold()
        if any(
            marker in lower
            for marker in (
                "/logo",
                "favicon",
                "sidebar-icon",
                "/img/footer",
                "/img/photos/logo",
                "sin-foto",
            )
        ):
            return False
        return (
            "active_storage" in lower
            or "/rails/" in lower
            or lower.endswith((".jpg", ".jpeg", ".png", ".webp"))
        )

    def _money(self, raw: object, currency: str) -> Decimal:
        if raw is None:
            return parse_money(None, currency)
        text = str(raw).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            amount = Decimal(text)
            if currency == "PYG":
                return amount.quantize(Decimal("1"))
            return amount
        return parse_money(text, currency)

    def _parse_labeled_money(self, text: str) -> tuple[Decimal, str] | None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return None
        if re.search(r"(?:R\$|BRL)", cleaned, re.I):
            currency = "BRL"
        elif re.search(r"(?:Gs\.?|PYG|guaran)", cleaned, re.I):
            currency = "PYG"
        elif re.search(r"(?:U\$|US\$|USD|\$)", cleaned, re.I):
            currency = "USD"
        else:
            return None
        try:
            amount = parse_money(cleaned, currency)
        except Exception:
            return None
        if currency == "PYG":
            amount = amount.quantize(Decimal("1"))
        return amount, currency

    @staticmethod
    def _strip_tags(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_inside_html_comment(html: str, index: int) -> bool:
        open_idx = html.rfind("<!--", 0, index)
        if open_idx < 0:
            return False
        close_idx = html.find("-->", open_idx)
        return close_idx < 0 or close_idx > index
