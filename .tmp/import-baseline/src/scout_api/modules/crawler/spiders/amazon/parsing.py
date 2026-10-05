"""Shared Amazon PDP parsing used by regional thin spiders.

Parsing only — no fetch, proxy, or Camoufox changes.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from html import unescape
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
from .marketplace import AmazonMarketplace

Availability = Literal["available", "out_of_stock", "unavailable"]

_ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
_ASIN_TOKEN = re.compile(r"^[A-Z0-9]{10}$", re.I)
_PARENT_ASIN_RE = re.compile(
    r"""["']parentAsin["']\s*:\s*["']([A-Z0-9]{10})["']""",
    re.I,
)
_COLOR_IMAGES_RE = re.compile(
    r"""['"]colorImages['"]\s*:\s*\{\s*['"]initial['"]\s*:\s*"""
    r"""(?:A\.\$\.parseJSON\(['"](.+?)['"]\)|(\[[\s\S]*?\]))\s*\}""",
    re.I,
)
_DIMENSION_VALUES_RE = re.compile(
    r""""dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,""",
    re.S,
)
_VARIATION_LABELS_RE = re.compile(
    r""""variationDisplayLabels"\s*:\s*(\{.*?\})\s*,""",
    re.S,
)
_DIMENSIONS_ORDER_RE = re.compile(
    r""""dimensions"\s*:\s*(\[[^\]]*\])\s*,""",
    re.S,
)
_INSTALLMENT_BR_RE = re.compile(
    r"""(?:em\s+at[eé]\s+)?(\d+)\s*x\s*(?:de\s*)?(?:R\$\s*)?([\d.]+,\d{2})""",
    re.I,
)
_PIX_PRICE_RE = re.compile(
    r"""(?:R\$\s*)([\d.]+,\d{2})\s*(?:no\s+pix|via\s+pix|com\s+pix)"""
    r"""|(?:R\$\s*)([\d.]+,\d{2})\s*(?:\n|\s){0,40}à\s*vista\s*no\s*Pix""",
    re.I | re.S,
)
_COUPON_VALUE_RE = re.compile(
    r"""(?:apply|aplicar)?\s*(?:R\$\s*)?([\d.,]+)\s*(?:coupon|cupom)""",
    re.I,
)
_BLOCK_MARKERS = (
    "validatecaptcha",
    "/errors/validatecaptcha",
    "opfcaptcha",
    "robot check",
    "type the characters you see",
    "enter the characters you see",
    "sorry, we just need to make sure you're not a robot",
    "clique na caixa para confirmar",
    "não sou um robô",
    "nao sou um robo",
    "to discuss automated access to amazon data",
)

_BUYBOX_PRICE_SELECTORS = (
    "#ppd .apex-pricetopay-value .a-offscreen::text",
    "#ppd .priceToPay .a-offscreen::text",
    "#corePriceDisplay_desktop_feature_div .apex-pricetopay-value .a-offscreen::text",
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen::text",
    "#corePrice_feature_div .a-price .a-offscreen::text",
    "#desktop_buybox .a-price .a-offscreen::text",
    "#apex_desktop .apex-pricetopay-value .a-offscreen::text",
    "#buybox .a-price .a-offscreen::text",
    "#qualifiedBuybox .a-price .a-offscreen::text",
    "#desktop_qualifiedBuyBox .a-price .a-offscreen::text",
)
_LIST_PRICE_SELECTORS = (
    "#ppd .apex-basisprice-value .a-offscreen::text",
    "#ppd .basisPrice .a-offscreen::text",
    "#corePriceDisplay_desktop_feature_div .apex-basisprice-value .a-offscreen::text",
    "#corePriceDisplay_desktop_feature_div .a-price.a-text-price .a-offscreen::text",
    "#ppd .a-price[data-a-strike='true'] .a-offscreen::text",
    "#qualifiedBuybox .a-price[data-a-strike='true'] .a-offscreen::text",
    "#buybox .a-price[data-a-strike='true'] .a-offscreen::text",
)


def extract_amazon_offer(
    spider: BaseStoreSpider,
    response: Response,
    marketplace: AmazonMarketplace,
) -> ProductOffer:
    ensure_amazon_product_page(response)
    asin = extract_asin(response)
    if not asin:
        raise ParseError("ASIN Amazon não encontrado")

    try:
        price, price_source = extract_buybox_price(response, marketplace.currency)
    except MissingPriceError:
        availability, availability_source = extract_availability(
            response, marketplace, price=None
        )
        if availability == "out_of_stock":
            raise MissingPriceError(
                "Produto Amazon sem preço na Buy Box (indisponível / sem oferta)"
            ) from None
        raise
    original, original_source = extract_list_price(
        response, marketplace.currency, price
    )
    discount = None
    if original is not None and original > price:
        discount = ((original - price) * 100 / original).quantize(Decimal("0.01"))

    availability, availability_source = extract_availability(
        response, marketplace, price=price
    )
    seller, seller_source, fulfilled_by = extract_seller(response, marketplace)
    pix_price, pix_source = extract_pix_price(response, marketplace, buybox_price=price)
    installment_price, installment_count, installment_source = extract_installment(
        response, marketplace
    )
    pricing_meta = extract_conditional_pricing(response, marketplace)
    parent_asin = extract_parent_asin(response.text or "", asin)
    variants = extract_selected_variant_dimensions(response.text or "", asin)

    metadata: dict[str, Any] = {
        "source": {
            "price": price_source,
            "original_price": original_source,
            "pix_price": pix_source,
            "installment": installment_source,
            "availability": availability_source,
            "seller": seller_source,
            "asin": "url-or-dom",
        },
        "marketplace": marketplace.host,
        "asin": asin,
        "buy_box": True,
    }
    if parent_asin and parent_asin != asin:
        metadata["parent_asin"] = parent_asin
    if fulfilled_by:
        metadata["fulfilled_by"] = fulfilled_by
    if variants:
        metadata["variant"] = variants
    if pricing_meta:
        metadata["pricing"] = pricing_meta
    if marketplace.country == "US":
        metadata["shipping_to_brazil"] = False
        metadata["source"]["shipping_to_brazil"] = False

    return ProductOffer(
        store=spider.store,
        country=marketplace.country,
        product_id=asin,
        sku=asin,
        seller=seller,
        url=response.url,
        canonical_url=canonical_amazon_url(marketplace.host, asin),
        currency=marketplace.currency,
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


def extract_amazon_details(
    spider: BaseStoreSpider,
    response: Response,
    marketplace: AmazonMarketplace,
) -> ProductDetails:
    ensure_amazon_product_page(response)
    asin = extract_asin(response)
    title = extract_title(response)
    if not asin or not title:
        raise ParseError("Identidade do produto Amazon não encontrada")

    specifications = extract_specifications(response)
    brand = extract_brand(response, specifications)
    model = _spec_value(
        specifications, "model", "modelo", "model number", "número do modelo"
    )
    gtin = extract_gtin(response, specifications)
    description = extract_description(response)
    variants = extract_selected_variant_dimensions(response.text or "", asin)
    color = variants.get("color") or variants.get("colour") or variants.get("cor")
    storage = (
        variants.get("storage")
        or variants.get("size")
        or variants.get("capacity")
        or variants.get("tamanho")
    )

    resolved = resolve_product_identity(
        specifications=specifications,
        structured={
            "brand": brand,
            "model": model,
            "color": color,
            "storage": storage,
            "capacity": variants.get("capacity"),
            "size": variants.get("size") or variants.get("tamanho"),
        },
        title=title,
    )
    specifications = merge_specification_gaps(specifications, resolved)
    if variants:
        variant: str | None = "; ".join(
            f"{key}: {value}" for key, value in variants.items()
        )
    else:
        variant = format_identity_variant(resolved)

    parent_asin = extract_parent_asin(response.text or "", asin)
    attribute_sources = resolved.found_sources()
    metadata: dict[str, Any] = {
        "source": {
            "title": "product-title" if response.css("#productTitle") else "fallback",
            "brand": attribute_sources.get("brand", "not-found"),
            "model": attribute_sources.get("model", "not-found"),
            "specifications": "product-details" if specifications else "not-found",
            "variant": "twister"
            if variants
            else attribute_sources.get("color", "not-found"),
            "asin": "url-or-dom",
        },
        "marketplace": marketplace.host,
        "asin": asin,
    }
    if parent_asin and parent_asin != asin:
        metadata["parent_asin"] = parent_asin
    if variants:
        metadata["variant"] = variants
    if attribute_sources:
        metadata["attribute_sources"] = attribute_sources

    return ProductDetails(
        product_id=asin,
        sku=asin,
        gtin=gtin,
        title=title,
        brand=_string(brand),
        model=_string(resolved.value("model")),
        variant=variant or None,
        description=_string(description),
        specifications=specifications,
        metadata=metadata,
    )


def extract_amazon_images(response: Response) -> list[str]:
    ensure_amazon_product_page(response)
    urls: list[str] = []
    urls.extend(extract_color_images(response.text or ""))
    if not urls:
        landing = response.css("#landingImage::attr(data-old-hires)").get()
        if landing:
            urls.append(landing.strip())
        dynamic = response.css("#landingImage::attr(data-a-dynamic-image)").get()
        urls.extend(_dynamic_image_urls(dynamic))
    if not urls:
        urls.extend(
            u.strip()
            for u in response.css(
                "#altImages img::attr(src), #imageBlock img::attr(src)"
            ).getall()
            if u and "transparent-pixel" not in u and "sprite" not in u.lower()
        )
    return BaseStoreSpider.normalize_image_urls(urls, base_url=response.url)


def ensure_amazon_product_page(response: Response) -> None:
    """Raise UPSTREAM_BLOCKED on robot/CAPTCHA pages; ParseError if not a PDP."""
    text = response.text or ""
    folded = text[:12000].casefold()
    title = ""
    try:
        title = (response.css("title::text").get() or "").casefold()
    except (AttributeError, ValueError, TypeError):
        title = ""
    haystack = f"{title}\n{folded}"
    if any(marker in haystack for marker in _BLOCK_MARKERS):
        raise RequestError(
            "Amazon apresentou challenge anti-bot / CAPTCHA",
            code="UPSTREAM_BLOCKED",
            url=response.url,
            upstream_status=403,
            retryable=True,
        )
    if "ap/signin" in (response.url or "").casefold():
        raise RequestError(
            "Amazon redirecionou para login; página de produto indisponível",
            code="UPSTREAM_BLOCKED",
            url=response.url,
            upstream_status=401,
            retryable=True,
        )
    has_title = bool(response.css("#productTitle::text").get())
    has_asin = bool(extract_asin(response))
    if not has_title and not has_asin:
        raise ParseError("Página Amazon sem marcadores de produto (PDP)")


def extract_asin(response: Response) -> str | None:
    for selector in (
        "input#ASIN::attr(value)",
        "input[name='ASIN']::attr(value)",
        "#asin::attr(value)",
        "#averageCustomerReviews::attr(data-asin)",
        "#dp::attr(data-asin)",
    ):
        value = response.css(selector).get()
        if value and _ASIN_TOKEN.match(value.strip()):
            return value.strip().upper()
    match = _ASIN_RE.search(response.url or "")
    if match:
        return match.group(1).upper()
    return None


def canonical_amazon_url(host: str, asin: str) -> str:
    return canonicalize_url(f"https://www.{host}/dp/{asin}")


def prepare_amazon_fetch_url(url: str, host: str) -> str:
    """Strip tracking/slug noise; fetch the canonical ``/dp/{ASIN}`` PDP."""
    match = _ASIN_RE.search(url or "")
    if not match:
        return url
    return canonical_amazon_url(host, match.group(1).upper())


def extract_title(response: Response) -> str | None:
    title = response.css("#productTitle::text").get()
    if title and title.strip():
        return unescape(title).strip()
    data = BaseStoreSpider.json_ld(response)
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def extract_buybox_price(response: Response, currency: str) -> tuple[Decimal, str]:
    for selector in _BUYBOX_PRICE_SELECTORS:
        for raw in response.css(selector).getall():
            text = (raw or "").strip()
            if not text or not any(ch.isdigit() for ch in text):
                continue
            if _looks_like_monthly_payment(text):
                continue
            try:
                return parse_money(text, currency), "buybox-price"
            except MissingPriceError:
                continue

    json_ld = BaseStoreSpider.json_ld(response)
    offer = json_ld.get("offers") if isinstance(json_ld, dict) else None
    if isinstance(offer, list) and offer:
        offer = offer[0]
    if isinstance(offer, dict) and offer.get("price") is not None:
        try:
            return (
                parse_money(str(offer.get("price")), currency),
                "json-ld-offer",
            )
        except MissingPriceError:
            pass
    raise MissingPriceError("Preço da Buy Box Amazon não encontrado")


def extract_list_price(
    response: Response, currency: str, price: Decimal
) -> tuple[Decimal | None, str]:
    for selector in _LIST_PRICE_SELECTORS:
        for raw in response.css(selector).getall():
            text = (raw or "").strip()
            if not text or not any(ch.isdigit() for ch in text):
                continue
            try:
                amount = parse_money(text, currency)
            except MissingPriceError:
                continue
            if amount > price:
                return amount, "buybox-list-price"
    return None, "not-found"


def extract_pix_price(
    response: Response,
    marketplace: AmazonMarketplace,
    *,
    buybox_price: Decimal | None = None,
) -> tuple[Decimal | None, str]:
    if marketplace.country != "BR":
        return None, "not-applicable"
    text = " ".join(
        t.strip()
        for t in response.css(
            "#ppd ::text, #corePriceDisplay_desktop_feature_div ::text, "
            "#apex_desktop ::text, #buybox ::text, #qualifiedBuybox ::text"
        ).getall()
        if t and t.strip()
    )
    match = _PIX_PRICE_RE.search(text)
    if match:
        raw = next((group for group in match.groups() if group), None)
        if raw:
            try:
                return parse_money(raw, marketplace.currency), "buybox-pix"
            except MissingPriceError:
                pass
    folded = " ".join(text.casefold().split())
    pix_marker = (
        "à vista no pix" in folded
        or "a vista no pix" in folded
        or ("nupay" in folded and "pix" in folded)
    )
    if buybox_price is not None and pix_marker:
        return buybox_price, "buybox-as-pix"
    return None, "not-found"


def extract_installment(
    response: Response, marketplace: AmazonMarketplace
) -> tuple[Decimal | None, int | None, str]:
    if marketplace.country != "BR":
        # US monthly financing must never become price; omit as installment too.
        return None, None, "not-applicable"
    text = " ".join(
        t.strip()
        for t in response.css(
            "#ppd ::text, #installmentCalculatorCentral_feature_div ::text, "
            "#apex_desktop ::text"
        ).getall()
        if t and t.strip()
    )
    match = _INSTALLMENT_BR_RE.search(text)
    if not match:
        return None, None, "not-found"
    try:
        count = int(match.group(1))
        amount = parse_money(match.group(2), marketplace.currency)
    except (MissingPriceError, ValueError):
        return None, None, "not-found"
    if count <= 1:
        return None, None, "not-found"
    return amount, count, "buybox-installment"


def extract_conditional_pricing(
    response: Response, marketplace: AmazonMarketplace
) -> dict[str, Any]:
    text = (response.text or "")[:80000].casefold()
    scoped = " ".join(
        t.strip().casefold()
        for t in response.css("#ppd ::text, #apex_desktop ::text").getall()
        if t and t.strip()
    )
    haystack = f"{scoped}\n{text}"
    meta: dict[str, Any] = {}
    if any(m in haystack for m in marketplace.coupon_markers):
        meta["coupon_required"] = True
        coupon_match = _COUPON_VALUE_RE.search(haystack)
        if coupon_match:
            meta["coupon_label"] = coupon_match.group(0).strip()
    if any(m in haystack for m in marketplace.prime_markers):
        # Presence of Prime badge/UX — not proof that price is Prime-only.
        meta["prime_badge"] = True
    if any(m in haystack for m in marketplace.subscribe_markers):
        meta["subscribe_and_save"] = True
    if marketplace.country == "BR" and any(
        m in haystack for m in marketplace.pix_markers
    ):
        meta["pix_available"] = True
    return meta


def extract_availability(
    response: Response,
    marketplace: AmazonMarketplace,
    *,
    price: Decimal | None,
) -> tuple[Availability, str]:
    texts = [
        t.strip()
        for t in response.css(
            "#availability span::text, #availability ::text, "
            "#outOfStock ::text, #availability_feature_div ::text"
        ).getall()
        if t and t.strip()
    ]
    avail_text = " ".join(texts).casefold()
    if avail_text:
        if any(marker in avail_text for marker in marketplace.out_of_stock_markers):
            return "out_of_stock", "availability-dom"
        if any(marker in avail_text for marker in marketplace.in_stock_markers):
            return "available", "availability-dom"

    json_ld = BaseStoreSpider.json_ld(response)
    offer = json_ld.get("offers") if isinstance(json_ld, dict) else None
    if isinstance(offer, list) and offer:
        offer = offer[0]
    if isinstance(offer, dict):
        availability = str(offer.get("availability") or "").casefold()
        if "outofstock" in availability or "soldout" in availability:
            return "out_of_stock", "json-ld-offer"
        if "instock" in availability or "limitedavailability" in availability:
            return "available", "json-ld-offer"

    if price is not None and price > 0:
        # Price-first for US reference (ADR 0013/0015): shipping restrictions
        # must not flip availability.
        return "available", "price-present"
    return "unavailable", "not-found"


def extract_seller(
    response: Response, marketplace: AmazonMarketplace
) -> tuple[str | None, str, str | None]:
    seller = _first_nonempty(
        response.css(
            "#merchantInfoFeature_feature_div "
            ".offer-display-feature-text-message::text, "
            "#sellerProfileTriggerId::text"
        ).getall()
    )
    fulfilled = _first_nonempty(
        response.css(
            "#fulfillerInfoFeature_feature_div "
            ".offer-display-feature-text-message::text"
        ).getall()
    )
    if seller:
        if not fulfilled and "amazon" in seller.casefold():
            fulfilled = seller
        return seller, "buybox-merchant", fulfilled

    merchant_bits = [
        t.strip()
        for t in response.css("#merchant-info ::text, #tabular-buybox ::text").getall()
        if t and t.strip()
    ]
    merchant_text = " ".join(merchant_bits)
    sold = _value_after_label(merchant_text, marketplace.sold_by_labels)
    ships = _value_after_label(merchant_text, marketplace.ships_from_labels)
    if not fulfilled:
        fulfilled = ships
    if sold:
        if not fulfilled and "amazon" in sold.casefold():
            fulfilled = sold
        return sold, "merchant-info", fulfilled
    if "amazon" in merchant_text.casefold():
        return (
            marketplace.default_seller,
            "merchant-info-amazon",
            fulfilled or marketplace.default_seller,
        )
    return None, "not-found", fulfilled


def extract_parent_asin(text: str, asin: str) -> str | None:
    match = _PARENT_ASIN_RE.search(text)
    if match:
        return match.group(1).upper()
    return None


def extract_selected_variant_dimensions(text: str, asin: str) -> dict[str, str]:
    values_match = _DIMENSION_VALUES_RE.search(text)
    if not values_match:
        return {}
    try:
        values = json.loads(values_match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(values, dict):
        return {}
    selected = values.get(asin) or values.get(asin.upper()) or values.get(asin.lower())
    if not isinstance(selected, list):
        return {}

    dim_keys: list[str] = []
    order_match = _DIMENSIONS_ORDER_RE.search(text)
    if order_match:
        try:
            parsed = json.loads(order_match.group(1))
            if isinstance(parsed, list):
                dim_keys = [str(item) for item in parsed]
        except json.JSONDecodeError:
            dim_keys = []

    labels: dict[str, str] = {}
    labels_match = _VARIATION_LABELS_RE.search(text)
    if labels_match:
        try:
            parsed_labels = json.loads(labels_match.group(1))
            if isinstance(parsed_labels, dict):
                labels = {str(k): str(v) for k, v in parsed_labels.items()}
        except json.JSONDecodeError:
            labels = {}

    result: dict[str, str] = {}
    for index, raw in enumerate(selected):
        if raw is None:
            continue
        raw_key = dim_keys[index] if index < len(dim_keys) else f"dimension_{index}"
        display = labels.get(raw_key, raw_key)
        key = (
            str(display).replace("_name", "").replace("_", " ").strip().lower()
            or f"option_{index}"
        )
        result[key] = str(raw).strip()
    return {k: v for k, v in result.items() if v}


def extract_color_images(text: str) -> list[str]:
    match = _COLOR_IMAGES_RE.search(text)
    if not match:
        return []
    raw = match.group(1) or match.group(2) or ""
    if match.group(1):
        try:
            raw = raw.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    try:
        images = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(images, list):
        return []
    urls: list[str] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        for key in ("hiRes", "large", "main"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
                break
            if isinstance(value, dict):
                # main can be {url: [w,h], ...}; pick largest by declared size.
                best: str | None = None
                best_area = -1
                for url, dims in value.items():
                    area = 0
                    if isinstance(dims, list) and len(dims) >= 2:
                        try:
                            area = int(dims[0]) * int(dims[1])
                        except (TypeError, ValueError):
                            area = 0
                    if area >= best_area and isinstance(url, str):
                        best = url
                        best_area = area
                if best:
                    urls.append(best)
                    break
    return urls


def extract_brand(response: Response, specifications: dict[str, Any]) -> str | None:
    byline = response.css("#bylineInfo::text, a#bylineInfo::text").get()
    if byline:
        cleaned = re.sub(
            r"^(?:visita a loja|visite a loja|visit the|brand:|marca:)\s*",
            "",
            byline.strip(),
            flags=re.I,
        ).strip()
        if cleaned:
            return cleaned
    return _spec_value(specifications, "brand", "marca", "manufacturer", "fabricante")


def extract_gtin(response: Response, specifications: dict[str, Any]) -> str | None:
    for key in (
        "gtin",
        "ean",
        "upc",
        "isbn-13",
        "isbn",
        "código de barras",
        "codigo de barras",
    ):
        value = _spec_value(specifications, key)
        if value:
            digits = re.sub(r"\D", "", value)
            if len(digits) in {8, 12, 13, 14}:
                return digits
    json_ld = BaseStoreSpider.json_ld(response)
    for key in ("gtin13", "gtin12", "gtin8", "gtin", "isbn"):
        value = json_ld.get(key)
        if value:
            digits = re.sub(r"\D", "", str(value))
            if len(digits) in {8, 12, 13, 14}:
                return digits
    return None


def extract_description(response: Response) -> str | None:
    bullets = [
        t.strip()
        for t in response.css("#feature-bullets li span.a-list-item::text").getall()
        if t and t.strip() and "make sure" not in t.casefold()
    ]
    if bullets:
        return " ".join(bullets[:8])
    json_ld = BaseStoreSpider.json_ld(response)
    desc = json_ld.get("description")
    return str(desc).strip() if desc else None


def extract_specifications(response: Response) -> dict[str, Any]:
    specs: dict[str, Any] = {}
    for row in response.css(
        "#productDetails_techSpec_section_1 tr, "
        "#productDetails_detailBullets_sections1 tr, "
        "#productDetails_techSpec_section_2 tr"
    ):
        label = " ".join(row.css("th ::text").getall()).strip(" \n\t\u200f\u200e:")
        value = " ".join(row.css("td ::text").getall()).strip(" \n\t\u200f\u200e")
        if label and value and label.casefold() not in specs:
            specs[label] = value
    for item in response.css("#detailBullets_feature_div li"):
        label = " ".join(item.css(".a-text-bold::text").getall())
        label = re.sub(r"[:\s\u200f\u200e]+$", "", label).strip()
        texts = [
            t.strip()
            for t in item.css("span::text").getall()
            if t and t.strip() and t.strip() not in {":", "‏", "‎"}
        ]
        value = " ".join(
            t for t in texts if t.casefold() not in label.casefold()
        ).strip()
        if label and value and label.casefold() not in {k.casefold() for k in specs}:
            specs[label] = value
    return specs


def _dynamic_image_urls(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(unescape(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    ranked: list[tuple[int, str]] = []
    for url, dims in data.items():
        area = 0
        if isinstance(dims, list) and len(dims) >= 2:
            try:
                area = int(dims[0]) * int(dims[1])
            except (TypeError, ValueError):
                area = 0
        if isinstance(url, str) and url.startswith("http"):
            ranked.append((area, url))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in ranked]


def _looks_like_monthly_payment(text: str) -> bool:
    folded = text.casefold()
    return any(
        marker in folded
        for marker in ("/mo", "/month", "a month", "por mês", "por mes", "ao mês")
    )


def _first_nonempty(values: list[str]) -> str | None:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return None


def _value_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    folded = text.casefold()
    for label in labels:
        idx = folded.find(label.casefold())
        if idx < 0:
            continue
        tail = text[idx + len(label) :].strip(" \t:-–")
        if not tail:
            continue
        # Stop at common separators.
        piece = re.split(r"[.\n|]| and | e ", tail, maxsplit=1)[0].strip()
        if piece:
            return piece
    return None


def _spec_value(specs: dict[str, Any], *keys: str) -> str | None:
    wanted = {key.casefold() for key in keys}
    for label, value in specs.items():
        if str(label).casefold() in wanted and value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
