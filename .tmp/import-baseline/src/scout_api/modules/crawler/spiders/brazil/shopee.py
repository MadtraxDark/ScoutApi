"""Shopee Brasil adapter — offer / details / images from PDP structured data."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from scrapy.http import Response
from scrapy.selector import Selector

from ...core.exceptions import ParseError, RequestError, shopee_auth_required_error
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.product_attributes import (
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ..base import BaseStoreSpider

Availability = Literal["available", "out_of_stock", "unavailable"]

_PRICE_SCALE = Decimal("100000")
_IMAGE_CDN = "https://down-br.img.susercontent.com/file/"
_URL_IDS = re.compile(
    r"[.-]i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)",
    re.IGNORECASE,
)


class ShopeeSpider(BaseStoreSpider):
    """Parse Shopee BR product pages from `/api/v4/pdp/get_pc`-shaped data."""

    name = "shopee"
    store, country, currency = "shopee", "BR", "BRL"
    supports_images = True
    allowed_domains = ["shopee.com.br", "www.shopee.com.br"]
    start_urls: list[str] = []

    def extract_offer(self, response: Response) -> ProductOffer:
        self._ensure_product_page(response)
        payload = self._pdp_payload(response)
        item = self._item(payload)
        url_ids = self._url_identity(response.url)
        shop_id = self._id_str(
            item.get("shop_id") or item.get("shopid")
        ) or url_ids.get("shop_id")
        item_id = self._id_str(
            item.get("item_id") or item.get("itemid")
        ) or url_ids.get("item_id")
        if not item_id:
            raise ParseError("Identificador do produto (item_id) não encontrado")

        model = self._selected_model(item, payload, response.url, url_ids)
        price, price_source = self._price(payload, item, model)
        original, original_source = self._original_price(payload, item, model, price)
        discount = self._discount_percentage(payload, item, model, price, original)
        model_id = self._id_str(
            (model or {}).get("model_id") or (model or {}).get("modelid")
        )
        pix_price, pix_source, pricing_meta, pricing_sources = self._final_pricing(
            payload, model_id=model_id, list_price=price
        )
        installment_price, installment_count, installment_source = self._installment(
            payload
        )
        availability, availability_source = self._availability(item, model, payload)
        seller, seller_source = self._seller(payload, item)
        sku = self._sku(model, item_id)

        source = {
            "price": price_source,
            "original_price": original_source,
            "discount": "structured" if discount is not None else "not-found",
            "pix_price": pix_source,
            "installment": installment_source,
            "availability": availability_source,
            "seller": seller_source,
            "product_id": "item_id",
            "sku": "model_id" if model_id else "item_id",
            **pricing_sources,
        }
        metadata: dict[str, Any] = {
            "shop_id": shop_id,
            "item_id": str(item_id),
            "model_id": model_id,
            "display_model_id": url_ids.get("display_model_id"),
            "model_selection_logic": url_ids.get("model_selection_logic"),
            "source": source,
        }
        if pricing_meta:
            metadata["pricing"] = pricing_meta

        from ...utils.timed_promotion import shopee_flash_promotion

        promo = shopee_flash_promotion(
            payload,
            model=model,
            model_id=model_id,
            product_id=str(item_id),
        )
        if promo:
            metadata["promotion"] = promo
            source["promotion"] = promo.get("source", "flash_sale")
        else:
            source["promotion"] = "absent"

        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=str(item_id),
            sku=sku,
            seller=seller,
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=self.currency,
            price=price,
            original_price=original,
            discount_percentage=discount,
            pix_price=pix_price,
            installment_price=installment_price,
            installment_count=installment_count,
            availability=availability,
            available=availability == "available",
            metadata=metadata,
        )

    def extract_details(self, response: Response) -> ProductDetails:
        self._ensure_product_page(response)
        payload = self._pdp_payload(response)
        item = self._item(payload)
        url_ids = self._url_identity(response.url)
        item_id = self._id_str(
            item.get("item_id") or item.get("itemid")
        ) or url_ids.get("item_id")
        title = self._string(item.get("title") or item.get("name"))
        if not item_id or not title:
            raise ParseError("Identidade do produto não encontrada")

        model = self._selected_model(item, payload, response.url, url_ids)
        specifications = self._specifications(payload, item)
        brand = self._brand(payload, item, specifications)
        model_name = self._catalog_model(specifications, item)
        variant = self._variant_label(item, model)
        description = self._description(item)
        model_id = self._id_str(
            (model or {}).get("model_id") or (model or {}).get("modelid")
        )
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={"brand": brand, "model": model_name},
            title=title,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        attribute_sources = resolved.found_sources()
        source = {
            "title": "pdp-item",
            "brand": attribute_sources.get(
                "brand", "product-attributes" if brand else "not-found"
            ),
            "model": attribute_sources.get(
                "model", "product-attributes" if model_name else "not-found"
            ),
            "variant": "tier-variations-or-model" if variant else "not-found",
            "specifications": "product-attributes" if specifications else "not-found",
            "description": "pdp-item" if description else "not-found",
            **{
                key: source_name
                for key, source_name in attribute_sources.items()
                if key not in {"brand", "model"}
            },
        }
        if resolved.category:
            source["category"] = resolved.category

        return ProductDetails(
            product_id=str(item_id),
            sku=self._sku(model, item_id),
            gtin=self._gtin(specifications, item),
            title=title,
            brand=brand or resolved.value("brand"),
            model=resolved.value("model"),
            variant=variant or format_identity_variant(resolved),
            description=description,
            specifications=specifications,
            metadata={
                "shop_id": self._id_str(item.get("shop_id") or item.get("shopid"))
                or url_ids.get("shop_id"),
                "item_id": str(item_id),
                "model_id": model_id,
                "display_model_id": url_ids.get("display_model_id"),
                "source": source,
            },
        )

    def extract_images(self, response: Response) -> list[str]:
        """Gallery URLs from the already-fetched PDP payload (no CDN download)."""
        from ...utils.image_pipeline import ImagePipelineStats, Timer

        stats = ImagePipelineStats(
            store=self.store, source="pdp-payload-product_images"
        )
        discovery = Timer()
        payload = self._pdp_payload(response)
        item = self._item(payload)
        url_ids = self._url_identity(response.url)
        model = self._selected_model(item, payload, response.url, url_ids)
        stats.discovery_ms = discovery.ms()
        parse = Timer()
        urls = self._gallery_urls(payload, item, model)
        stats.parse_ms = parse.ms()
        stats.raw = len(urls)
        stats.after_filter = len(urls)
        stats.after_dedup = len(urls)
        stats.mark_returned(urls)
        stats.log()
        response.meta["image_pipeline"] = stats
        return urls

    # ------------------------------------------------------------------ payload

    @classmethod
    def _ensure_product_page(cls, response: Response) -> None:
        url = (response.url or "").casefold()
        text = response.text or ""
        title = ""
        try:
            title = (response.css("title::text").get() or "").casefold()
        except (AttributeError, ValueError, TypeError):
            title = ""

        if "/verify/traffic" in url or "verify/traff" in text.casefold():
            raise shopee_auth_required_error(url=response.url)
        if "login" in url and "next=" in url:
            raise shopee_auth_required_error(url=response.url)
        if re.search(r'"error"\s*:\s*90309999', text):
            raise RequestError(
                "Shopee bloqueou a requisição (anti-bot error 90309999)",
                code="UPSTREAM_BLOCKED",
                url=response.url,
                upstream_status=403,
                retryable=True,
            )
        folded = f"{title} {text[:4000].casefold()}"
        if any(
            marker in folded
            for marker in (
                "captcha",
                "segurança da conta",
                "seguranca da conta",
                "unusual traffic",
                "tráfego incomum",
                "trafego incomum",
            )
        ) and not cls._looks_like_pdp_json(text):
            raise RequestError(
                "Shopee apresentou desafio anti-bot / CAPTCHA",
                code="UPSTREAM_BLOCKED",
                url=response.url,
                upstream_status=403,
                retryable=True,
            )

    @classmethod
    def _pdp_payload(cls, response: Response) -> dict[str, Any]:
        text = (response.text or "").strip()
        if cls._looks_like_pdp_json(text):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ParseError("JSON PDP da Shopee inválido") from exc
            return cls._normalize_payload(value)

        for raw in response.css(
            "script[type='application/json']::text, "
            "script#__NEXT_DATA__::text, "
            "script[data-shopee-pdp]::text"
        ).getall():
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload = cls._normalize_payload(value)
            if payload.get("item") or cls._dict(payload.get("data")).get("item"):
                return payload

        for raw in response.css("script:not([src])::text").getall():
            match = re.search(
                r"(?:window\.__INITIAL_STATE__\s*=\s*|\"item\"\s*:\s*\{)",
                raw,
            )
            if not match:
                continue
            candidate = cls._extract_json_object(raw)
            if candidate is None:
                continue
            payload = cls._normalize_payload(candidate)
            if payload.get("item") or cls._dict(payload.get("data")).get("item"):
                return payload

        raise ParseError(
            "Dados estruturados do produto Shopee (get_pc / item) não encontrados"
        )

    @staticmethod
    def _looks_like_pdp_json(text: str) -> bool:
        stripped = text.lstrip()
        if not stripped.startswith("{"):
            return False
        head = stripped[:800]
        return '"item"' in head or '"item_id"' in head or '"itemid"' in head

    @classmethod
    def _normalize_payload(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        error = value.get("error")
        if error not in (None, 0, "0", False):
            if str(error) == "90309999" or error == 90309999:
                raise RequestError(
                    "Shopee bloqueou a requisição (anti-bot error 90309999)",
                    code="UPSTREAM_BLOCKED",
                    upstream_status=403,
                    retryable=True,
                )
            raise ParseError(f"Shopee retornou erro de PDP: {error!r}")
        data = value.get("data")
        if isinstance(data, dict) and (
            isinstance(data.get("item"), dict) or "product_price" in data
        ):
            return data
        if isinstance(value.get("item"), dict):
            return value
        props = cls._dict(cls._dict(value.get("props")).get("pageProps"))
        if isinstance(props.get("item"), dict) or isinstance(
            cls._dict(props.get("data")).get("item"), dict
        ):
            nested = props.get("data")
            return nested if isinstance(nested, dict) else props
        return value

    @classmethod
    def _item(cls, payload: dict[str, Any]) -> dict[str, Any]:
        item = payload.get("item")
        if isinstance(item, dict):
            return item
        raise ParseError("Objeto item ausente no payload Shopee")

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any] | None:
        start = raw.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(raw[start:], start=start):
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
                    try:
                        value = json.loads(raw[start : index + 1])
                    except json.JSONDecodeError:
                        return None
                    return value if isinstance(value, dict) else None
        return None

    # --------------------------------------------------------------- identity

    @classmethod
    def _url_identity(cls, url: str) -> dict[str, str | None]:
        parsed = urlparse(url)
        match = _URL_IDS.search(parsed.path)
        shop_id = match.group("shop_id") if match else None
        item_id = match.group("item_id") if match else None
        display_model_id: str | None = None
        model_selection_logic: str | None = None
        query = parse_qs(parsed.query)
        extra_raw = query.get("extraParams", [None])[0]
        if extra_raw:
            try:
                extra = json.loads(unquote(extra_raw))
            except (json.JSONDecodeError, TypeError):
                extra = {}
            if isinstance(extra, dict):
                display_model_id = cls._id_str(extra.get("display_model_id"))
                if extra.get("model_selection_logic") is not None:
                    model_selection_logic = str(extra.get("model_selection_logic"))
        if display_model_id is None:
            for key in ("model_id", "display_model_id"):
                values = query.get(key)
                if values:
                    display_model_id = cls._id_str(values[0])
                    if display_model_id:
                        break
        return {
            "shop_id": shop_id,
            "item_id": item_id,
            "display_model_id": display_model_id,
            "model_selection_logic": model_selection_logic,
        }

    @classmethod
    def _selected_model(
        cls,
        item: dict[str, Any],
        payload: dict[str, Any],
        url: str,
        url_ids: dict[str, str | None] | None = None,
    ) -> dict[str, Any] | None:
        models = cls._models(item, payload)
        if not models:
            return None
        ids = url_ids or cls._url_identity(url)
        wanted = ids.get("display_model_id")
        if wanted:
            for model in models:
                model_id = cls._id_str(model.get("model_id") or model.get("modelid"))
                if model_id == wanted:
                    return model
            raise ParseError(
                f"Modelo display_model_id={wanted} não encontrado nas variações"
            )
        if len(models) == 1:
            return models[0]
        # Sem model na URL: primeira variante vendável (ordem estável).
        # Nunca selecionar pelo menor preço.
        for model in models:
            if model.get("is_grayout") is True or model.get("is_clickable") is False:
                continue
            if model.get("has_stock") is False:
                continue
            return model
        return models[0]

    @classmethod
    def _models(
        cls, item: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        for candidate in (
            item.get("models"),
            cls._dict(payload.get("product_attributes")).get("models"),
            cls._dict(payload.get("product_images")).get("models"),
        ):
            if isinstance(candidate, list):
                return [entry for entry in candidate if isinstance(entry, dict)]
        return []

    @staticmethod
    def _sku(model: dict[str, Any] | None, item_id: str) -> str:
        if model:
            model_id = ShopeeSpider._id_str(
                model.get("model_id") or model.get("modelid")
            )
            if model_id:
                return model_id
        return str(item_id)

    # ------------------------------------------------------------------ prices

    def _price(
        self,
        payload: dict[str, Any],
        item: dict[str, Any],
        model: dict[str, Any] | None,
    ) -> tuple[Decimal, str]:
        if model is not None:
            raw = self._model_price_raw(model)
            if raw is not None:
                return self._micros(raw), "selected-model"
        product_price = self._dict(payload.get("product_price"))
        nested = self._dict(product_price.get("price"))
        if nested:
            single = nested.get("single_value")
            if self._is_positive_micros(single):
                return self._micros(single), "product-price"
            range_min = nested.get("range_min")
            range_max = nested.get("range_max")
            if (
                self._is_positive_micros(range_min)
                and self._is_positive_micros(range_max)
                and Decimal(str(range_min)) == Decimal(str(range_max))
            ):
                return self._micros(range_min), "product-price-range-single"
        for key in ("price", "price_min"):
            if self._is_positive_micros(item.get(key)):
                return self._micros(item[key]), f"item-{key}"
        raise ParseError("Preço do produto/variante não encontrado")

    def _original_price(
        self,
        payload: dict[str, Any],
        item: dict[str, Any],
        model: dict[str, Any] | None,
        price: Decimal,
    ) -> tuple[Decimal | None, str]:
        candidates: list[tuple[Any, str]] = []
        if model is not None:
            candidates.append(
                (self._model_price_before_raw(model), "selected-model-before-discount")
            )
        product_price = self._dict(payload.get("product_price"))
        before = product_price.get("price_before_discount")
        if isinstance(before, dict):
            candidates.append(
                (before.get("single_value"), "product-price-before-discount")
            )
        else:
            candidates.append((before, "product-price-before-discount"))
        candidates.append(
            (item.get("price_before_discount"), "item-price-before-discount")
        )
        for raw, source in candidates:
            if not self._is_positive_micros(raw):
                continue
            value = self._micros(raw)
            if value > price:
                return value, source
        return None, "not-found"

    @staticmethod
    def _model_price_raw(model: dict[str, Any]) -> Any:
        price = model.get("price")
        if isinstance(price, dict):
            return price.get("single_value")
        return price

    @staticmethod
    def _model_price_before_raw(model: dict[str, Any]) -> Any:
        price = model.get("price_before_discount")
        if isinstance(price, dict):
            return price.get("single_value")
        return price

    def _discount_percentage(
        self,
        payload: dict[str, Any],
        item: dict[str, Any],
        model: dict[str, Any] | None,
        price: Decimal,
        original: Decimal | None,
    ) -> Decimal | None:
        for raw in (
            self._dict(payload.get("product_price")).get("discount"),
            item.get("raw_discount"),
            item.get("show_discount"),
            (model or {}).get("raw_discount"),
        ):
            if raw in (None, "", 0, "0"):
                continue
            text = str(raw).strip().replace("%", "")
            try:
                value = Decimal(text)
            except (InvalidOperation, ValueError):
                continue
            if value > 0:
                return value.quantize(Decimal("0.01"))
        if original is None or original <= price:
            return None
        return ((original - price) * 100 / original).quantize(Decimal("0.01"))

    def _final_pricing(
        self,
        payload: dict[str, Any],
        *,
        model_id: str | None,
        list_price: Decimal,
    ) -> tuple[Decimal | None, str, dict[str, Any], dict[str, str]]:
        """Extract Pix/coupon breakdown from get_pc product_price fields.

        Finding selected_model.price must not skip this layer: list price and
        final Pix(+coupon) price are separate commercial fields.

        All values must belong to the same selected model_id; Shopee may attach
        ``final_price_info`` / ``price_breakdown`` to a different featured model.
        """
        product_price = self._dict(payload.get("product_price"))
        final_info = self._dict(product_price.get("final_price_info"))
        breakdown = self._dict(payload.get("price_breakdown"))
        pricing: dict[str, Any] = {}
        sources: dict[str, str] = {}

        final_model = self._id_str(final_info.get("model_id"))
        if model_id and final_model and final_model != model_id:
            return None, "not-found-other-model", pricing, sources

        for entry in breakdown.get("discount_breakdown") or []:
            if not isinstance(entry, dict):
                continue
            amount = self._optional_micros_amount(entry.get("discount_amount"))
            if amount is None:
                continue
            label = (entry.get("price_source") or "").casefold()
            entry_type = entry.get("type")
            if entry_type == 0 or "produto" in label:
                pricing["product_discount"] = str(amount)
                sources["product_discount"] = "price-breakdown"
            elif entry_type == 3 or "cupom" in label:
                pricing["coupon_discount"] = str(amount)
                sources["coupon_discount"] = "price-breakdown"
            elif entry_type == 4 or "pix" in label:
                pricing["pix_discount"] = str(amount)
                sources["pix_discount"] = "price-breakdown"

        vouchers = self._dict(final_info.get("final_price_vouchers"))
        platform_voucher = self._dict(vouchers.get("platform_voucher"))
        if "coupon_discount" not in pricing:
            voucher_amount = self._optional_micros_amount(
                platform_voucher.get("voucher_discount")
            )
            if voucher_amount is not None:
                pricing["coupon_discount"] = str(voucher_amount)
                sources["coupon_discount"] = "final-price-info-voucher"

        pix_block = self._dict(
            self._dict(final_info.get("payment_channel_discount")).get("pix_discount")
        )
        if "pix_discount" not in pricing:
            pix_amount = self._optional_micros_amount(pix_block.get("discount_amount"))
            if pix_amount is not None:
                pricing["pix_discount"] = str(pix_amount)
                sources["pix_discount"] = "final-price-info-pix"

        hint = self._string(final_info.get("hint_text"))
        if hint:
            pricing["final_price_hint"] = hint
            pricing["pix_requires_coupon"] = "cupom" in hint.casefold()

        pix_price: Decimal | None = None
        pix_source = "not-found"
        if product_price.get("has_final_price"):
            nested = self._dict(product_price.get("price"))
            raw_final = nested.get("single_value")
            if self._is_positive_micros(raw_final):
                candidate = self._micros(raw_final)
                # Final/Pix price is at or below the list promotional price.
                if candidate <= list_price:
                    pix_price = candidate
                    pix_source = "product-price-final"
        if pix_price is None:
            nested = self._dict(breakdown.get("price"))
            raw_final = nested.get("single_value")
            if self._is_positive_micros(raw_final):
                candidate = self._micros(raw_final)
                if candidate <= list_price:
                    pix_price = candidate
                    pix_source = "price-breakdown"

        if pix_price is not None:
            sources["pix_price"] = pix_source
        return pix_price, pix_source, pricing, sources

    def _installment(
        self, payload: dict[str, Any]
    ) -> tuple[Decimal | None, int | None, str]:
        product_price = self._dict(payload.get("product_price"))
        recommended = self._dict(
            self._dict(product_price.get("installment_info")).get("recommended_plan")
        )
        if recommended:
            count = recommended.get("months") or recommended.get("installment")
            amount = recommended.get("pay_per_month") or recommended.get(
                "monthly_payment"
            )
            parsed = self._parse_installment_values(count, amount)
            if parsed[0] is not None:
                return (*parsed, "product-price-recommended-plan")
        for key in ("installment_info", "spl_installment_info"):
            info = product_price.get(key)
            parsed = self._parse_installment_block(info)
            if parsed[0] is not None:
                return (*parsed, f"product-price-{key}")
        promotion = self._dict(payload.get("promotion_info"))
        parsed = self._parse_installment_block(promotion.get("installment"))
        if parsed[0] is not None:
            return (*parsed, "promotion-installment")
        drawer = self._dict(payload.get("installment_drawer"))
        bank = self._dict(drawer.get("bank"))
        channels = bank.get("channels")
        if isinstance(channels, list) and channels:
            plans = self._dict(channels[0]).get("plans")
            if isinstance(plans, list) and plans:
                # Prefer longest plan that still has a monthly amount (UI default).
                best: tuple[Decimal, int] | None = None
                for plan in plans:
                    if not isinstance(plan, dict):
                        continue
                    parsed = self._parse_installment_values(
                        plan.get("months") or plan.get("tenure"),
                        plan.get("monthly_payment") or plan.get("pay_per_month"),
                    )
                    if parsed[0] is None or parsed[1] is None:
                        continue
                    if best is None or parsed[1] > best[1]:
                        best = (parsed[0], parsed[1])
                if best is not None:
                    return best[0], best[1], "installment-drawer"
        return None, None, "not-found"

    def _parse_installment_block(self, info: Any) -> tuple[Decimal | None, int | None]:
        if not isinstance(info, dict):
            return None, None
        if isinstance(info.get("recommended_plan"), dict):
            plan = info["recommended_plan"]
            return self._parse_installment_values(
                plan.get("months") or plan.get("installment"),
                plan.get("pay_per_month")
                or plan.get("monthly_payment")
                or plan.get("amount"),
            )
        count = (
            info.get("installment")
            or info.get("month")
            or info.get("months")
            or info.get("tenure")
            or info.get("plan_tenor")
        )
        amount = (
            info.get("installment_amount")
            or info.get("monthly_payment")
            or info.get("monthly_amount")
            or info.get("pay_per_month")
            or info.get("amount")
            or info.get("price")
        )
        return self._parse_installment_values(count, amount)

    def _parse_installment_values(
        self, count: Any, amount: Any
    ) -> tuple[Decimal | None, int | None]:
        try:
            count_int = int(count) if count is not None else None
        except (TypeError, ValueError):
            count_int = None
        amount_dec = self._optional_micros_amount(amount)
        if amount_dec is None and self._is_positive_micros(amount):
            # Values below micros scale are already BRL.
            try:
                value = Decimal(str(amount))
            except (InvalidOperation, ValueError, TypeError):
                value = None
            if value is not None and value > 0:
                amount_dec = (
                    (value / _PRICE_SCALE).quantize(Decimal("0.01"))
                    if value >= _PRICE_SCALE
                    else value.quantize(Decimal("0.01"))
                )
        if count_int and count_int > 0 and amount_dec and amount_dec > 0:
            return amount_dec, count_int
        return None, None

    def _optional_micros_amount(self, raw: Any) -> Decimal | None:
        if not self._is_positive_micros(raw):
            return None
        try:
            return self._micros(raw)
        except ParseError:
            return None

    # ----------------------------------------------------------- availability

    @classmethod
    def _availability(
        cls,
        item: dict[str, Any],
        model: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> tuple[Availability, str]:
        status = str(item.get("item_status") or item.get("status") or "").casefold()
        if status in {"deleted", "disabled", "banned", "unlisted", "invalid"}:
            return "unavailable", "item-status"

        if model is not None:
            if model.get("is_grayout") is True or model.get("is_clickable") is False:
                return "out_of_stock", "selected-model-disabled"
            if model.get("has_stock") is False:
                return "out_of_stock", "selected-model-has-stock"
            if model.get("has_stock") is True:
                return "available", "selected-model-has-stock"
            stock = model.get("stock")
            if stock is not None:
                try:
                    return (
                        ("available", "selected-model-stock")
                        if int(stock) > 0
                        else ("out_of_stock", "selected-model-stock")
                    )
                except (TypeError, ValueError):
                    pass
            model_status = str(model.get("status") or "").casefold()
            if model_status in {"0", "unavailable", "model_unavailable"}:
                return "out_of_stock", "selected-model-status"

        stock = item.get("stock")
        if stock is not None:
            try:
                return (
                    ("available", "item-stock")
                    if int(stock) > 0
                    else ("out_of_stock", "item-stock")
                )
            except (TypeError, ValueError):
                pass
        if item.get("has_model_with_available_shopee_stock") is False:
            return "out_of_stock", "item-no-available-model"
        images_meta = cls._dict(payload.get("product_images"))
        if str(images_meta.get("abnormal_status") or "").casefold() in {
            "sold_out",
            "out_of_stock",
        }:
            return "out_of_stock", "product-images-abnormal"
        return "available", "default-in-stock"

    @classmethod
    def _seller(
        cls, payload: dict[str, Any], item: dict[str, Any]
    ) -> tuple[str | None, str]:
        shop = cls._dict(payload.get("shop_detailed"))
        for key in ("name", "shop_name"):
            value = cls._string(shop.get(key))
            if value:
                return value, "shop-detailed"
        account = cls._dict(shop.get("account"))
        username = cls._string(account.get("username"))
        if username:
            return username, "shop-account-username"
        for key in ("shop_name", "seller_name"):
            value = cls._string(item.get(key))
            if value:
                return value, "item-shop-name"
        return None, "not-found"

    # --------------------------------------------------------------- details

    @classmethod
    def _specifications(
        cls, payload: dict[str, Any], item: dict[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        attrs = item.get("attributes")
        if isinstance(attrs, list):
            for entry in attrs:
                if not isinstance(entry, dict):
                    continue
                name = cls._string(entry.get("name"))
                value = cls._string(entry.get("value"))
                if name and value:
                    result[name] = value
        product_attrs = cls._dict(payload.get("product_attributes")).get("attrs")
        if isinstance(product_attrs, list):
            for entry in product_attrs:
                if not isinstance(entry, dict):
                    continue
                name = cls._string(entry.get("name"))
                value = cls._string(entry.get("value"))
                if name and value and name not in result:
                    result[name] = value
        return result

    @classmethod
    def _brand(
        cls,
        payload: dict[str, Any],
        item: dict[str, Any],
        specifications: dict[str, Any],
    ) -> str | None:
        for key, value in specifications.items():
            if str(key).casefold().strip() in {"marca", "brand", "品牌"}:
                return cls._string(value)
        raw = item.get("brand")
        if isinstance(raw, dict):
            raw = raw.get("name") or raw.get("display_name")
        if cls._string(raw) and str(raw).casefold() not in {
            "0",
            "no brand",
            "sem marca",
        }:
            return cls._string(raw)
        tracking = cls._dict(item.get("tracking"))
        return cls._string(tracking.get("brand"))

    @classmethod
    def _catalog_model(
        cls, specifications: dict[str, Any], item: dict[str, Any]
    ) -> str | None:
        for key, value in specifications.items():
            if str(key).casefold().strip() in {
                "modelo",
                "model",
                "model number",
                "mpn",
            }:
                return cls._string(value)
        return cls._string(item.get("model") or item.get("model_name"))

    @classmethod
    def _variant_label(
        cls, item: dict[str, Any], model: dict[str, Any] | None
    ) -> str | None:
        if model is None:
            return None
        name = cls._string(model.get("name"))
        tiers = item.get("tier_variations")
        tier_index = cls._dict(model.get("extinfo")).get("tier_index")
        if not isinstance(tier_index, list):
            tier_index = model.get("tier_index")
        parts: list[str] = []
        if isinstance(tiers, list) and isinstance(tier_index, list):
            for axis, index in zip(tiers, tier_index, strict=False):
                if not isinstance(axis, dict):
                    continue
                options = axis.get("options")
                label = cls._string(axis.get("name") or axis.get("title"))
                option: str | None = None
                if isinstance(options, list) and isinstance(index, int):
                    if 0 <= index < len(options):
                        entry = options[index]
                        option = (
                            cls._string(entry)
                            if not isinstance(entry, dict)
                            else cls._string(entry.get("name") or entry.get("value"))
                        )
                if label and option:
                    parts.append(f"{label}: {option}")
                elif option:
                    parts.append(option)
        if parts:
            return "; ".join(parts)
        return name

    @classmethod
    def _description(cls, item: dict[str, Any]) -> str | None:
        rich = cls._dict(item.get("rich_text_description"))
        paragraphs = rich.get("paragraph_list")
        if isinstance(paragraphs, list):
            chunks: list[str] = []
            for entry in paragraphs:
                if not isinstance(entry, dict):
                    continue
                text = cls._string(entry.get("text"))
                if text:
                    chunks.append(text)
            if chunks:
                return "\n".join(chunks)
        raw = item.get("description")
        if not raw:
            return None
        text = Selector(text=unescape(str(raw))).xpath("string(.)").get() or str(raw)
        return " ".join(text.split()) or None

    @classmethod
    def _gtin(cls, specifications: dict[str, Any], item: dict[str, Any]) -> str | None:
        for key, value in specifications.items():
            if str(key).casefold().strip() in {
                "ean",
                "gtin",
                "gtin13",
                "código de barras",
                "codigo de barras",
                "barcode",
            }:
                return cls._string(value)
        return cls._string(item.get("gtin") or item.get("ean"))

    # ----------------------------------------------------------------- images

    @classmethod
    def _gallery_urls(
        cls,
        payload: dict[str, Any],
        item: dict[str, Any],
        model: dict[str, Any] | None,
    ) -> list[str]:
        keys: list[str] = []
        product_images = cls._dict(payload.get("product_images"))
        for entry in product_images.get("images") or []:
            key = cls._image_key(entry)
            if key:
                keys.append(key)
        for entry in item.get("images") or []:
            key = cls._image_key(entry)
            if key:
                keys.append(key)
        cover = cls._image_key(item.get("image"))
        if cover:
            keys.insert(0, cover)

        model_image = None
        if model is not None:
            model_image = cls._image_key(
                model.get("image")
                or model.get("gallery_image")
                or cls._dict(model.get("extinfo")).get("image")
            )
            if model_image is None:
                # Match model tier to first_tier_variations / tier_variations images.
                model_image = cls._model_tier_image(item, product_images, model)

        ordered: list[str] = []
        seen: set[str] = set()
        for key in ([model_image] if model_image else []) + keys:
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(cls._absolute_image_url(key))
        return ordered

    @classmethod
    def _model_tier_image(
        cls,
        item: dict[str, Any],
        product_images: dict[str, Any],
        model: dict[str, Any],
    ) -> str | None:
        tier_index = cls._dict(model.get("extinfo")).get("tier_index")
        if not isinstance(tier_index, list) or not tier_index:
            tier_index = model.get("tier_index")
        if not isinstance(tier_index, list) or not tier_index:
            return None
        first = tier_index[0]
        if not isinstance(first, int):
            return None
        first_tier = product_images.get("first_tier_variations")
        if isinstance(first_tier, list) and 0 <= first < len(first_tier):
            entry = first_tier[first]
            if isinstance(entry, dict):
                key = cls._image_key(entry.get("gallery_image") or entry.get("image"))
                if key:
                    return key
        tiers = item.get("tier_variations")
        if isinstance(tiers, list) and tiers:
            images = tiers[0].get("images") if isinstance(tiers[0], dict) else None
            if isinstance(images, list) and 0 <= first < len(images):
                return cls._image_key(images[first])
        return None

    @classmethod
    def _image_key(cls, value: Any) -> str | None:
        if value is None or value is False:
            return None
        if isinstance(value, dict):
            for key in ("image_id", "image_hash", "url", "image", "path", "src"):
                found = cls._image_key(value.get(key))
                if found:
                    return found
            return None
        text = str(value).strip()
        if not text or text.casefold() in {"null", "none"}:
            return None
        if text.startswith("//"):
            text = f"https:{text}"
        if text.startswith("http://") or text.startswith("https://"):
            # Keep absolute CDN URLs; strip size query params when present.
            return text.split("?")[0]
        # Hash / relative file key (never invent by string surgery beyond CDN prefix).
        text = text.removeprefix("file/")
        if (
            "/" in text
            and not text.startswith("br-")
            and not re.match(r"^[a-z]{2}-", text)
        ):
            # Skip obvious non-gallery paths (icons, banners).
            lowered = text.casefold()
            if any(
                marker in lowered
                for marker in ("banner", "icon", "avatar", "logo", "rating", "review")
            ):
                return None
        return text

    @staticmethod
    def _absolute_image_url(key_or_url: str) -> str:
        if key_or_url.startswith("http://") or key_or_url.startswith("https://"):
            return key_or_url
        return f"{_IMAGE_CDN}{key_or_url}"

    # ----------------------------------------------------------------- money

    @staticmethod
    def _micros(raw: Any) -> Decimal:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ParseError(f"Preço Shopee inválido: {raw!r}") from exc
        if value <= 0:
            raise ParseError(f"Preço Shopee não positivo: {raw!r}")
        # Values below the scale are treated as already-decimal BRL (fixtures / rare).
        if value < _PRICE_SCALE:
            return value.quantize(Decimal("0.01"))
        return (value / _PRICE_SCALE).quantize(Decimal("0.01"))

    @staticmethod
    def _is_positive_micros(raw: Any) -> bool:
        if raw in (None, "", -1, "-1"):
            return False
        try:
            return Decimal(str(raw)) > 0
        except (InvalidOperation, ValueError, TypeError):
            return False

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _id_str(value: Any) -> str | None:
        if value is None or value is False:
            return None
        text = str(value).strip()
        if not text or text.casefold() in {"none", "null"}:
            return None
        try:
            if Decimal(text) < 0:
                return None
        except (InvalidOperation, ValueError):
            pass
        return text
