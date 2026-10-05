"""Shopee Brazil Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_URL_IDS = re.compile(
    r"[.-]i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)",
    re.IGNORECASE,
)
_EMBEDDED_IDS = re.compile(
    r"(?:^|[^A-Za-z0-9])i\.(?P<shop_id>\d+)\.(?P<item_id>\d+)",
    re.IGNORECASE,
)


class ShopeeSearchAdapter:
    """Candidate discovery for shopee.com.br ``/search?keyword=…``."""

    store_key: ClassVar[str] = "shopee"

    def build_search_request(self, query: str) -> SearchRequest:
        return SearchRequest(
            url=f"https://shopee.com.br/search?keyword={quote_plus(query.strip())}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        """Extract PDP candidates from SERP HTML / embedded JSON when present."""
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()

        # Mode A: JSON captured from the browser's own signed search API.
        for raw in response.css("script[data-shopee-search]::text").getall():
            for candidate in self._candidates_from_search_payload(raw):
                canonical = canonicalize_url(candidate.url)
                if canonical in seen:
                    continue
                seen.add(canonical)
                candidates.append(candidate)
                if len(candidates) >= 10:
                    return candidates
        if candidates:
            return candidates

        # Prefer structured item links in the SERP markup.
        for href in response.css(
            "a[href*='-i.']::attr(href), a[data-sqe='link']::attr(href)"
        ).getall():
            absolute = urljoin(response.url, href.strip())
            match = _URL_IDS.search(absolute)
            if not match:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    product_id=match.group("item_id"),
                    metadata={
                        "source": "shopee-search",
                        "shop_id": match.group("shop_id"),
                        "item_id": match.group("item_id"),
                    },
                )
            )
            if len(candidates) >= 10:
                return candidates

        if candidates:
            return candidates

        # Fallback: item ids embedded in page scripts (SSR / hydration payloads).
        for match in _EMBEDDED_IDS.finditer(response.text or ""):
            shop_id = match.group("shop_id")
            item_id = match.group("item_id")
            absolute = f"https://shopee.com.br/product/{shop_id}/{item_id}"
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    product_id=item_id,
                    metadata={
                        "source": "shopee-search-embedded",
                        "shop_id": shop_id,
                        "item_id": item_id,
                    },
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    @classmethod
    def _candidates_from_search_payload(cls, raw: str) -> list[SearchCandidate]:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = cls._search_items(payload)
        out: list[SearchCandidate] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            basic = (
                row.get("item_basic")
                if isinstance(row.get("item_basic"), dict)
                else row
            )
            if not isinstance(basic, dict):
                continue
            shop_id = cls._id_str(
                basic.get("shopid")
                or basic.get("shop_id")
                or row.get("shopid")
                or row.get("shop_id")
            )
            item_id = cls._id_str(
                basic.get("itemid")
                or basic.get("item_id")
                or row.get("itemid")
                or row.get("item_id")
            )
            if not shop_id or not item_id:
                continue
            title = basic.get("name") or basic.get("title")
            if title is not None:
                title = str(title).strip() or None
            out.append(
                SearchCandidate(
                    url=f"https://shopee.com.br/product/{shop_id}/{item_id}",
                    title=title,
                    product_id=item_id,
                    metadata={
                        "source": "shopee-search-api",
                        "shop_id": shop_id,
                        "item_id": item_id,
                    },
                )
            )
            if len(out) >= 10:
                break
        return out

    @classmethod
    def _search_items(cls, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("items", "item", "products"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("items", "item", "products"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

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
