import json
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any, Literal
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlparse,
    urlsplit,
    urlunsplit,
)

from scrapy.http import Response
from scrapy.selector import Selector

from ...core.exceptions import ParseError
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.parsing import parse_money
from ...utils.product_attributes import resolve_attributes, resolve_product_identity
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]

# US carriers are market-specific commercial conditions, not product identity
# for Brazilian price comparison. Keep them out of normalized `variant`.
_US_CARRIERS = frozenset(
    {
        "at&t",
        "att",
        "verizon",
        "t-mobile",
        "tmobile",
        "t mobile",
        "sprint",
        "us cellular",
        "boost mobile",
        "cricket",
        "metro by t-mobile",
        "xfinity mobile",
    }
)


class BestBuySpider(BaseStoreSpider):
    """Best Buy adapter based on product JSON and semantic product markup."""

    name = "bestbuy"
    store, country, currency = "bestbuy", "US", "USD"
    allowed_domains = ["bestbuy.com", "www.bestbuy.com"]
    start_urls: list[str] = []

    def prepare_fetch_url(self, url: str) -> str:
        """Normalize fetch URL and skip the international country splash.

        Accepts both modern ``/product/{slug}/{bsin}`` and legacy
        ``/site/{slug}/{sku}.p?skuId={sku}`` forms. ``intl=nosplash`` is only
        for the fetch request — see ``_canonical_offer_url``.
        """
        parts = urlsplit(url.strip())
        path = parts.path or "/"
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        sku = next(
            (value for key, value in pairs if key.lower() == "skuid" and value.strip()),
            None,
        )
        # Bare legacy SKU paths: /site/6556754.p → keep skuId in query.
        if sku is None:
            match = re.search(r"/(\d{5,10})\.p/?$", path, re.I)
            if match:
                sku = match.group(1)
                pairs = [(k, v) for k, v in pairs if k.lower() != "skuid"]
                pairs.append(("skuId", sku))
        pairs = [(key, value) for key, value in pairs if key.lower() != "intl"]
        pairs.append(("intl", "nosplash"))
        return urlunsplit(
            (parts.scheme, parts.netloc, path, urlencode(pairs), parts.fragment)
        )

    @staticmethod
    def _canonical_offer_url(url: str) -> str:
        """Canonical PDP URL without fetch-only ``intl=nosplash``."""
        parts = urlsplit(url.strip())
        pairs = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() != "intl"
        ]
        cleaned = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(pairs), "")
        )
        return canonicalize_url(cleaned)

    def extract_offer(self, response: Response) -> ProductOffer:
        self._ensure_product_page(response)
        product = self._product_data(response)
        self._ensure_sku_consistency(response, product)
        offer = self._offer_data(product)
        price_raw = self._first_value(
            offer, "price", "currentPrice", "salePrice", "customerPrice"
        )
        if price_raw is None:
            price_raw = self._first_value(
                product, "price", "currentPrice", "salePrice", "customerPrice"
            )
        price = self._money(price_raw)
        original = self._optional_money(
            self._first_value(
                offer, "originalPrice", "regularPrice", "wasPrice", "listPrice"
            )
        )
        if original is not None and original <= price:
            original = None
        discount = self._optional_money(
            self._first_value(offer, "discountPercentage", "percentOff")
        )
        if discount is None and original is not None:
            discount = ((original - price) * 100 / original).quantize(Decimal("0.01"))
        availability, availability_source = self._availability(
            response, offer, product, price=price
        )
        seller = self._seller(response, offer, product)
        title = self._string(
            self._first_value(product, "name", "title", "productName")
            or self.first(response, ["h1::text", "[itemprop='name']::text"])
        )
        carrier_meta = self._carrier_metadata(product, title)
        installment_price, installment_count = self._installment(
            offer, carrier_locked=bool(carrier_meta.get("carrier_locked"))
        )
        offer_metadata: dict[str, Any] = {
            "source": {
                "price": "product-offer-state" if offer else "json-ld-or-price-state",
                "availability": availability_source,
                "seller": "product-offer-state" if seller else "not-found",
                "location_dependent": self._location_dependent(response),
                "installment": (
                    "omitted-carrier-financing"
                    if carrier_meta.get("carrier_locked")
                    else "product-offer-state"
                    if installment_price is not None
                    else "not-found"
                ),
            }
        }
        offer_metadata.update(carrier_meta)
        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=self._product_id(product, response.url),
            sku=self._string(self._first_value(product, "sku", "skuId", "productSku")),
            seller=seller,
            url=response.url,
            canonical_url=self._canonical_offer_url(response.url),
            currency=self.currency,
            price=price,
            original_price=original,
            discount_percentage=discount,
            installment_price=installment_price,
            installment_count=installment_count,
            availability=availability,
            available=availability == "available",
            metadata=offer_metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        self._ensure_product_page(response)
        product = self._product_data(response)
        self._ensure_sku_consistency(response, product)
        title = self._string(
            self._first_value(product, "name", "title", "productName")
            or self.first(
                response, ["h1::text", "[itemprop='name']::text", "title::text"]
            )
        )
        product_id = self._product_id(product, response.url)
        if not title:
            raise ParseError("Identidade do produto não encontrada")
        variants = self._variants(product, title)
        carrier_meta = self._carrier_metadata(product, title)
        gtin = self._clean_short(
            self._first_value(product, "gtin", "gtin12", "gtin13", "upc")
        )
        details_metadata: dict[str, Any] = {
            "source": {
                "product_id": "product-state-or-url",
                "identity": "product-state-or-json-ld",
                "variants": "product-state-or-title",
                "location_dependent": self._location_dependent(response),
            },
            "variant": variants,
        }
        details_metadata.update(carrier_meta)
        raw_model = self._clean_short(
            self._first_value(product, "model", "modelNumber", "mpn")
        )
        resolved = resolve_product_identity(
            specifications=self._specifications(product),
            structured={
                "brand": self._brand(product),
                "model": raw_model,
                "color": variants.get("color"),
                "storage": variants.get("storage"),
            },
            title=title,
        )
        model = resolved.value("model") or raw_model
        if raw_model and model != raw_model:
            details_metadata.setdefault("structured_model", raw_model)
        return ProductDetails(
            product_id=product_id,
            sku=self._string(self._first_value(product, "sku", "skuId", "productSku")),
            gtin=gtin,
            title=title,
            brand=self._brand(product) or resolved.value("brand"),
            model=model,
            variant="; ".join(f"{key}: {value}" for key, value in variants.items())
            or None,
            description=self._description(product),
            specifications=self._specifications(product),
            metadata=details_metadata,
        )

    def extract_images(self, response: Response) -> list[str]:
        self._ensure_product_page(response)
        product = self._product_data(response)
        candidates = self._image_values(
            self._first_value(product, "images", "gallery", "media", "productImages")
        )
        if not candidates:
            candidates = self._image_values(self.json_ld(response).get("image"))
        if not candidates:
            candidates = response.css(
                "[data-testid*='gallery'] img::attr(src), "
                "[data-testid*='gallery'] img::attr(data-src), "
                "[aria-label*='product image' i] img::attr(src)"
            ).getall()
        return self._dedupe_images(candidates, response.url)

    @classmethod
    def _product_data(cls, response: Response) -> dict[str, Any]:
        """Prefer JSON-LD Product; only merge short structured keys from other JSON."""
        product = dict(cls.json_ld(response))
        safe_keys = {
            "productId",
            "productIdCode",
            "sku",
            "skuId",
            "productSku",
            "gtin",
            "gtin12",
            "gtin13",
            "upc",
            "brand",
            "model",
            "modelNumber",
            "mpn",
            "color",
            "storage",
            "carrier",
            "condition",
            "name",
            "title",
            "productName",
            "images",
            "gallery",
            "media",
            "productImages",
            "specifications",
            "specs",
            "specificationGroups",
            "technicalSpecifications",
            "offers",
            "offer",
            "selectedOffer",
            "pricing",
            "priceInfo",
            "price",
            "currentPrice",
            "salePrice",
            "customerPrice",
            "originalPrice",
            "regularPrice",
            "wasPrice",
            "listPrice",
            "installmentPrice",
            "installmentCount",
            "monthlyPrice",
            "paymentAmount",
            "months",
            "term",
            "discountPercentage",
            "percentOff",
            "availability",
            "stockStatus",
            "status",
            "available",
            "inStock",
            "isAvailable",
            "seller",
            "sellerName",
            "merchant",
        }
        for raw in response.css("script::text").getall():
            text = raw.strip()
            if not text or text.startswith("<!--") or len(text) > 500_000:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            found = cls._find_product_dict(value)
            if not found:
                continue
            for key in safe_keys:
                candidate = found.get(key)
                if candidate in (None, ""):
                    continue
                if key in {"description", "longDescription"}:
                    continue
                if isinstance(candidate, str) and len(candidate) > 500:
                    continue
                product[key] = candidate
            description = found.get("description") or found.get("longDescription")
            if (
                isinstance(description, str)
                and 0 < len(description) <= 4000
                and not cls._looks_like_page_noise(description)
            ):
                product.setdefault("description", description)
        apollo = cls._apollo_ssr_product_data(response)
        for key, value in apollo.items():
            if product.get(key) in (None, ""):
                product[key] = value
        return product

    @classmethod
    def _apollo_ssr_product_data(cls, response: Response) -> dict[str, Any]:
        """Extract offer fields from Best Buy Apollo SSR push payloads.

        Modern PDPs embed ``customerPrice`` / ``skuId`` / ``bsin`` inside
        ``ApolloSSRDataTransport`` script pushes that are not bare JSON, so
        ``json.loads`` on the whole script fails. Prefer numeric
        ``price.customerPrice`` event payloads over feature-flag strings.
        """
        product: dict[str, Any] = {}
        price_re = re.compile(
            r'"price"\s*:\s*\{\s*"customerPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        )
        sku_re = re.compile(r'"skuId"\s*:\s*"(\d{5,12})"')
        bsin_re = re.compile(r'"bsin"\s*:\s*"([A-Z0-9]{6,16})"')
        name_re = re.compile(
            r'"name"\s*:\s*\{\s*"short"\s*:\s*"((?:\\.|[^"\\])*)"',
            re.I,
        )
        for raw in response.css("script::text").getall():
            text = raw or ""
            if "customerPrice" not in text:
                continue
            if "ApolloSSRDataTransport" not in text and '"price"' not in text:
                continue
            price_match = price_re.search(text)
            if price_match and "customerPrice" not in product:
                amount = price_match.group(1)
                product["customerPrice"] = amount
                product["price"] = {"customerPrice": amount}
            if "skuId" not in product:
                sku_match = sku_re.search(text)
                if sku_match:
                    product["skuId"] = sku_match.group(1)
                    product["sku"] = sku_match.group(1)
            if "productId" not in product and "bsin" not in product:
                bsin_match = bsin_re.search(text)
                if bsin_match:
                    product["bsin"] = bsin_match.group(1)
                    product["productId"] = bsin_match.group(1)
            if "name" not in product and "title" not in product:
                name_match = name_re.search(text)
                if name_match:
                    try:
                        product["name"] = json.loads(f'"{name_match.group(1)}"')
                    except json.JSONDecodeError:
                        product["name"] = unescape(name_match.group(1))
            if "customerPrice" in product and "skuId" in product and "name" in product:
                break
        return product

    @classmethod
    def _find_product_dict(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            types = value.get("@type")
            if types == "Product" or (isinstance(types, list) and "Product" in types):
                return value
            if any(k in value for k in ("productId", "skuId", "productSku")) and any(
                k in value for k in ("name", "title", "offers", "price", "images")
            ):
                return value
            for child in value.values():
                found = cls._find_product_dict(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_product_dict(child)
                if found:
                    return found
        return None

    @staticmethod
    def _ensure_product_page(response: Response) -> None:
        title = (response.css("title::text").get() or "").casefold()
        if "select your country" in title or response.css("a.us-link").get():
            raise ParseError(
                "Best Buy retornou a página internacional de país; "
                "não foi possível carregar o produto"
            )
        if "page not found" in title:
            raise ParseError("Página de produto Best Buy não encontrada")

    @staticmethod
    def _offer_data(product: dict[str, Any]) -> dict[str, Any]:
        for key in (
            "offers",
            "offer",
            "selectedOffer",
            "pricing",
            "priceInfo",
            "price",
        ):
            value = product.get(key)
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and any(
                        item.get(field) not in (None, "")
                        for field in (
                            "price",
                            "currentPrice",
                            "salePrice",
                            "customerPrice",
                        )
                    ):
                        return item
                for item in value:
                    if isinstance(item, dict):
                        return item
        return {}

    @classmethod
    def _product_id(cls, product: dict[str, Any], url: str) -> str:
        value = cls._first_value(product, "productId", "productIdCode", "bsin")
        if value is None:
            parts = [part for part in urlparse(url).path.split("/") if part]
            # /product/{slug}/{bsin}/sku/{skuId} → prefer bsin (segment before sku)
            if parts and parts[-1].casefold() == "sku" and len(parts) >= 2:
                value = parts[-2]
            elif len(parts) >= 2 and parts[-2].casefold() == "sku":
                value = parts[-3] if len(parts) >= 3 else parts[-1]
            else:
                value = parts[-1] if parts else None
            if isinstance(value, str) and value.casefold().endswith(".p"):
                value = value[:-2]
        if value is None:
            value = cls._requested_sku_id(url)
        if not value:
            raise ParseError("Identificador do produto não encontrado")
        return str(value).strip()

    @staticmethod
    def _requested_sku_id(url: str) -> str | None:
        """Numeric Best Buy SKU from query or legacy ``/site/.../{sku}.p`` path."""
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        for key, value in pairs:
            if key.lower() == "skuid" and value.strip():
                return value.strip()
        match = re.search(r"/(\d{5,10})\.p(?:/|$)", urlsplit(url).path or "", re.I)
        return match.group(1) if match else None

    @classmethod
    def _ensure_sku_consistency(
        cls, response: Response, product: dict[str, Any]
    ) -> None:
        """Fail closed when a legacy SKU URL redirects to an unrelated PDP."""
        request_url = ""
        if response.request is not None:
            request_url = str(getattr(response.request, "url", "") or "")
        requested = cls._requested_sku_id(request_url) or cls._requested_sku_id(
            response.url
        )
        if not requested:
            return
        actual = cls._string(cls._first_value(product, "sku", "skuId", "productSku"))
        if actual and actual != requested:
            raise ParseError(
                "Best Buy redirecionou para SKU diferente do solicitado "
                f"(pedido={requested}, página={actual})"
            )

    @classmethod
    def _availability(
        cls,
        response: Response,
        offer: dict[str, Any],
        product: dict[str, Any],
        *,
        price: Decimal | None = None,
    ) -> tuple[Availability, str]:
        """Normalize Best Buy stock — not shipping/location fulfillment.

        Brazilian price comparison needs the US offer price even when shipping
        to Brazil, ZIP-based delivery, or store pickup is unavailable. Only
        clear sold-out / discontinued offer signals mark the product as
        out_of_stock. A valid extracted price is enough for ``available``.
        """
        html = response.text
        if cls._clear_sold_out_signal(response, html):
            return "out_of_stock", "fulfillment-button-state"

        raw = str(
            cls._first_value(offer, "availability", "stockStatus", "status") or ""
        ).lower()
        compact = re.sub(r"[\s/_-]+", "", raw)
        # Ignore vague "unavailable" / shipping copy — only clear stock tokens.
        if any(
            token in compact
            for token in (
                "outofstock",
                "soldout",
                "discontinued",
                "nolongeravailable",
            )
        ):
            return "out_of_stock", "product-offer-state"
        for key in ("available", "inStock", "isAvailable"):
            if product.get(key) is False:
                return "out_of_stock", "product-state"
            if product.get(key) is True:
                return "available", "product-state"
        if any(token in compact for token in ("instock", "instoreonly", "onlineonly")):
            return "available", "product-offer-state"
        if any(token in raw for token in ("available", "ready", "preorder", "presale")):
            return "available", "product-offer-state"
        if response.css("[data-testid*='add-to-cart'], button.add-to-cart").get():
            return "available", "purchase-control"
        # Price-first: active US list/sale price is a usable commercial offer
        # even when fulfillment UI is location-gated (ZIP, pickup, intl shipping).
        if price is not None and price > 0:
            return "available", "active-offer-price"
        return "unavailable", "no-availability-signal"

    @staticmethod
    def _clear_sold_out_signal(response: Response, html: str) -> bool:
        """True only for explicit offer stock-out — not shipping unavailability."""
        if re.search(r'"buttonState"\s*:\s*"SOLD_OUT"', html) or re.search(
            r'buttonState"\s*:\s*"SOLD_OUT"', html
        ):
            return True
        if re.search(r"\bNotify Me\b", html, re.I) and re.search(
            r"\bSold Out\b", html, re.I
        ):
            return True
        if response.css("[data-testid*='add-to-cart'], button.add-to-cart").get():
            return False
        return bool(
            re.search(r"\b(sold\s*out|out\s*of\s*stock|discontinued)\b", html, re.I)
        )

    @classmethod
    def _seller(
        cls, response: Response, offer: dict[str, Any], product: dict[str, Any]
    ) -> str | None:
        value = cls._first_value(offer, "seller", "sellerName", "merchant")
        if isinstance(value, dict):
            value = cls._first_value(value, "name", "displayName", "sellerName")
        return cls._clean_short(
            value or cls._first_value(product, "seller", "sellerName") or "Best Buy"
        )

    @classmethod
    def _installment(
        cls,
        offer: dict[str, Any],
        *,
        carrier_locked: bool,
    ) -> tuple[Decimal | None, int | None]:
        # Carrier installment plans are not comparable device pricing for BR buyers.
        if carrier_locked:
            return None, None
        raw_price = cls._first_value(
            offer, "installmentPrice", "monthlyPrice", "paymentAmount"
        )
        raw_count = cls._first_value(offer, "installmentCount", "months", "term")
        return cls._optional_money(raw_price), cls._optional_int(raw_count)

    @classmethod
    def _variants(cls, product: dict[str, Any], title: str | None) -> dict[str, str]:
        """Identity dimensions used for BR comparison — never US carrier names."""
        result: dict[str, str] = {}
        for key, labels in (
            ("color", ("color", "Color")),
            ("storage", ("storage", "Built-in Storage", "Storage")),
            ("condition", ("condition", "Condition")),
        ):
            value = next(
                (
                    product.get(label)
                    for label in labels
                    if product.get(label) not in (None, "")
                ),
                None,
            )
            if isinstance(value, dict):
                value = cls._first_value(value, "name", "value", "label")
            cleaned = cls._clean_short(value)
            if cleaned and not cls._is_us_carrier_name(cleaned):
                result[key] = cleaned
        for key, value in cls._variants_from_title(title).items():
            result.setdefault(key, value)
        return result

    @classmethod
    def _carrier_metadata(
        cls, product: dict[str, Any], title: str | None
    ) -> dict[str, Any]:
        """Preserve US carrier lock status without using it as product identity."""
        raw = cls._first_value(product, "carrier", "Carrier", "network", "Network")
        if isinstance(raw, dict):
            raw = cls._first_value(raw, "name", "value", "label")
        label = cls._clean_short(raw)
        if not label and title:
            match = re.search(r"\(([^)]+)\)\s*$", title)
            if match:
                label = cls._clean_short(match.group(1))
        if not label:
            return {}
        lowered = label.casefold()
        if lowered in {"unlocked", "carrier unlocked", "sim free"}:
            return {"carrier": "Unlocked", "carrier_locked": False}
        if cls._is_us_carrier_name(label):
            return {"carrier": label, "carrier_locked": True}
        return {}

    @classmethod
    def _is_us_carrier_name(cls, value: str) -> bool:
        lowered = value.casefold().strip()
        if lowered in _US_CARRIERS:
            return True
        return any(
            carrier in lowered for carrier in ("at&t", "verizon", "t-mobile", "tmobile")
        )

    @classmethod
    def _variants_from_title(cls, title: str | None) -> dict[str, str]:
        if not title:
            return {}
        resolved = resolve_attributes(
            ("color", "storage"),
            title=title,
        )
        result: dict[str, str] = {}
        color = resolved.value("color")
        storage = resolved.value("storage")
        if color and color.casefold() not in {"wireless", "bluetooth", "noise"}:
            result["color"] = color
        if storage:
            # Preserve Best Buy's compact gallery-style token when possible.
            result["storage"] = storage.replace(" ", "")
        return result

    @classmethod
    def _description(cls, product: dict[str, Any]) -> str | None:
        raw = cls._first_value(product, "description", "longDescription")
        text = cls._plain_text(raw)
        if not text or cls._looks_like_page_noise(text) or len(text) > 2000:
            return None
        return text

    @staticmethod
    def _specifications(product: dict[str, Any]) -> dict[str, Any]:
        for key in (
            "specifications",
            "specs",
            "specificationGroups",
            "technicalSpecifications",
        ):
            value = product.get(key)
            if isinstance(value, dict):
                return {
                    str(name): val
                    for name, val in value.items()
                    if isinstance(name, str)
                    and not BestBuySpider._looks_like_page_noise(str(name))
                    and not BestBuySpider._looks_like_page_noise(str(val))
                }
            if isinstance(value, list):
                result: dict[str, Any] = {}
                for item in value:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("key") or item.get("label")
                        val = item.get("value") or item.get("values")
                        if (
                            name
                            and val is not None
                            and not BestBuySpider._looks_like_page_noise(str(name))
                            and not BestBuySpider._looks_like_page_noise(str(val))
                        ):
                            result[str(name)] = val
                if result:
                    return result
        return {}

    @classmethod
    def _brand(cls, product: dict[str, Any]) -> str | None:
        value = product.get("brand")
        if isinstance(value, dict):
            value = cls._first_value(value, "name", "label")
        return cls._clean_short(value)

    @staticmethod
    def _first_value(data: dict[str, Any], *keys: str) -> Any:
        return next(
            (data[key] for key in keys if data.get(key) not in (None, "")), None
        )

    @staticmethod
    def _string(value: Any) -> str | None:
        return str(value).strip() if value is not None and str(value).strip() else None

    @classmethod
    def _clean_short(cls, value: Any, *, max_len: int = 80) -> str | None:
        if value is None:
            return None
        text = unescape(str(value)).strip()
        text = " ".join(text.split())
        if not text or len(text) > max_len or cls._looks_like_page_noise(text):
            return None
        return text

    @staticmethod
    def _looks_like_page_noise(value: str) -> bool:
        lowered = value.casefold()
        markers = (
            "self.__next_f",
            "graphql",
            "fragment ",
            "query ",
            "productbyskuId",
            "notifyonnetworkstatuschange",
            "apollo",
            "buttonstate",
            "special offers",
            "see all features",
            "questions & answers",
            "related item",
            "sponsored",
        )
        if any(marker in lowered for marker in markers):
            return True
        if value.count("{") > 2 or value.count("}") > 2:
            return True
        if "$" in value and "per month" in lowered:
            return True
        return False

    @classmethod
    def _money(cls, value: Any) -> Decimal:
        if value is None:
            return parse_money(None, cls.currency)
        try:
            amount = Decimal(str(value).replace(",", ""))
            if amount > 0:
                return amount.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            pass
        return parse_money(str(value), cls.currency)

    @classmethod
    def _optional_money(cls, value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            amount = cls._money(value)
            return amount if amount > 0 else None
        except Exception:
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            parsed = int(str(value).split()[0])
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _plain_text(value: Any) -> str | None:
        if not value:
            return None
        text = Selector(text=unescape(str(value))).xpath("string(.)").get() or ""
        return " ".join(text.split()) or None

    @staticmethod
    def _image_values(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                result.extend(BestBuySpider._image_values(item))
            return result
        if isinstance(value, dict):
            return BestBuySpider._image_values(
                BestBuySpider._first_value(
                    value, "url", "src", "contentUrl", "image", "large", "standard"
                )
            )
        return []

    @staticmethod
    def _dedupe_images(values: list[str], base_url: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            urls = BestBuySpider.normalize_image_urls(value, base_url=base_url)
            if not urls:
                continue
            url = urls[0]
            key = re.sub(r"([?&](?:width|height|w|h)=\d+)", "", url, flags=re.I)
            if key in seen or not re.match(r"https?://", url):
                continue
            seen.add(key)
            result.append(url)
        return result

    @staticmethod
    def _location_dependent(response: Response) -> bool:
        text = " ".join(response.css("body ::text").getall()).casefold()
        return bool(
            re.search(r"\b(zip code|postal code|choose a store|pickup)\b", text)
        )
