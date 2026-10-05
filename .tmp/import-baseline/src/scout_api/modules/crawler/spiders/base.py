import json
import logging
from abc import ABC
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, cast
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.http import Response

from ..core.circuit_breaker import CircuitBreaker
from ..core.exceptions import MissingPriceError, ParseError, RequestError
from ..core.fingerprints import canonicalize_url
from ..core.retry import retry_after
from ..models.product import (
    ProductDetails,
    ProductOffer,
    ProductPriceItem,
    compose_product_price_item,
)
from ..utils.parsing import parse_money

logger = logging.getLogger(__name__)


class BaseStoreSpider(scrapy.Spider, ABC):
    """Shared policy layer. Store adapters define offer/details extraction."""

    store: ClassVar[str]
    country: ClassVar[str]
    currency: ClassVar[str]
    supports_images: ClassVar[bool] = True
    allowed_domains: list[str] = []
    custom_settings: dict[str, Any] = {
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 60.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY": True,
        "RETRY_TIMES": 3,
        "RETRY_HTTP_CODES": [408, 429, 500, 502, 503, 504],
    }
    circuit_breakers: ClassVar[dict[str, CircuitBreaker]] = {}

    def __init__(
        self, start_urls: str | None = None, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        if start_urls:
            self.start_urls = [
                url.strip() for url in start_urls.split(",") if url.strip()
            ]

    def start_requests(self):  # type: ignore[no-untyped-def]
        for url in self.start_urls:
            yield scrapy.Request(
                url, callback=self.parse, errback=self.on_error, dont_filter=False
            )

    def on_error(self, failure: Any) -> None:
        logger.warning(
            "crawl_request_failed",
            extra={"store": self.store, "error": str(failure.value)},
        )

    def parse(self, response: Response):  # type: ignore[no-untyped-def]
        domain = urlparse(response.url).netloc
        breaker = self.circuit_breakers.setdefault(domain, CircuitBreaker())
        if response.status >= 400 and response.status != 429:
            breaker.record_failure()
            raise RequestError(f"HTTP {response.status} recebido de {response.url}")
        if response.status == 429:
            header = response.headers.get("Retry-After") or b""
            delay = retry_after(header.decode())
            breaker.record_failure()
            logger.warning(
                "rate_limited", extra={"store": self.store, "retry_after": delay}
            )
            return
        if response.status >= 500:
            breaker.record_failure()
            return
        breaker.record_success()
        try:
            yield self.parse_product(response)
        except (ParseError, MissingPriceError):
            logger.exception(
                "parse_error", extra={"store": self.store, "url": response.url}
            )
            raise

    def parse_product(self, response: Response) -> ProductPriceItem:
        return compose_product_price_item(
            self.extract_offer(response),
            self.extract_details(response),
        )

    def extract_offer(self, response: Response) -> ProductOffer:
        data = self.json_ld(response)
        offers = data.get("offers") if isinstance(data, dict) else None
        structured: dict[str, Any] = offers if isinstance(offers, dict) else {}
        raw_price = structured.get("price") or self.first(
            response,
            [
                "[itemprop='price']::attr(content)",
                ".price::text",
                ".product-price::text",
            ],
        )
        price = parse_money(
            str(raw_price) if raw_price is not None else None, self.currency
        )
        product_id = data.get("sku") or self.first(
            response,
            [
                "[itemprop='sku']::attr(content)",
                "[data-product-id]::attr(data-product-id)",
            ],
        )
        if not product_id:
            product_id = canonicalize_url(response.url)
        available = str(structured.get("availability", "")).lower() not in {
            "outofstock",
            "false",
        }
        return ProductOffer(
            store=self.store,
            country=self.country,
            product_id=str(product_id),
            sku=str(product_id),
            url=response.url,
            canonical_url=canonicalize_url(response.url),
            currency=self.currency,
            price=Decimal(price),
            available=available,
            availability="available" if available else "out_of_stock",
            metadata={"source": "json-ld-or-selector"},
        )

    def extract_details(self, response: Response) -> ProductDetails:
        data = self.json_ld(response)
        title = data.get("name") or self.first(response, ["h1::text", "title::text"])
        if not title:
            raise ParseError("Título do produto não encontrado")
        product_id = data.get("sku") or self.first(
            response,
            [
                "[itemprop='sku']::attr(content)",
                "[data-product-id]::attr(data-product-id)",
            ],
        )
        if not product_id:
            product_id = canonicalize_url(response.url)
        return ProductDetails(
            product_id=str(product_id),
            sku=str(product_id),
            title=str(title).strip(),
            metadata={"source": "json-ld-or-selector"},
        )

    def extract_images(self, response: Response) -> list[str]:
        """Extract and normalize gallery URLs. Not called unless include_images."""
        data = self.json_ld(response)
        return self.normalize_image_urls(data.get("image"), base_url=response.url)

    def prepare_fetch_url(self, url: str) -> str:
        """Store-specific URL adjustments before the HTML fetch."""
        return url

    @staticmethod
    def normalize_image_urls(raw: Any, *, base_url: str) -> list[str]:
        """Flatten common image shapes into absolute, de-duplicated URL strings."""
        values: list[Any]
        if raw is None:
            values = []
        elif isinstance(raw, list):
            values = raw
        elif isinstance(raw, dict):
            # Nested gallery bags (e.g. Magalu ``media`` / catalog ``images``).
            nested = raw.get("images")
            if isinstance(nested, list):
                values = list(nested)
            elif isinstance(raw.get("details"), list):
                main = raw.get("main")
                values = ([main] if main else []) + list(raw["details"])
            else:
                values = [raw]
        else:
            values = [raw]

        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            candidate: Any = value
            if isinstance(value, dict):
                nested_images = value.get("images")
                if isinstance(nested_images, list):
                    for nested in BaseStoreSpider.normalize_image_urls(
                        nested_images, base_url=base_url
                    ):
                        if nested not in seen:
                            seen.add(nested)
                            result.append(nested)
                    continue
                candidate = (
                    value.get("url")
                    or value.get("contentUrl")
                    or value.get("src")
                    or value.get("image")
                    or value.get("path")
                )
            if candidate is None:
                continue
            text = str(candidate).strip()
            if not text:
                continue
            absolute = urljoin(base_url, text)
            if absolute in seen:
                continue
            seen.add(absolute)
            result.append(absolute)
        return result

    @staticmethod
    def first(response: Any, selectors: list[str]) -> str | None:
        for selector in selectors:
            value = response.css(selector).get()
            if value and value.strip():
                return cast(str, value.strip())
        return None

    @staticmethod
    def json_ld(response: Response) -> dict[str, Any]:
        for raw in response.css("script[type='application/ld+json']::text").getall():
            try:
                value = json.loads(raw)
                candidates = value if isinstance(value, list) else [value]
                for item in candidates:
                    types = item.get("@type", []) if isinstance(item, dict) else []
                    if isinstance(item, dict) and (
                        types == "Product"
                        or (isinstance(types, list) and "Product" in types)
                    ):
                        return item
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def scraped_at() -> datetime:
        return datetime.now(UTC)
