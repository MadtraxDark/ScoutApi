"""Nissei (Paraguay / Magento) store adapter."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse

from scrapy.http import Response

from ...core.exceptions import ParseError
from ...core.fingerprints import canonicalize_url
from ...models.product import ProductDetails, ProductOffer
from ...utils.parsing import parse_money
from ...utils.product_attributes import (
    SOURCE_NOT_FOUND,
    SOURCE_SELECTED_VARIANT,
    SOURCE_SPECIFICATIONS,
    format_identity_variant,
    merge_specification_gaps,
    resolve_product_identity,
)
from ..base import BaseStoreSpider

logger = logging.getLogger(__name__)

Availability = Literal["available", "out_of_stock", "unavailable"]

# Magento swatch attribute codes → canonical identity keys.
_SWATCH_CODE_MAP: dict[str, str] = {
    "color": "color",
    "cor": "color",
    "colour": "color",
    "memoria_interna": "storage",
    "storage": "storage",
    "capacidade": "storage",
    "capacidad": "storage",
    "memoria_ram": "ram",
    "ram": "ram",
}

_CONTENTS_WITH_IDS_RE = re.compile(
    r"contentsWithIds\s*=\s*(\{.*?\});",
    re.DOTALL,
)
_CONFIGURABLE_PRODUCT_ID_RE = re.compile(
    r"configurableProductId\s*=\s*(\d+)",
)


class NisseiSpider(BaseStoreSpider):
    """Parse a single Nissei product offer and catalog details.

    Magento configurable PDPs may omit storage/color from the URL. Variant
    identity prefers selected swatch state / sole-option determination and the
    ``contentsWithIds`` child map over the URL slug (supporting evidence only).
    """

    name = "nissei"
    store, country, currency = "nissei", "PY", "PYG"
    allowed_domains = ["nissei.com"]
    start_urls: list[str] = []

    def extract_offer(self, response: Response) -> ProductOffer:
        data = self.json_ld(response)
        offers = data.get("offers") if isinstance(data, dict) else None
        offer = offers if isinstance(offers, dict) else {}
        page_text = " ".join(response.css("body ::text").getall())
        product_root = response.css(".product-info-main")
        selection = self._magento_selection(response)

        raw_price = (
            selection.get("price")
            or offer.get("price")
            or self.first(
                response,
                [
                    "[itemprop='price']::attr(content)",
                    "[data-price-amount]::attr(data-price-amount)",
                    ".price::text",
                    ".product-price::text",
                ],
            )
        )
        price = self._price(raw_price)
        original_price = self._original_price(product_root)
        discount_percentage = self._discount_percentage(price, original_price)
        installment_price, installment_count = self._installment(product_root)
        currency = self._currency(response, offer)

        product_id, sku, id_source = self._product_identifiers(
            response, data, page_text, selection
        )
        availability = self._availability(offer, page_text)
        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=product_id,
            sku=sku,
            url=response.url,
            canonical_url=self._canonical_url(response),
            currency=currency,
            price=Decimal(price),
            original_price=original_price,
            discount_percentage=discount_percentage,
            installment_price=installment_price,
            installment_count=installment_count,
            available=availability == "available",
            availability=availability,
            metadata={
                "source": {
                    "price": selection.get("price_source")
                    or "json-ld-or-magento",
                    "sku": id_source,
                    "availability": "json-ld-or-page",
                },
                "parent_product_id": selection.get("parent_product_id"),
                "variant_product_id": selection.get("variant_product_id"),
            },
        )

    def extract_details(self, response: Response) -> ProductDetails:
        data = self.json_ld(response)
        page_text = " ".join(response.css("body ::text").getall())
        product_root = response.css(".product-info-main")
        selection = self._magento_selection(response)
        specifications = self._specifications(response)

        title = data.get("name") or self.first(
            response, ["h1 .base::text", "h1::text", "title::text"]
        )
        if not title:
            raise ParseError("Título do produto não encontrado")

        brand = self.first(product_root, [".amshopby-brand-title-link::text"])
        gtin = (
            self._attribute_value(response, "UPC")
            or self._attribute_value(response, "EAN")
            or self._attribute_value(response, "GTIN")
        )
        product_id, sku, id_source = self._product_identifiers(
            response, data, page_text, selection
        )

        selected_variant = selection.get("variant_attrs") or {}
        url_evidence = self._url_variant_evidence(response.url)
        structured = {
            "brand": brand or data.get("brand"),
            "color": data.get("color") if isinstance(data, dict) else None,
            "model": data.get("model") if isinstance(data, dict) else None,
        }
        if isinstance(structured.get("brand"), dict):
            structured["brand"] = structured["brand"].get("name")

        resolved = resolve_product_identity(
            selected_variant=selected_variant,
            specifications=specifications,
            structured={k: v for k, v in structured.items() if v},
            url_evidence=url_evidence,
            title=str(title).strip(),
        )
        specifications = merge_specification_gaps(specifications, resolved)
        conflicts = self._source_conflicts(
            selected_variant=selected_variant,
            specifications=specifications,
            url_evidence=url_evidence,
            resolved=resolved,
        )
        if conflicts:
            logger.info(
                "nissei_variant_source_conflict",
                extra={"store": self.store, "url": response.url, "conflicts": conflicts},
            )

        attribute_sources = {
            key: resolved.get(key).source
            for key in ("brand", "model", "color", "storage", "ram", "size", "capacity")
            if resolved.get(key).source != SOURCE_NOT_FOUND
        }
        variant_dims = {
            key: value
            for key, value in {
                "color": resolved.value("color"),
                "storage": resolved.value("storage"),
                "ram": resolved.value("ram"),
            }.items()
            if value
        }
        metadata_source = {
            "title": "json-ld-or-h1",
            "sku": id_source,
            "specifications": "product-attribute-specs-table"
            if specifications
            else "not-found",
            "selected_variant": selection.get("selection_source") or "not-found",
            **attribute_sources,
        }
        return ProductDetails(
            product_id=product_id,
            sku=sku,
            gtin=gtin,
            title=str(title).strip(),
            brand=(str(brand).strip() if brand else resolved.value("brand")),
            model=resolved.value("model"),
            variant=format_identity_variant(resolved),
            specifications=specifications,
            metadata={
                "source": metadata_source,
                "variant": variant_dims,
                "selected_variant": selected_variant,
                "url_evidence": url_evidence,
                "parent_product_id": selection.get("parent_product_id"),
                "variant_product_id": selection.get("variant_product_id"),
                "variant_conflicts": conflicts or None,
            },
        )

    def extract_images(self, response: Response) -> list[str]:
        gallery = response.css(
            ".gallery-placeholder img::attr(src), "
            ".fotorama__img::attr(src), "
            ".product.media img::attr(src), "
            "[data-gallery-role='gallery'] img::attr(src)"
        ).getall()
        urls = self.normalize_image_urls(gallery, base_url=response.url)
        if urls:
            unique: list[str] = []
            seen: set[str] = set()
            for url in urls:
                identity = self._gallery_image_identity(url)
                if identity in seen:
                    continue
                seen.add(identity)
                unique.append(url)
            return unique
        return super().extract_images(response)

    @staticmethod
    def _gallery_image_identity(url: str) -> str:
        """Collapse Magento cache variants to the same catalog image path."""
        return re.sub(r"/cache/[0-9a-f]{32}/", "/", url, count=1, flags=re.I)

    def _price(self, raw: object) -> Decimal:
        if raw is None:
            return parse_money(None, self.currency)
        text = str(raw).strip()
        # Magento often exposes a plain decimal with '.' as radix.
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return Decimal(text).quantize(Decimal("1"))
        return parse_money(text, self.currency)

    def _currency(self, response: Response, offer: dict[str, Any]) -> str:
        structured = str(offer.get("priceCurrency") or "").strip().upper()
        if structured in {"USD", "PYG", "BRL", "EUR"}:
            return structured
        text = response.text or ""
        if re.search(r"US\$|priceCurrency[\"']\s*:\s*[\"']USD", text, re.I):
            return "USD"
        if re.search(r"\bGs\.|priceCurrency[\"']\s*:\s*[\"']PYG", text, re.I):
            return "PYG"
        # Locale /br/ storefront commonly prices in USD even when SERP is /py/.
        path = (urlparse(response.url).path or "").lower()
        if path.startswith("/br/") or path == "/br":
            return "USD"
        return self.currency

    def _canonical_url(self, response: Response) -> str:
        href = response.css("link[rel='canonical']::attr(href)").get()
        if href and str(href).strip():
            return canonicalize_url(urljoin(response.url, str(href).strip()))
        return canonicalize_url(response.url)

    def _product_identifiers(
        self,
        response: Response,
        data: dict[str, Any],
        page_text: str,
        selection: dict[str, Any],
    ) -> tuple[str, str, str]:
        """Return ``(product_id, sku, source)`` preferring the selected child."""
        child_sku = selection.get("child_sku")
        child_id = selection.get("variant_product_id")
        if child_sku:
            return str(child_sku).strip(), str(child_sku).strip(), "selected-variant-sku"
        if child_id:
            return str(child_id).strip(), str(child_id).strip(), "selected-variant-id"

        product_id = (
            data.get("sku")
            or self.first(
                response,
                [
                    "[itemprop='sku']::attr(content)",
                    "[itemprop='sku']::text",
                    "form[data-product-sku]::attr(data-product-sku)",
                    "[data-product-id]::attr(data-product-id)",
                ],
            )
            or self._sku_from_text(page_text)
        )
        if not product_id:
            product_id = self._canonical_url(response)
            return str(product_id).strip(), str(product_id).strip(), "canonical-url"
        return (
            str(product_id).strip(),
            str(product_id).strip(),
            "json-ld-or-magento",
        )

    def _magento_selection(self, response: Response) -> dict[str, Any]:
        """Resolve Magento selected / sole-option variant without inventing picks."""
        product_root = response.css(".product-info-main")
        swatches = self._parse_swatches(product_root)
        contents = self._parse_contents_with_ids(response.text or "")
        parent_id = self._parent_product_id(response, product_root)

        explicit: dict[str, str] = {}
        sole: dict[str, str] = {}
        multi_unselected = False
        for swatch in swatches:
            key = _SWATCH_CODE_MAP.get(swatch["code"])
            if not key:
                continue
            if swatch.get("selected_label"):
                explicit[key] = str(swatch["selected_label"]).strip()
                continue
            options = swatch.get("options") or []
            if len(options) == 1:
                sole[key] = str(options[0]["label"]).strip()
            elif len(options) > 1:
                multi_unselected = True

        # Fully determined when every swatch attr is either explicit or sole.
        # Never take the first option among many.
        variant_attrs: dict[str, str] = {}
        selection_source = "not-found"
        if swatches:
            determined = True
            merged: dict[str, str] = {}
            for swatch in swatches:
                key = _SWATCH_CODE_MAP.get(swatch["code"])
                if not key:
                    continue
                if key in explicit:
                    merged[key] = explicit[key]
                elif key in sole:
                    merged[key] = sole[key]
                else:
                    determined = False
            if determined and merged:
                variant_attrs = merged
                selection_source = (
                    SOURCE_SELECTED_VARIANT
                    if explicit
                    else "sole-option-selected-variant"
                )
            elif explicit:
                # Partial explicit selection — keep known dims, leave rest missing.
                variant_attrs = dict(explicit)
                selection_source = SOURCE_SELECTED_VARIANT

        child_sku = None
        variant_product_id = None
        price = None
        price_source = None

        matched_child = self._match_contents_child(contents, variant_attrs)
        if matched_child is not None:
            variant_product_id, payload = matched_child
            child_sku = str(payload.get("id") or "").strip() or None
            if payload.get("item_price") is not None:
                price = payload.get("item_price")
                price_source = "contentsWithIds"
            color = payload.get("color")
            if color and "color" not in variant_attrs:
                variant_attrs["color"] = str(color).strip()
                if selection_source == "not-found":
                    selection_source = SOURCE_SELECTED_VARIANT
        elif len(contents) == 1 and not multi_unselected:
            # Single sellable child and no ambiguous multi-option attrs.
            variant_product_id, payload = next(iter(contents.items()))
            child_sku = str(payload.get("id") or "").strip() or None
            color = payload.get("color")
            if color and "color" not in variant_attrs:
                variant_attrs["color"] = str(color).strip()
                selection_source = (
                    selection_source
                    if selection_source != "not-found"
                    else SOURCE_SELECTED_VARIANT
                )
            if payload.get("item_price") is not None:
                price = payload.get("item_price")
                price_source = "contentsWithIds"
            if selection_source == "not-found":
                selection_source = "contentsWithIds"

        return {
            "variant_attrs": variant_attrs,
            "selection_source": selection_source,
            "child_sku": child_sku,
            "variant_product_id": variant_product_id,
            "parent_product_id": parent_id,
            "price": price,
            "price_source": price_source,
            "swatches": swatches,
            "contents_with_ids": contents,
        }

    @staticmethod
    def _match_contents_child(
        contents: dict[str, dict[str, Any]],
        variant_attrs: dict[str, str],
    ) -> tuple[str, dict[str, Any]] | None:
        """Resolve a unique contentsWithIds child from known selected dims."""
        if not contents or not variant_attrs:
            return None
        matches: list[tuple[str, dict[str, Any]]] = []
        wanted_color = (variant_attrs.get("color") or "").casefold().strip()
        for product_id, payload in contents.items():
            if not isinstance(payload, dict):
                continue
            color = str(payload.get("color") or "").casefold().strip()
            if wanted_color and color and color != wanted_color:
                continue
            if wanted_color and not color:
                # Cannot confirm — keep as candidate only when sole remaining.
                matches.append((product_id, payload))
                continue
            matches.append((product_id, payload))
        if wanted_color:
            color_hits = [
                item
                for item in matches
                if str(item[1].get("color") or "").casefold().strip() == wanted_color
            ]
            if len(color_hits) == 1:
                return color_hits[0]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _parse_swatches(product_root: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for node in product_root.css(".swatch-attribute"):
            code = (
                node.attrib.get("data-attribute-code")
                or node.css("::attr(data-attribute-code)").get()
                or ""
            ).strip().casefold()
            if not code:
                continue
            attr_id = (
                node.attrib.get("data-attribute-id")
                or node.css("::attr(data-attribute-id)").get()
            )
            selected_id = (
                node.attrib.get("data-option-selected")
                or node.attrib.get("option-selected")
                or node.css("::attr(data-option-selected)").get()
                or node.css("::attr(option-selected)").get()
            )
            selected_label = " ".join(
                t.strip()
                for t in node.css(".swatch-attribute-selected-option::text").getall()
                if t and t.strip()
            ) or None
            options: list[dict[str, str]] = []
            for opt in node.css(".swatch-option"):
                opt_id = (
                    opt.attrib.get("data-option-id")
                    or opt.css("::attr(data-option-id)").get()
                    or ""
                ).strip()
                label = (
                    opt.attrib.get("data-option-label")
                    or opt.css("::attr(data-option-label)").get()
                    or opt.attrib.get("aria-label")
                    or ""
                ).strip()
                if not opt_id or not label:
                    continue
                checked = (
                    opt.attrib.get("aria-checked")
                    or opt.css("::attr(aria-checked)").get()
                    or ""
                ).strip().casefold()
                classes = (opt.attrib.get("class") or "").casefold()
                options.append({"id": opt_id, "label": label})
                if checked == "true" or "selected" in classes.split():
                    selected_id = selected_id or opt_id
                    selected_label = selected_label or label
            if selected_id and not selected_label:
                for opt in options:
                    if opt["id"] == str(selected_id).strip():
                        selected_label = opt["label"]
                        break
            results.append(
                {
                    "code": code,
                    "attribute_id": str(attr_id).strip() if attr_id else None,
                    "options": options,
                    "selected_option_id": str(selected_id).strip()
                    if selected_id
                    else None,
                    "selected_label": selected_label,
                }
            )
        return results

    @staticmethod
    def _parse_contents_with_ids(text: str) -> dict[str, dict[str, Any]]:
        match = _CONTENTS_WITH_IDS_RE.search(text)
        if not match:
            return {}
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                result[str(key)] = value
        return result

    @staticmethod
    def _parent_product_id(response: Response, product_root: Any) -> str | None:
        hidden = product_root.css('input[name="product"]::attr(value)').get()
        if hidden and str(hidden).strip().isdigit():
            return str(hidden).strip()
        price_box = product_root.css(
            ".price-box[data-product-id]::attr(data-product-id)"
        ).get()
        if price_box and str(price_box).strip().isdigit():
            return str(price_box).strip()
        match = _CONFIGURABLE_PRODUCT_ID_RE.search(response.text or "")
        if match:
            return match.group(1)
        return None

    def _url_variant_evidence(self, url: str) -> dict[str, str]:
        """Supporting evidence only — never the primary identity source."""
        path = unquote(urlparse(url).path or "")
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        if not slug:
            return {}
        slug_title = slug.replace("-", " ")
        bundle = resolve_product_identity(title=slug_title, url_evidence=None)
        evidence: dict[str, str] = {}
        for key in ("storage", "color", "ram"):
            value = bundle.value(key)
            if value:
                evidence[key] = value
        return evidence

    @staticmethod
    def _source_conflicts(
        *,
        selected_variant: dict[str, str],
        specifications: dict[str, Any],
        url_evidence: dict[str, str],
        resolved: Any,
    ) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []

        def _norm(value: str) -> str:
            return value.casefold().replace(" ", "")

        for key in ("storage", "color", "ram"):
            selected = selected_variant.get(key)
            url_val = url_evidence.get(key)
            final = resolved.value(key)
            winner = resolved.get(key).source if final else None
            if (
                selected
                and url_val
                and _norm(selected) != _norm(url_val)
            ):
                conflicts.append(
                    {
                        "attribute": key,
                        "selected_variant": selected,
                        "url_evidence": url_val,
                        "chosen": final or selected,
                        "winner_source": winner or SOURCE_SELECTED_VARIANT,
                    }
                )
            spec_val = None
            if key == "storage":
                spec_val = (
                    specifications.get("Memoria Interna")
                    or specifications.get("Memória Interna")
                    or specifications.get("Storage")
                )
            elif key == "color":
                spec_val = (
                    specifications.get("Cor")
                    or specifications.get("Color")
                    or specifications.get("Colour")
                )
            elif key == "ram":
                spec_val = (
                    specifications.get("Memoria RAM")
                    or specifications.get("Memória RAM")
                    or specifications.get("RAM")
                )
            if (
                spec_val
                and url_val
                and _norm(str(spec_val)) != _norm(url_val)
                and winner == SOURCE_SPECIFICATIONS
            ):
                conflicts.append(
                    {
                        "attribute": key,
                        "specifications": str(spec_val),
                        "url_evidence": url_val,
                        "chosen": final or str(spec_val),
                        "winner_source": SOURCE_SPECIFICATIONS,
                    }
                )
            if (
                selected
                and spec_val
                and _norm(selected) != _norm(str(spec_val))
                and winner == SOURCE_SELECTED_VARIANT
            ):
                conflicts.append(
                    {
                        "attribute": key,
                        "selected_variant": selected,
                        "specifications": str(spec_val),
                        "chosen": final or selected,
                        "winner_source": SOURCE_SELECTED_VARIANT,
                    }
                )
        return conflicts

    def _specifications(self, response: Response) -> dict[str, Any]:
        specs: dict[str, Any] = {}
        rows = response.css(
            "#product-attribute-specs-table tr, "
            "table.product-attribute-specs-table tr, "
            ".additional-attributes-wrapper table tr"
        )
        for row in rows:
            key = " ".join(row.css("th::text").getall()).strip()
            value = " ".join(row.css("td::text").getall()).strip()
            if not key:
                key = (row.css("td::attr(data-th)").get() or "").strip()
            if key and value:
                specs[key] = value
        return specs

    @staticmethod
    def _attribute_value(response: Response, label: str) -> str | None:
        """Read a product specification by its explicit table label."""
        rows = response.css(
            "#product-attribute-specs-table tr, "
            "table.product-attribute-specs-table tr, "
            ".additional-attributes-wrapper table tr"
        )
        for row in rows:
            key = " ".join(row.css("th::text").getall()).strip()
            if not key:
                key = (row.css("td::attr(data-th)").get() or "").strip()
            if key.casefold() == label.casefold():
                value = " ".join(row.css("td::text").getall()).strip()
                return value or None
        return None

    @staticmethod
    def _original_price(product_root: Any) -> Decimal | None:
        raw = product_root.css(
            ".price-box[data-role='priceBox'] "
            ".price-wrapper[data-price-type='oldPrice']::attr(data-price-amount)"
        ).get()
        if not raw:
            return None
        return Decimal(raw).quantize(Decimal("1"))

    @staticmethod
    def _discount_percentage(
        price: Decimal, original_price: Decimal | None
    ) -> Decimal | None:
        if original_price is None or original_price <= price:
            return None
        return ((original_price - price) * 100 / original_price).quantize(
            Decimal("0.01")
        )

    @staticmethod
    def _installment(product_root: Any) -> tuple[Decimal | None, int | None]:
        """Use the installment amount/count explicitly rendered by Nissei's page JS."""
        text = " ".join(
            product_root.css(
                ".principal-cuotas h3::text, .principal-cuotas h3 *::text"
            ).getall()
        )
        match = re.search(
            r"Hasta\s+(\d+)\s+cuotas.*?Gs\.\s*([\d.]+)", text, re.I | re.S
        )
        if not match:
            return None, None
        amount = parse_money(match.group(2), "PYG")
        return Decimal(amount), int(match.group(1))

    @staticmethod
    def _sku_from_text(page_text: str) -> str | None:
        match = re.search(r"SKU\s*[#:]?\s*([A-Za-z0-9\-]+)", page_text, re.I)
        return match.group(1) if match else None

    @staticmethod
    def _availability(offer: dict[str, Any], page_text: str) -> Availability:
        structured = str(offer.get("availability", "")).lower()
        if any(marker in structured for marker in ("outofstock", "soldout")):
            return "out_of_stock"
        lower = page_text.lower()
        if "agotado" in lower or "sin stock" in lower or "out of stock" in lower:
            return "out_of_stock"
        if "en stock" in lower or "in stock" in structured:
            return "available"
        if "instock" in structured:
            return "available"
        return "available"
