import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlparse

from scrapy.http import Response

from ...core.exceptions import ParseError
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


class MagazineLuizaSpider(BaseStoreSpider):
    """Parse Magalu offers and catalog details from structured page data and HTML."""

    name = "magazineluiza"
    store, country, currency = "magazineluiza", "BR", "BRL"
    allowed_domains = ["magazineluiza.com.br", "m.magazineluiza.com.br"]
    start_urls: list[str] = []

    def parse_product(self, response: Response) -> ProductPriceItem:
        return compose_product_price_item(
            self.extract_offer(response),
            self.extract_details(response),
        )

    def extract_offer(self, response: Response) -> ProductOffer:
        json_ld = self.json_ld(response)
        state = self._next_data(response)
        item = self._state_item(state)
        self._ensure_product_page(response, item)
        offer = self._selected_offer(item, response.url)
        fallback_offer = self._selected_fallback_offer(item, response.url)
        page_text = " ".join(response.css("body ::text").getall())

        price, price_source = self._regular_price(
            offer, fallback_offer, json_ld, page_text
        )
        pix_price, pix_source = self._pix_price(offer, json_ld, page_text)
        original_price, original_source = self._original_price(
            offer, fallback_offer, page_text, price
        )
        installment_price, installment_count, installment_source = self._installment(
            offer, page_text
        )
        availability, availability_source = self._availability(
            response, offer, json_ld, page_text
        )

        product_id = (
            item.get("id")
            or item.get("offerId")
            or json_ld.get("sku")
            or self._product_id(response.url)
        )
        if not product_id:
            raise ParseError("Identificador do produto não encontrado")
        seller_value = offer.get("seller")
        seller_data: dict[str, Any] = (
            seller_value if isinstance(seller_value, dict) else {}
        )
        seller = (
            seller_data.get("id")
            or seller_data.get("deliveryId")
            or self._seller_from_url(response.url)
            or self._seller_from_text(page_text)
        )
        sku = seller_data.get("sku") or offer.get("sku") or json_ld.get("sku")
        original_price = (
            original_price if original_price and original_price > price else None
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
            original_price=original_price,
            discount_percentage=self._discount_percentage(offer, price, original_price),
            pix_price=pix_price,
            installment_price=installment_price,
            installment_count=installment_count,
            available=availability == "available",
            availability=availability,
            metadata={
                "source": {
                    "price": price_source,
                    "pix_price": pix_source,
                    "original_price": original_source,
                    "installment": installment_source,
                    "availability": availability_source,
                    "seller": "offer-state" if seller_data else "url-or-rendered-text",
                    "product_id": "product-state"
                    if item.get("id")
                    else "json-ld-or-url",
                    "sku": "offer-seller-state"
                    if seller_data.get("sku")
                    else "json-ld",
                }
            },
        )

    def extract_details(self, response: Response) -> ProductDetails:
        json_ld = self.json_ld(response)
        state = self._next_data(response)
        item = self._state_item(state)
        self._ensure_product_page(response, item)
        offer = self._selected_offer(item, response.url)
        seller_value = offer.get("seller")
        seller_data: dict[str, Any] = (
            seller_value if isinstance(seller_value, dict) else {}
        )

        title = (
            item.get("title")
            or json_ld.get("name")
            or self.first(response, ["h1::text", "title::text"])
        )
        if not title:
            raise ParseError("Título do produto não encontrado")

        product_id = (
            item.get("id")
            or item.get("offerId")
            or json_ld.get("sku")
            or self._product_id(response.url)
        )
        if not product_id:
            raise ParseError("Identificador do produto não encontrado")

        sku = seller_data.get("sku") or offer.get("sku") or json_ld.get("sku")
        brand = self._brand(item, json_ld, response)
        model = self._specification(response, "Modelo")
        variant = item.get("color") or self._specification(response, "Cor")
        title_text = str(title).strip()
        specifications = self._table_specifications(response)
        resolved = resolve_product_identity(
            specifications=specifications,
            structured={
                "brand": brand,
                "model": model,
                "color": variant,
            },
            title=title_text,
        )
        specifications = merge_specification_gaps(specifications, resolved)
        attribute_sources = resolved.found_sources()
        metadata_source = {
            "title": "product-state" if item.get("title") else "json-ld-or-h1",
            "product_id": "product-state" if item.get("id") else "json-ld-or-url",
            "sku": "offer-seller-state" if seller_data.get("sku") else "json-ld",
            "brand": attribute_sources.get(
                "brand", "structured-or-html" if brand else "not-found"
            ),
            "model": attribute_sources.get(
                "model", "html-specification" if model else "not-found"
            ),
            "variant": "product-state-or-html" if variant else "not-found",
            "specifications": "html-table-and-title-fallback"
            if specifications
            else "not-found",
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
            sku=self._string(sku),
            gtin=self._gtin(item, response),
            title=title_text,
            brand=self._string(brand) or resolved.value("brand"),
            model=resolved.value("model"),
            # Keep Magalu's raw color/variant label when present.
            variant=self._string(variant) or format_identity_variant(resolved),
            description=None,
            specifications=specifications,
            metadata={"source": metadata_source},
        )

    def extract_images(self, response: Response) -> list[str]:
        """Official PDP gallery from ``item.media.images`` (NEXT_DATA).

        Magalu ships template CDN URLs with ``{w}x{h}``. JSON-LD exposes only
        the primary image — never treat that as the full gallery.
        """
        from ...utils.image_pipeline import ImagePipelineStats, Timer

        stats = ImagePipelineStats(store=self.store)
        discovery = Timer()
        state = self._next_data(response)
        item = self._state_item(state)
        stats.discovery_ms = discovery.ms()

        parse = Timer()
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        raw_gallery = media.get("images") if isinstance(media, dict) else None
        source = "next-data-media.images"
        if not isinstance(raw_gallery, list) or not raw_gallery:
            # Legacy / fixture shape: flat ``item.images`` list of absolute URLs.
            raw_gallery = item.get("images")
            source = "next-data-item.images"
        if not isinstance(raw_gallery, list) or not raw_gallery:
            stats.source = "json-ld-fallback"
            stats.parse_ms = parse.ms()
            urls = super().extract_images(response)
            stats.raw = len(urls)
            stats.after_filter = len(urls)
            stats.after_dedup = len(urls)
            stats.mark_returned(urls)
            stats.log()
            response.meta["image_pipeline"] = stats
            return urls

        stats.source = source
        stats.raw = len(raw_gallery)
        stats.parse_ms = parse.ms()

        filter_t = Timer()
        product_id = str(item.get("id") or self._product_id(response.url) or "")
        materialized: list[str] = []
        for entry in raw_gallery:
            for absolute in self.normalize_image_urls(entry, base_url=response.url):
                resolved = self._materialize_cdn_template(absolute)
                if self._is_official_gallery_url(resolved, product_id):
                    materialized.append(resolved)
        stats.after_filter = len(materialized)
        stats.filter_ms = filter_t.ms()

        dedup_t = Timer()
        main_url = None
        main_raw = item.get("image")
        if isinstance(main_raw, str) and main_raw.strip():
            main_candidates = self.normalize_image_urls(
                main_raw, base_url=response.url
            )
            if main_candidates:
                candidate = self._materialize_cdn_template(main_candidates[0])
                if self._is_official_gallery_url(candidate, product_id):
                    main_url = candidate
        urls = self._dedupe_gallery_preserve_order(materialized, main_url=main_url)
        stats.after_dedup = len(urls)
        stats.dedup_ms = dedup_t.ms()
        stats.mark_returned(urls)
        stats.log()
        response.meta["image_pipeline"] = stats
        return urls

    # Magalu CDN templates use ``{w}x{h}``; prefer a concrete high-res size that
    # the public CDN accepts (verified live: 450/800/1000/1200 all 200).
    _CDN_TEMPLATE_SIZE = "1200x1200"

    @classmethod
    def _materialize_cdn_template(cls, url: str) -> str:
        text = url.strip()
        if not text:
            return text
        text = text.replace("{w}x{h}", cls._CDN_TEMPLATE_SIZE)
        text = text.replace("{w}", "1200").replace("{h}", "1200")
        # Same asset is often mirrored on the mobile host; prefer public CDN.
        text = text.replace(
            "https://m.magazineluiza.com.br/a-static/",
            "https://a-static.mlcdn.com.br/",
        )
        return text

    @staticmethod
    def _gallery_asset_identity(url: str) -> str:
        """Identity by content hash filename — not by resolution query/path size."""
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0].casefold()
        return stem

    @classmethod
    def _is_official_gallery_url(cls, url: str, product_id: str) -> bool:
        lowered = url.casefold()
        if not lowered.startswith(("http://", "https://")):
            return False
        if any(
            marker in lowered
            for marker in (
                "logo",
                "selo-",
                "badge",
                "/sellers/",
                "review",
                "avatar",
                "banner",
                "icon",
                "/embed/",
                "ugc-magalu-videos",
            )
        ):
            return False
        host = (urlparse(url).hostname or "").casefold()
        if "mlcdn.com.br" not in host and "magazineluiza.com.br" not in host:
            return False
        path = urlparse(url).path.casefold()
        # Real Magalu CDN embeds ``/{seller}/{product_id}/{hash}.jpg`` — keep
        # only the selected product id (exclude other color/storage variants).
        if product_id and (
            "/magazineluiza/" in path or re.search(r"/[a-f0-9]{32}\.", path)
        ):
            return f"/{product_id.casefold()}/" in path
        return True

    @classmethod
    def _dedupe_gallery_preserve_order(
        cls, urls: list[str], *, main_url: str | None
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        if main_url:
            identity = cls._gallery_asset_identity(main_url)
            if identity and identity not in seen:
                seen.add(identity)
                ordered.append(main_url)
        for url in urls:
            identity = cls._gallery_asset_identity(url)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            ordered.append(url)
        return ordered

    @staticmethod
    def _next_data(response: Response) -> dict[str, Any]:
        selectors = "script#__NEXT_DATA__::text, script[type='application/json']::text"
        for raw in response.css(selectors).getall():
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("props"), dict):
                return value
        return {}

    @staticmethod
    def _state_item(state: dict[str, Any]) -> dict[str, Any]:
        props = state.get("props")
        page_props = props.get("pageProps") if isinstance(props, dict) else None
        data = page_props.get("data") if isinstance(page_props, dict) else None
        item = data.get("item") if isinstance(data, dict) else None
        return item if isinstance(item, dict) else {}

    @classmethod
    def _ensure_product_page(cls, response: Response, item: dict[str, Any]) -> None:
        """Fail closed on Magalu soft-404 shells (``Oops!``) before price parse."""
        h1 = " ".join(
            part.strip()
            for part in response.css("h1 ::text").getall()
            if part and part.strip()
        )
        folded_h1 = cls._fold(h1)
        if folded_h1 == "oops" or folded_h1.startswith("oops"):
            raise ParseError("Página Magalu não encontrada (soft-404)")
        has_product = bool(
            item.get("id")
            or item.get("offerId")
            or item.get("title")
            or item.get("offers")
        )
        if not has_product:
            title = (response.css("title::text").get() or "").strip()
            if "pra voce e magalu" in cls._fold(title):
                raise ParseError("Página Magalu não encontrada (soft-404)")

    @classmethod
    def _selected_offer(cls, item: dict[str, Any], url: str) -> dict[str, Any]:
        offers = item.get("offers")
        if not isinstance(offers, list):
            return {}
        requested = cls._seller_from_url(url)
        for offer in offers:
            seller = offer.get("seller") if isinstance(offer, dict) else None
            if requested and isinstance(seller, dict) and seller.get("id") == requested:
                return cast(dict[str, Any], offer)
        return next(
            (
                cast(dict[str, Any], offer)
                for offer in offers
                if isinstance(offer, dict)
            ),
            {},
        )

    @classmethod
    def _selected_fallback_offer(cls, item: dict[str, Any], url: str) -> dict[str, Any]:
        fallback = item.get("itemFallback")
        offers = fallback.get("offers") if isinstance(fallback, dict) else None
        if not isinstance(offers, list):
            return {}
        requested = cls._seller_from_url(url)
        for offer in offers:
            seller = offer.get("seller") if isinstance(offer, dict) else None
            if requested and isinstance(seller, dict) and seller.get("id") == requested:
                return cast(dict[str, Any], offer)
        return next(
            (
                cast(dict[str, Any], offer)
                for offer in offers
                if isinstance(offer, dict)
            ),
            {},
        )

    def _regular_price(
        self,
        offer: dict[str, Any],
        fallback: dict[str, Any],
        json_ld: dict[str, Any],
        text: str,
    ) -> tuple[Decimal, str]:
        raw = offer.get("price") or fallback.get("price")
        if raw is not None:
            return self._state_money(raw), "product-offer-state"
        match = re.search(
            r"\bou\s+r\$\s*([\d.]+(?:,\d{2})?)\s+em\s+\d+\s*x", self._fold(text), re.I
        )
        if match:
            return parse_money(match.group(1), self.currency), "rendered-regular-price"
        match = re.search(r"preco\s+r\$\s*([\d.]+(?:,\d{2})?)", self._fold(text), re.I)
        if match:
            return parse_money(match.group(1), self.currency), "rendered-price-label"
        structured_value = json_ld.get("offers")
        structured: dict[str, Any] = (
            structured_value if isinstance(structured_value, dict) else {}
        )
        if structured.get("price") is not None:
            return self._state_money(structured["price"]), "json-ld-offer"
        return parse_money(None, self.currency), "missing"

    def _pix_price(
        self, offer: dict[str, Any], json_ld: dict[str, Any], text: str
    ) -> tuple[Decimal | None, str]:
        best = offer.get("bestPrice")
        if (
            isinstance(best, dict)
            and str(best.get("paymentMethodId", "")).lower() == "pix"
        ):
            return self._state_money(best.get("totalAmount")), "payment-method-pix"
        match = re.search(
            r"r\$\s*([\d.]+(?:,\d{2})?)\s+no\s+pix", self._fold(text), re.I
        )
        if match:
            return parse_money(match.group(1), self.currency), "rendered-pix-price"
        structured_value = json_ld.get("offers")
        structured: dict[str, Any] = (
            structured_value if isinstance(structured_value, dict) else {}
        )
        return (
            (self._state_money(structured["price"]), "json-ld-offer")
            if structured.get("price") is not None
            else (None, "not-found")
        )

    def _original_price(
        self, offer: dict[str, Any], fallback: dict[str, Any], text: str, price: Decimal
    ) -> tuple[Decimal | None, str]:
        raw = offer.get("listPrice") or fallback.get("listPrice")
        if raw is not None:
            candidate = self._state_money(raw)
            if candidate > price:
                return candidate, "offer-list-price"
        match = re.search(
            r"(?:^|\s)de\s+r\$\s*([\d.]+(?:,\d{2})?)", self._fold(text), re.I
        )
        if match:
            candidate = parse_money(match.group(1), self.currency)
            if candidate > price:
                return candidate, "rendered-previous-price"
        return None, "not-found"

    def _installment(
        self, offer: dict[str, Any], text: str
    ) -> tuple[Decimal | None, int | None, str]:
        plan = offer.get("bestInstallmentPlan")
        if (
            isinstance(plan, dict)
            and plan.get("installment")
            and plan.get("installmentAmount")
        ):
            return (
                self._state_money(plan["installmentAmount"]),
                int(plan["installment"]),
                "best-installment-plan",
            )
        match = re.search(
            r"\bem\s+(\d+)\s*x\s+de\s+r\$\s*([\d.]+(?:,\d{2})?)", self._fold(text), re.I
        )
        if match:
            return (
                parse_money(match.group(2), self.currency),
                int(match.group(1)),
                "rendered-installment",
            )
        return None, None, "not-found"

    @staticmethod
    def _availability(
        response: Response, offer: dict[str, Any], json_ld: dict[str, Any], text: str
    ) -> tuple[Availability, str]:
        seller_value = offer.get("seller")
        seller: dict[str, Any] = seller_value if isinstance(seller_value, dict) else {}
        if seller.get("available") is False or offer.get("available") is False:
            return "out_of_stock", "offer-state"
        structured_value = json_ld.get("offers")
        structured: dict[str, Any] = (
            structured_value if isinstance(structured_value, dict) else {}
        )
        structured_text = str(structured.get("availability", "")).lower()
        if any(marker in structured_text for marker in ("outofstock", "soldout")):
            return "out_of_stock", "json-ld-offer"
        folded = MagazineLuizaSpider._fold(text)
        if re.search(
            r"produto\s+(?:esgotado|indisponivel)|fora\s+de\s+estoque", folded
        ):
            return "out_of_stock", "rendered-stock-message"
        if "indisponivel" in folded:
            return "unavailable", "rendered-stock-message"
        buttons = response.css("button")
        button_text = " ".join(buttons.css("::text").getall())
        if re.search(
            r"adicionar\s+a\s+sacola|comprar\s+agora",
            f"{folded} {MagazineLuizaSpider._fold(button_text)}",
        ):
            return "available", "purchase-controls"
        raise ParseError("Sinal de disponibilidade não encontrado")

    @staticmethod
    def _brand(
        item: dict[str, Any], json_ld: dict[str, Any], response: Response
    ) -> str | None:
        raw = item.get("brand")
        if isinstance(raw, dict):
            raw = raw.get("label")
        raw = (
            raw
            or json_ld.get("brand")
            or MagazineLuizaSpider._specification(response, "Marca")
        )
        return MagazineLuizaSpider._string(raw)

    @staticmethod
    def _gtin(item: dict[str, Any], response: Response) -> str | None:
        for key in ("gtin", "gtin13", "gtin12", "ean"):
            if item.get(key):
                return str(item[key]).strip()
        return MagazineLuizaSpider._specification(
            response, "EAN"
        ) or MagazineLuizaSpider._specification(response, "GTIN")

    @staticmethod
    def _specification(response: Response, label: str) -> str | None:
        for row in response.css("tr"):
            cells = row.css("th, td")
            values = [" ".join(cell.css("::text").getall()).strip() for cell in cells]
            if values and MagazineLuizaSpider._fold(
                values[0]
            ) == MagazineLuizaSpider._fold(label):
                return values[-1] or None
        return None

    @staticmethod
    def _table_specifications(response: Response) -> dict[str, str]:
        specs: dict[str, str] = {}
        for row in response.css("tr"):
            cells = row.css("th, td")
            values = [" ".join(cell.css("::text").getall()).strip() for cell in cells]
            if len(values) >= 2 and values[0] and values[-1]:
                specs[values[0]] = values[-1]
        return specs

    @staticmethod
    def _state_money(raw: Any) -> Decimal:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ParseError(f"Valor monetário estruturado inválido: {raw!r}") from exc
        if value <= 0:
            raise ParseError(f"Valor monetário estruturado não positivo: {raw!r}")
        return value.quantize(Decimal("0.01"))

    @staticmethod
    def _discount_percentage(
        offer: dict[str, Any], price: Decimal, original: Decimal | None
    ) -> Decimal | None:
        best = offer.get("bestPrice")
        if isinstance(best, dict) and best.get("discount") is not None:
            return Decimal(str(best["discount"])).quantize(Decimal("0.01"))
        if original is None or original <= price:
            return None
        return ((original - price) * 100 / original).quantize(Decimal("0.01"))

    @staticmethod
    def _seller_from_url(url: str) -> str | None:
        return parse_qs(urlparse(url).query).get("seller_id", [None])[0]

    @staticmethod
    def _seller_from_text(text: str) -> str | None:
        match = re.search(
            r"vendido\s+e\s+entregue\s+por\s+([\w!.-]+)",
            MagazineLuizaSpider._fold(text),
            re.I,
        )
        return match.group(1).lower() if match else None

    @staticmethod
    def _product_id(url: str) -> str | None:
        match = re.search(r"/p/([^/?]+)", urlparse(url).path)
        return match.group(1) if match else None

    @staticmethod
    def _string(value: Any) -> str | None:
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _fold(value: str) -> str:
        value = (
            value.replace("Ã§", "ç")
            .replace("Ã£", "ã")
            .replace("Ã¡", "á")
            .replace("Ã©", "é")
        )
        return "".join(
            c
            for c in unicodedata.normalize("NFKD", value.lower())
            if not unicodedata.combining(c)
        )
