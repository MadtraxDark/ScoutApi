"""Shopping China Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin, urlsplit

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_PRODUCT_PATH_ID = re.compile(
    r"/(?:produto|producto)/[^/]*?-(\d+)/?$",
    re.IGNORECASE,
)


class ShoppingChinaSearchAdapter:
    """Candidate discovery via Shopping China ``quick_search`` JSON endpoint."""

    store_key: ClassVar[str] = "shoppingchina"

    def build_search_request(self, query: str) -> SearchRequest:
        # Legacy Magento ``/catalogsearch/result`` returns 404. Live storefront
        # exposes a lightweight JSON autocomplete/search endpoint.
        return SearchRequest(
            url=(
                "https://www.shoppingchina.com.py/quick_search?search="
                f"{quote_plus(query.strip())}"
            ),
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        text = (response.text or "").strip()
        if text.startswith("[") or text.startswith("{"):
            return self._parse_quick_search_json(text, response.url)
        return self._parse_search_html(response)

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    @staticmethod
    def _normalize_product_path(url: str) -> str:
        """Map legacy ``/produto/`` → ``/producto/`` on the ``.com.py`` host."""
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        path = parts.path or ""
        if host.endswith("shoppingchina.com.py") and "/produto/" in path.casefold():
            rewritten = re.sub(
                r"/produto/",
                "/producto/",
                path,
                count=1,
                flags=re.IGNORECASE,
            )
            return parts._replace(path=rewritten).geturl()
        return url

    def _parse_quick_search_json(
        self, text: str, page_url: str
    ) -> list[SearchCandidate]:
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows: list[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            raw = payload.get("products") or payload.get("items") or payload.get("data")
            rows = raw if isinstance(raw, list) else []
        else:
            rows = []

        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            href = (
                row.get("url_po")
                or row.get("url_es")
                or row.get("url")
                or row.get("href")
            )
            if not isinstance(href, str) or not href.strip():
                continue
            absolute = self._normalize_product_path(
                urljoin(page_url, href.strip())
            )
            path = urlsplit(absolute).path or ""
            if "/produto/" not in path.lower() and "/producto/" not in path.lower():
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            product_id = None
            match = _PRODUCT_PATH_ID.search(path)
            if match:
                product_id = match.group(1)
            title = row.get("title_po") or row.get("title_es") or row.get("title")
            if title is not None:
                title = str(title).strip() or None
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    product_id=product_id,
                    metadata={"source": "shoppingchina-quick-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def _parse_search_html(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for href in response.css(
            "a[href*='/produto/']::attr(href), "
            "a[href*='/producto/']::attr(href), "
            "a.product-item-link::attr(href), "
            "li.product-item a::attr(href)"
        ).getall():
            absolute = self._normalize_product_path(
                urljoin(response.url, href.strip())
            )
            path = urlsplit(absolute).path or ""
            if "catalogsearch" in path.lower() or "/site/search" in path.lower():
                continue
            if "/produto/" not in path.lower() and "/producto/" not in path.lower():
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            product_id = None
            match = _PRODUCT_PATH_ID.search(path)
            if match:
                product_id = match.group(1)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    product_id=product_id,
                    metadata={"source": "shoppingchina-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates
