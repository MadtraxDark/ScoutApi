"""AliExpress adapter — offer / details / images from MTop PDP payloads."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

from scrapy.http import Response

from ...core.exceptions import MissingPriceError, ParseError, RequestError
from ...core.fingerprints import canonicalize_url
from ...models.product import (
    ProductDetails,
    ProductOffer,
    ProductPriceItem,
    compose_product_price_item,
)
from ...utils.parsing import parse_money
from ...utils.product_attributes import (
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]

_ITEM_PATH = re.compile(
    r"/item/(?:[^/]+/)?(?P<item_id>\d{6,})\.html",
    re.IGNORECASE,
)
_SKU_IN_PDP_NPI = re.compile(r"(?<!\d)(1\d{13,18})(?!\d)")
_INSTALLMENT = re.compile(
    r"(?P<price>\d[\d.,]*)\s*x\s*(?P<count>\d+)\s*(?:meses|months|x)?",
    re.IGNORECASE,
)
_DISCOUNT_PCT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

_BLOCK_MARKERS = (
    "rgv587",
    "fail_sys_user_validate",
    "fail_sys_token_empty",
    "_____tmd_____",
    "x5secdata",
    "punish",
    "baxia-dialog",
    "nc_wrapper",
    "slide-to-validate",
)


class AliExpressSpider(BaseStoreSpider):
    """Parse AliExpress PDP data captured from ``mtop.aliexpress.pdp.pc.query``."""

    name = "aliexpress"
    store, country, currency = "aliexpress", "BR", "BRL"
    supports_images = True
    allowed_domains = [
        "aliexpress.com",
        "www.aliexpress.com",
        "pt.aliexpress.com",
        "m.aliexpress.com",
        "es.aliexpress.com",
        "fr.aliexpress.com",
        "de.aliexpress.com",
        "nl.aliexpress.com",
        "it.aliexpress.com",
        "pl.aliexpress.com",
        "ko.aliexpress.com",
        "ja.aliexpress.com",
        "ar.aliexpress.com",
        "th.aliexpress.com",
        "vi.aliexpress.com",
        "id.aliexpress.com",
        "he.aliexpress.com",
        "tr.aliexpress.com",
        "ru.aliexpress.com",
        "uk.aliexpress.com",
    ]
    start_urls: list[str] = []

    def prepare_fetch_url(self, url: str) -> str:
        """Drop SERP tracking while preserving sku selection hints for the PDP."""
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=False)
        kept: dict[str, list[str]] = {}
        for key in ("sku_id", "skuId", "skuid"):
            if key in query and query[key]:
                kept["sku_id"] = [str(query[key][0])]
                break
        if "sku_id" not in kept:
            sku_hint = self._sku_hint_from_query(query)
            if sku_hint:
                kept["sku_id"] = [sku_hint]
        new_query = "&".join(f"{k}={v[0]}" for k, v in kept.items())
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                parsed.path,
                "",
                new_query,
                "",
            )
        )

    def parse_product(self, response: Response) -> ProductPriceItem:
        offer = self.extract_offer(response)
        details = self.extract_details(response)
        shipping = self._shipping_price(self._pdp_result(response))
        return compose_product_price_item(
            offer,
            details,
            shipping_price=shipping,
        )

    def extract_offer(self, response: Response) -> ProductOffer:
        result = self._pdp_result(response)
        url_ids = self._url_identity(response.url)
        product_id = self._product_id(result, url_ids)
        if not product_id:
            raise ParseError(
                "Identificador do produto (item/product id) não encontrado"
            )

        selected = self._selected_sku(result, url_ids)
        sku_id = self._id_str(selected.get("skuId") or selected.get("skuIdStr"))
        price_info = self._price_info_for_sku(result, sku_id)
        price, currency, price_source = self._sale_price(price_info)
        original, original_source = self._original_price(price_info, price)
        discount = self._discount_percentage(price_info, price, original)
        installment_price, installment_count, installment_source = self._installment(
            result
        )
        availability, availability_source = self._availability(result, selected)
        seller, seller_source = self._seller(result)
        seller_meta = self._seller_metadata(result)

        source = {
            "price": price_source,
            "original_price": original_source,
            "discount": "structured" if discount is not None else "not-found",
            "installment": installment_source,
            "availability": availability_source,
            "seller": seller_source,
            "product_id": "item_id",
            "sku": "selected_sku_id" if sku_id else "item_id",
            "currency": "price-payload",
        }
        metadata: dict[str, Any] = {
            "item_id": product_id,
            "sku_id": sku_id,
            "market": {
                "locale": self._locale(result),
                "region": self._region(result),
                "storefront": (urlparse(response.url).hostname or "").lower(),
            },
            "source": source,
            **seller_meta,
        }
        promo = self._promotion_metadata(result)
        if promo:
            metadata["promotions"] = promo
        shipping_meta = self._shipping_metadata(result)
        if shipping_meta:
            metadata["shipping"] = shipping_meta

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=product_id,
            sku=sku_id or product_id,
            seller=seller,
            url=response.url,
            canonical_url=self._canonical_product_url(
                response.url, product_id, sku_id=sku_id
            ),
            currency=currency or self.currency,
            price=price,
            original_price=original,
            discount_percentage=discount,
            pix_price=None,
            installment_price=installment_price,
            installment_count=installment_count,
            availability=availability,
            available=availability == "available",
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        result = self._pdp_result(response)
        url_ids = self._url_identity(response.url)
        product_id = self._product_id(result, url_ids)
        title = self._title(result)
        if not product_id or not title:
            raise ParseError("Identidade do produto não encontrada")

        selected = self._selected_sku(result, url_ids)
        sku_id = self._id_str(selected.get("skuId") or selected.get("skuIdStr"))
        specifications = self._specifications(result)
        brand = self._brand(specifications)
        model_name = self._catalog_model(specifications)
        variant = self._variant_label(result, selected)
        description = self._description(result)
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={"brand": brand, "model": model_name},
            title=title,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        attribute_sources = resolved.found_sources()
        source = {
            "title": "mtop-product-title",
            "brand": attribute_sources.get(
                "brand", "product-attributes" if brand else "not-found"
            ),
            "model": attribute_sources.get(
                "model", "product-attributes" if model_name else "not-found"
            ),
            "variant": "selected-sku" if variant else "not-found",
            "specifications": "product-prop-pc" if specifications else "not-found",
            "description": "mtop-desc" if description else "not-found",
            **{
                key: source_name
                for key, source_name in attribute_sources.items()
                if key not in {"brand", "model"}
            },
        }
        if resolved.category:
            source["category"] = resolved.category

        return ProductDetails(
            product_id=product_id,
            sku=sku_id or product_id,
            gtin=self._gtin(specifications),
            title=title,
            brand=brand or resolved.value("brand"),
            model=resolved.value("model"),
            variant=variant or format_identity_variant(resolved),
            description=description,
            specifications=specifications,
            metadata={
                "item_id": product_id,
                "sku_id": sku_id,
                "source": source,
            },
        )

    def extract_images(self, response: Response) -> list[str]:
        result = self._pdp_result(response)
        url_ids = self._url_identity(response.url)
        selected = self._selected_sku(result, url_ids)
        sku_id = self._id_str(selected.get("skuId") or selected.get("skuIdStr"))
        return self._gallery_urls(result, sku_id=sku_id, base_url=response.url)

    # ------------------------------------------------------------------ payload

    def _pdp_result(self, response: Response) -> dict[str, Any]:
        self._ensure_product_page(response)
        payload = self._pdp_payload(response)
        self._ensure_mtop_success(payload, url=response.url)
        result = self._result_from_payload(payload)
        if not result:
            raise ParseError("Payload MTop AliExpress sem componente de produto")
        return result

    @classmethod
    def _ensure_product_page(cls, response: Response) -> None:
        url = (response.url or "").casefold()
        text = response.text or ""
        folded = text[:12_000].casefold()
        title = ""
        try:
            title = (response.css("title::text").get() or "").casefold()
        except (AttributeError, ValueError, TypeError):
            title = ""

        if (
            cls._looks_like_pdp_json(text)
            or response.css("script[data-aliexpress-pdp]").get()
        ):
            return

        haystack = f"{title} {folded} {url}"
        if any(marker in haystack for marker in _BLOCK_MARKERS):
            raise RequestError(
                "AliExpress apresentou desafio anti-bot / validação",
                code="UPSTREAM_BLOCKED",
                url=response.url,
                upstream_status=403,
                retryable=True,
            )
        if "login" in url and ("passport" in url or "login.aliexpress" in url):
            raise RequestError(
                "AliExpress apresentou parede de autenticação",
                code="AUTH_REQUIRED",
                url=response.url,
                upstream_status=401,
                retryable=True,
            )

    @classmethod
    def _pdp_payload(cls, response: Response) -> dict[str, Any]:
        for raw in response.css("script[data-aliexpress-pdp]::text").getall():
            parsed = cls._parse_json_blob(raw)
            if parsed:
                return parsed

        text = (response.text or "").strip()
        if cls._looks_like_pdp_json(text):
            parsed = cls._parse_json_blob(text)
            if parsed:
                return parsed

        # CSR shell / empty runParams without intercepted MTop.
        if "runparams" in text.casefold() and "product_title" not in text.casefold():
            raise RequestError(
                "AliExpress retornou shell CSR sem payload de produto",
                code="UPSTREAM_BLOCKED",
                url=response.url,
                upstream_status=403,
                retryable=True,
            )
        raise ParseError("Payload PDP AliExpress não encontrado")

    @classmethod
    def _ensure_mtop_success(cls, payload: dict[str, Any], *, url: str | None) -> None:
        ret = payload.get("ret")
        messages: list[str] = []
        if isinstance(ret, list):
            messages = [str(item) for item in ret]
        elif isinstance(ret, str):
            messages = [ret]
        joined = " ".join(messages).casefold()
        if any(
            token in joined
            for token in (
                "fail_sys_user_validate",
                "rgv587",
                "fail_sys_token_empty",
                "fail_sys_session_expired",
            )
        ):
            raise RequestError(
                f"AliExpress MTop bloqueou a leitura ({messages[:1] or 'FAIL'})",
                code="UPSTREAM_BLOCKED",
                url=url,
                upstream_status=403,
                retryable=True,
            )
        if messages and not any("success" in item.casefold() for item in messages):
            # Non-success without known block markers — still not a PDP.
            data = payload.get("data")
            if not data:
                raise RequestError(
                    f"AliExpress MTop sem dados de produto ({messages[:1]})",
                    code="UPSTREAM_BLOCKED",
                    url=url,
                    upstream_status=403,
                    retryable=True,
                )

    @classmethod
    def _result_from_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, dict):
            result = data.get("result")
            if isinstance(result, dict) and (
                "PRODUCT_TITLE" in result or "PRICE" in result or "SKU" in result
            ):
                return result
            if "PRODUCT_TITLE" in data or "PRICE" in data or "SKU" in data:
                return data
        if "PRODUCT_TITLE" in payload or "PRICE" in payload:
            return payload
        return {}

    @staticmethod
    def _looks_like_pdp_json(text: str) -> bool:
        head = (text or "")[:4_000]
        return (
            "PRODUCT_TITLE" in head
            or "mtop.aliexpress.pdp" in head
            or '"skuIdStrPriceInfoMap"' in head
            or '"targetSkuPriceInfo"' in head
        )

    @staticmethod
    def _parse_json_blob(raw: str) -> dict[str, Any] | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("mtopjsonp"):
            match = re.search(r"\((\{.*\})\)\s*$", text, re.S)
            if match:
                text = match.group(1)
        try:
            value: Any = json.loads(text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    # ------------------------------------------------------------------ identity

    @classmethod
    def _url_identity(cls, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=False)
        out: dict[str, str] = {}
        item_id = cls._item_id_from_url(url)
        if item_id:
            out["item_id"] = item_id
        for key in ("sku_id", "skuId", "skuid"):
            values = query.get(key)
            if values and str(values[0]).strip().isdigit():
                out["sku_id"] = str(values[0]).strip()
                break
        if "sku_id" not in out:
            hint = cls._sku_hint_from_query(query)
            if hint:
                out["sku_id"] = hint
        return out

    @staticmethod
    def _item_id_from_url(url: str) -> str | None:
        match = _ITEM_PATH.search(urlparse(url).path or "")
        return match.group("item_id") if match else None

    @staticmethod
    def _sku_hint_from_query(query: dict[str, list[str]]) -> str | None:
        for key in ("pdp_npi", "pdpNPI", "pdpnpi"):
            values = query.get(key)
            if not values:
                continue
            decoded = unquote(values[0])
            # Prefer the long numeric token used as skuId in live pdp_npi blobs.
            matches = _SKU_IN_PDP_NPI.findall(decoded)
            if matches:
                # Last long id in the npi blob is typically the selected sku.
                return str(matches[-1])
        return None

    @classmethod
    def _product_id(cls, result: dict[str, Any], url_ids: dict[str, str]) -> str | None:
        price = cls._as_dict(result.get("PRICE"))
        global_data = cls._global_data(result)
        for candidate in (
            price.get("productId"),
            global_data.get("productId"),
            url_ids.get("item_id"),
        ):
            value = cls._id_str(candidate)
            if value:
                return value
        return None

    @classmethod
    def _selected_sku(
        cls, result: dict[str, Any], url_ids: dict[str, str]
    ) -> dict[str, Any]:
        sku_block = cls._as_dict(result.get("SKU"))
        paths = sku_block.get("skuPaths")
        path_rows = paths if isinstance(paths, list) else []

        wanted = url_ids.get("sku_id")
        if wanted:
            for row in path_rows:
                if not isinstance(row, dict):
                    continue
                if cls._id_str(row.get("skuId") or row.get("skuIdStr")) == wanted:
                    return row

        price_block = (
            cls._as_dict(result.get("PRICE"))
        )
        selected_id = cls._id_str(
            sku_block.get("selectedSkuIdStr")
            or sku_block.get("selectedSkuId")
            or price_block.get("selectedSkuId")
        )
        if selected_id:
            for row in path_rows:
                if not isinstance(row, dict):
                    continue
                if cls._id_str(row.get("skuId") or row.get("skuIdStr")) == selected_id:
                    return row
            return {
                "skuId": selected_id,
                "skuIdStr": selected_id,
                "salable": bool(sku_block.get("selectedSkuSaleable", True)),
                "skuAttr": sku_block.get("selectedSkuAttr"),
            }

        # Stable default: first sellable path — never cheapest.
        for row in path_rows:
            if not isinstance(row, dict):
                continue
            if row.get("salable") is False:
                continue
            stock = row.get("skuStock")
            if stock is not None:
                try:
                    if int(stock) <= 0:
                        continue
                except (TypeError, ValueError):
                    pass
            return row
        if path_rows and isinstance(path_rows[0], dict):
            return path_rows[0]
        return {}

    @classmethod
    def _canonical_product_url(
        cls, url: str, product_id: str, *, sku_id: str | None = None
    ) -> str:
        parsed = urlparse(url)
        host = (parsed.netloc or "pt.aliexpress.com").lower()
        path = f"/item/{product_id}.html"
        query = f"sku_id={sku_id}" if sku_id else ""
        return canonicalize_url(
            urlunparse((parsed.scheme or "https", host, path, "", query, ""))
        )

    # ------------------------------------------------------------------ pricing

    @classmethod
    def _price_info_for_sku(
        cls, result: dict[str, Any], sku_id: str | None
    ) -> dict[str, Any]:
        price = cls._as_dict(result.get("PRICE"))
        if sku_id:
            for key in ("skuIdStrPriceInfoMap", "skuPriceInfoMap"):
                mapping = price.get(key)
                if isinstance(mapping, dict) and sku_id in mapping:
                    info = mapping[sku_id]
                    if isinstance(info, dict):
                        return info
        target = price.get("targetSkuPriceInfo")
        return target if isinstance(target, dict) else {}

    @classmethod
    def _sale_price(cls, price_info: dict[str, Any]) -> tuple[Decimal, str, str]:
        currency = cls._currency_from_price_info(price_info) or "BRL"
        for key, source in (
            ("salePrice", "target-sku-sale-price"),
            ("amount", "target-sku-amount"),
        ):
            value = price_info.get(key)
            if isinstance(value, dict) and value.get("value") is not None:
                try:
                    amount = Decimal(str(value["value"]))
                except (InvalidOperation, TypeError, ValueError):
                    amount = None
                if amount is not None and amount > 0:
                    cur = str(value.get("currency") or currency)
                    return amount, cur, source
        local = price_info.get("salePriceLocal")
        if isinstance(local, str) and "|" in local:
            # "R$8.819,02|8819|02"
            parts = local.split("|")
            if len(parts) >= 3 and parts[1].isdigit():
                try:
                    amount = Decimal(f"{parts[1]}.{parts[2]}")
                    if amount > 0:
                        return amount, currency, "sale-price-local"
                except (InvalidOperation, TypeError, ValueError):
                    pass
        for key, source in (
            ("salePriceString", "sale-price-string"),
            ("formatedAmount", "formatted-amount"),
        ):
            raw = price_info.get(key)
            if raw:
                try:
                    return parse_money(str(raw), currency), currency, source
                except MissingPriceError:
                    continue
        raise MissingPriceError("Preço da SKU selecionada não encontrado")

    @classmethod
    def _original_price(
        cls, price_info: dict[str, Any], sale: Decimal
    ) -> tuple[Decimal | None, str]:
        original = price_info.get("originalPrice")
        if isinstance(original, dict) and original.get("value") is not None:
            try:
                amount = Decimal(str(original["value"]))
            except (InvalidOperation, TypeError, ValueError):
                amount = None
            if amount is not None and amount > sale:
                return amount, "original-price"
            if amount is not None and amount == sale:
                return None, "not-found"
        if isinstance(original, dict) and original.get("formatedAmount"):
            currency = str(original.get("currency") or "BRL")
            try:
                amount = parse_money(str(original["formatedAmount"]), currency)
            except MissingPriceError:
                amount = None
            if amount is not None and amount > sale:
                return amount, "original-price-formatted"
        return None, "not-found"

    @classmethod
    def _discount_percentage(
        cls,
        price_info: dict[str, Any],
        sale: Decimal,
        original: Decimal | None,
    ) -> Decimal | None:
        raw = price_info.get("discount")
        if isinstance(raw, str):
            match = _DISCOUNT_PCT.search(raw)
            if match:
                try:
                    return Decimal(match.group(1).replace(",", "."))
                except InvalidOperation:
                    pass
        if original is not None and original > 0 and sale < original:
            return ((original - sale) / original * Decimal("100")).quantize(
                Decimal("0.01")
            )
        return None

    @classmethod
    def _currency_from_price_info(cls, price_info: dict[str, Any]) -> str | None:
        original = price_info.get("originalPrice")
        if isinstance(original, dict) and original.get("currency"):
            return str(original["currency"]).upper()
        sale = price_info.get("salePrice")
        if isinstance(sale, dict) and sale.get("currency"):
            return str(sale["currency"]).upper()
        text = str(price_info.get("salePriceString") or "")
        if "R$" in text or "BRL" in text.upper():
            return "BRL"
        if "$" in text and "R$" not in text:
            return "USD"
        return None

    @classmethod
    def _installment(
        cls, result: dict[str, Any]
    ) -> tuple[Decimal | None, int | None, str]:
        block = cls._as_dict(result.get("INSTALLMENT"))
        text = str(block.get("text") or "")
        if not text:
            return None, None, "not-found"
        match = _INSTALLMENT.search(text.replace(" ", ""))
        if not match:
            match = _INSTALLMENT.search(text)
        if not match:
            return None, None, "not-found"
        try:
            price = parse_money(match.group("price"), "BRL")
            count = int(match.group("count"))
        except (MissingPriceError, ValueError):
            return None, None, "not-found"
        if count <= 0 or price <= 0:
            return None, None, "not-found"
        return price, count, "installment-block"

    # ---------------------------------------------------------- availability / seller

    @classmethod
    def _availability(
        cls, result: dict[str, Any], selected: dict[str, Any]
    ) -> tuple[Availability, str]:
        if selected.get("salable") is False:
            return "out_of_stock", "sku-salable-false"
        stock = selected.get("skuStock")
        if stock is not None:
            try:
                if int(stock) <= 0:
                    return "out_of_stock", "sku-stock"
            except (TypeError, ValueError):
                pass
        quantity = (
            cls._as_dict(result.get("QUANTITY_PC"))
        )
        total = quantity.get("totalAvailableInventory")
        if total is not None:
            try:
                if int(str(total).strip()) <= 0:
                    return "out_of_stock", "total-inventory"
            except (TypeError, ValueError):
                pass
        sku_block = cls._as_dict(result.get("SKU"))
        if sku_block.get("selectedSkuSaleable") is False:
            return "out_of_stock", "selected-sku-saleable"
        title = cls._title(result).casefold() if cls._title(result) else ""
        if "não está mais disponível" in title or "no longer available" in title:
            return "unavailable", "title-unavailable"
        return "available", "sku-stock"

    @classmethod
    def _seller(cls, result: dict[str, Any]) -> tuple[str | None, str]:
        shop = (
            cls._as_dict(result.get("SHOP_CARD_PC"))
        )
        name = shop.get("storeName") or shop.get("shopName")
        if name and str(name).strip():
            return str(name).strip(), "shop-card"
        seller_info = (
            cls._as_dict(shop.get("sellerInfo"))
        )
        for key in ("storeName", "companyName", "sellerName"):
            value = seller_info.get(key)
            if value and str(value).strip():
                return str(value).strip(), "seller-info"
        return None, "not-found"

    @classmethod
    def _seller_metadata(cls, result: dict[str, Any]) -> dict[str, Any]:
        shop = (
            cls._as_dict(result.get("SHOP_CARD_PC"))
        )
        seller_info = (
            cls._as_dict(shop.get("sellerInfo"))
        )
        meta: dict[str, Any] = {}
        store_num = cls._id_str(seller_info.get("storeNum") or shop.get("storeNum"))
        if store_num:
            meta["store_num"] = store_num
        seller_id = cls._id_str(
            seller_info.get("adminSeq")
            or seller_info.get("companyId")
            or cls._global_data(result).get("sellerId")
        )
        if seller_id:
            meta["seller_id"] = seller_id
        origin = seller_info.get("countryCompleteName") or seller_info.get("country")
        if origin:
            meta["seller_origin"] = str(origin)
        return meta

    # ------------------------------------------------------------------ details helpers

    @classmethod
    def _title(cls, result: dict[str, Any]) -> str:
        block = (
            cls._as_dict(result.get("PRODUCT_TITLE"))
        )
        text = block.get("text") or block.get("title")
        if text:
            return str(text).strip()
        subject = cls._global_data(result).get("subject")
        return str(subject).strip() if subject else ""

    @classmethod
    def _specifications(cls, result: dict[str, Any]) -> dict[str, Any]:
        props = (
            cls._as_dict(result.get("PRODUCT_PROP_PC"))
        )
        out: dict[str, Any] = {}
        for key in ("showedProps", "outerProps"):
            rows = props.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("attrName") or "").strip()
                value = row.get("attrValue")
                if not name or value is None:
                    continue
                text = str(value).strip()
                if text:
                    out[name] = text
        return out

    @classmethod
    def _brand(cls, specifications: dict[str, Any]) -> str | None:
        for key in (
            "Nome da marca",
            "Brand Name",
            "Marca",
            "brand",
            "Fabricante de chipset",
        ):
            value = specifications.get(key)
            if value and str(value).strip():
                text = str(value).strip()
                if text.casefold() in {"nvidia", "amd"} and key.startswith(
                    "Fabricante"
                ):
                    continue
                return text
        return None

    @classmethod
    def _catalog_model(cls, specifications: dict[str, Any]) -> str | None:
        for key in (
            "Model Number",
            "Número do modelo",
            "Model",
            "Modelo",
            "Manufacturer Part Number",
        ):
            value = specifications.get(key)
            if value and str(value).strip():
                return str(value).strip()
        return None

    @classmethod
    def _variant_label(
        cls, result: dict[str, Any], selected: dict[str, Any]
    ) -> str | None:
        sku_block = cls._as_dict(result.get("SKU"))
        selected_attr = str(
            selected.get("skuAttr") or sku_block.get("selectedSkuAttr") or ""
        )
        if not selected_attr:
            return None
        props = sku_block.get("skuProperties")
        if not isinstance(props, list):
            return None
        selected_pairs = {
            left: right
            for part in selected_attr.split(";")
            if ":" in part
            for left, right in [part.split(":", 1)]
        }
        labels: list[str] = []
        for prop in props:
            if not isinstance(prop, dict):
                continue
            prop_id = str(prop.get("skuPropertyId") or "")
            wanted = selected_pairs.get(prop_id)
            if not wanted:
                continue
            values = prop.get("skuPropertyValues")
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                value_id = str(
                    value.get("propertyValueIdLong")
                    or value.get("propertyValueId")
                    or ""
                )
                if value_id != wanted:
                    continue
                name = str(prop.get("skuPropertyName") or "").strip()
                display = str(
                    value.get("propertyValueDisplayName")
                    or value.get("propertyValueName")
                    or ""
                ).strip()
                if name and display:
                    labels.append(f"{name}: {display}")
                elif display:
                    labels.append(display)
                break
        return "; ".join(labels) if labels else None

    @classmethod
    def _description(cls, result: dict[str, Any]) -> str | None:
        block = cls._as_dict(result.get("DESC"))
        for key in ("pcDescUrl", "nativeDescUrl", "msiteDescUrl"):
            value = block.get(key)
            if value:
                # Description body is a separate asset URL — keep reference only.
                return None
        text = block.get("title")
        return str(text).strip() if text else None

    @classmethod
    def _gtin(cls, specifications: dict[str, Any]) -> str | None:
        for key, value in specifications.items():
            lowered = str(key).casefold()
            if any(
                token in lowered for token in ("gtin", "ean", "upc", "código de barras")
            ):
                text = re.sub(r"\D", "", str(value))
                if 8 <= len(text) <= 14:
                    return text
        return None

    # -------------------------------------------------------------- images / shipping

    @classmethod
    def _gallery_urls(
        cls,
        result: dict[str, Any],
        *,
        sku_id: str | None,
        base_url: str,
    ) -> list[str]:
        images = (
            cls._as_dict(result.get("HEADER_IMAGE_PC"))
        )
        ordered: list[Any] = []
        if sku_id:
            sku_map = images.get("skuImagesMap")
            if isinstance(sku_map, dict) and sku_id in sku_map:
                sku_images = sku_map[sku_id]
                if isinstance(sku_images, list):
                    ordered.extend(sku_images)
            current = images.get("currentSkuImages")
            if isinstance(current, list):
                ordered.extend(current)
        for key in ("imagePathList", "imgList", "mainImages", "summImagePathList"):
            value = images.get(key)
            if isinstance(value, list):
                ordered.extend(value)
        if not ordered:
            global_image = cls._global_data(result).get("image")
            if global_image:
                ordered.append(global_image)

        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in ordered:
            url = cls._normalize_image_url(raw, base_url=base_url)
            if not url or url in seen:
                continue
            if cls._is_non_product_image(url):
                continue
            seen.add(url)
            cleaned.append(url)
        return cleaned

    @staticmethod
    def _normalize_image_url(raw: Any, *, base_url: str) -> str | None:
        candidate: Any = raw
        if isinstance(raw, dict):
            candidate = (
                raw.get("imageUrl")
                or raw.get("imgUrl")
                or raw.get("url")
                or raw.get("path")
            )
        if candidate is None:
            return None
        text = str(candidate).strip()
        if not text:
            return None
        if text.startswith("//"):
            text = "https:" + text
        # AliExpress CDN often appends ``_960x960.png`` after the real filename
        # (``hash.png_960x960.png`` → ``hash.png``).
        text = re.sub(
            r"_\d+x\d+\.(?:jpg|jpeg|png|webp)$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        absolute = urljoin(base_url, text)
        return absolute

    @staticmethod
    def _is_non_product_image(url: str) -> bool:
        lowered = url.casefold()
        return any(
            token in lowered
            for token in (
                "avatar",
                "banner",
                "logo",
                "sprite",
                "icon",
                "recommend",
                "placeholder",
                "/kf/sbc534a10bc2044448f9aa271540236800/",  # size-chart UI
            )
        )

    @classmethod
    def _shipping_price(cls, result: dict[str, Any]) -> Decimal | None:
        meta = cls._shipping_metadata(result)
        if not meta:
            return None
        fee = meta.get("shipping_fee")
        if fee == "free" or fee == 0 or fee == 0.0 or fee == "0":
            return Decimal("0.00")
        if isinstance(fee, (int, float, Decimal)):
            amount = Decimal(str(fee))
            return amount if amount >= 0 else None
        if isinstance(fee, str):
            try:
                return parse_money(fee, str(meta.get("currency") or "BRL"))
            except MissingPriceError:
                return None
        return None

    @classmethod
    def _shipping_metadata(cls, result: dict[str, Any]) -> dict[str, Any]:
        ship = (
            cls._as_dict(result.get("SHIPPING"))
        )
        layouts = (
            ship.get("deliveryLayoutInfo") or ship.get("originalLayoutResultList") or []
        )
        if not isinstance(layouts, list) or not layouts:
            return {}
        first = layouts[0]
        if not isinstance(first, dict):
            return {}
        biz = cls._as_dict(first.get("bizData"))
        if not biz:
            return {}
        out: dict[str, Any] = {}
        for key, dest in (
            ("shippingFee", "shipping_fee"),
            ("currency", "currency"),
            ("shipFrom", "ship_from"),
            ("shipFromCode", "ship_from_code"),
            ("shipTo", "ship_to"),
            ("shipToCode", "ship_to_code"),
            ("company", "company"),
        ):
            if biz.get(key) is not None:
                out[dest] = biz.get(key)
        return out

    @classmethod
    def _promotion_metadata(cls, result: dict[str, Any]) -> dict[str, Any]:
        global_data = cls._global_data(result)
        page_price = global_data.get("curPagePriceInfo")
        out: dict[str, Any] = {}
        if isinstance(page_price, dict):
            promo = page_price.get("promotionInfo")
            if isinstance(promo, str) and promo.strip():
                try:
                    parsed = json.loads(promo)
                except json.JSONDecodeError:
                    parsed = {"raw": promo}
                if isinstance(parsed, dict):
                    # Conditional / campaign tags — never fold into ``price``.
                    for key in (
                        "m03_new_user",
                        "n_tag",
                        "disPrice",
                        "u",
                        "d",
                    ):
                        if key in parsed:
                            out[key] = parsed[key]
        coupon = (
            cls._as_dict(result.get("COUPON_BLOCK_PC"))
        )
        if coupon and coupon.get("hideCouponBlock") in (False, "False", "false"):
            out["coupon_block_visible"] = True
        return out

    @classmethod
    def _global_data(cls, result: dict[str, Any]) -> dict[str, Any]:
        block = (
            cls._as_dict(result.get("GLOBAL_DATA"))
        )
        nested = block.get("globalData")
        return nested if isinstance(nested, dict) else block

    @classmethod
    def _locale(cls, result: dict[str, Any]) -> str | None:
        value = cls._global_data(result).get("localStr")
        return str(value) if value else None

    @classmethod
    def _region(cls, result: dict[str, Any]) -> str | None:
        price = cls._as_dict(result.get("PRICE"))
        value = price.get("region")
        return str(value) if value else None

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _id_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return None
        if text.endswith(".0") and text.replace(".", "", 1).isdigit():
            text = text[:-2]
        return text if text.isdigit() or text.isalnum() else text
