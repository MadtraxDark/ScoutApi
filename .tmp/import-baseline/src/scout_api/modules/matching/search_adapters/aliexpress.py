"""AliExpress Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_ITEM_PATH = re.compile(
    r"/item/(?:[^/]+/)?(?P<item_id>\d{6,})\.html",
    re.IGNORECASE,
)


class AliExpressSearchAdapter:
    """Candidate discovery for AliExpress wholesale SERP pages."""

    store_key: ClassVar[str] = "aliexpress"

    def build_search_request(self, query: str) -> SearchRequest:
        q = quote_plus(query.strip())
        return SearchRequest(
            url=f"https://pt.aliexpress.com/w/wholesale-{q}.html",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()

        for raw in response.css("script[data-aliexpress-search]::text").getall():
            for candidate in self._candidates_from_search_payload(raw, response.url):
                canonical = canonicalize_url(candidate.url)
                if canonical in seen:
                    continue
                seen.add(canonical)
                candidates.append(candidate)
                if len(candidates) >= 10:
                    return candidates
        if candidates:
            return candidates

        for href in response.css("a[href*='/item/']::attr(href)").getall():
            absolute = urljoin(response.url, href.strip())
            item_id = self._item_id_from_url(absolute)
            if not item_id:
                continue
            canonical = self._canonical_product_url(absolute, item_id)
            if canonical in seen:
                continue
            seen.add(canonical)
            candidates.append(
                SearchCandidate(
                    url=canonical,
                    product_id=item_id,
                    metadata={"source": "aliexpress-search-link", "item_id": item_id},
                )
            )
            if len(candidates) >= 10:
                return candidates

        # Hydration / SSR leftovers.
        for match in _ITEM_PATH.finditer(response.text or ""):
            item_id = match.group("item_id")
            host = urlparse(response.url).netloc or "pt.aliexpress.com"
            absolute = f"https://{host}/item/{item_id}.html"
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            candidates.append(
                SearchCandidate(
                    url=canonical,
                    product_id=item_id,
                    metadata={
                        "source": "aliexpress-search-embedded",
                        "item_id": item_id,
                    },
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    @staticmethod
    def _item_id_from_url(url: str) -> str | None:
        match = _ITEM_PATH.search(urlparse(url).path or "")
        return match.group("item_id") if match else None

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

    @classmethod
    def _candidates_from_search_payload(
        cls, raw: str, page_url: str
    ) -> list[SearchCandidate]:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = cls._search_items(payload)
        host = urlparse(page_url).netloc or "pt.aliexpress.com"
        out: list[SearchCandidate] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            item_id = cls._id_str(
                row.get("productId")
                or row.get("product_id")
                or row.get("itemId")
                or row.get("item_id")
            )
            if not item_id:
                continue
            title = None
            title_block = row.get("title")
            if isinstance(title_block, dict):
                title = title_block.get("displayTitle") or title_block.get("title")
            elif isinstance(title_block, str):
                title = title_block
            title = str(title).strip() if title else None
            url = f"https://{host}/item/{item_id}.html"
            out.append(
                SearchCandidate(
                    url=url,
                    title=title,
                    product_id=item_id,
                    metadata={
                        "source": "aliexpress-search-api",
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
        mods = payload.get("mods")
        if isinstance(mods, dict):
            item_list = mods.get("itemList")
            if isinstance(item_list, dict):
                content = item_list.get("content")
                if isinstance(content, list):
                    return content
        for key in ("items", "products", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, dict):
            return cls._search_items(data)
        return []
