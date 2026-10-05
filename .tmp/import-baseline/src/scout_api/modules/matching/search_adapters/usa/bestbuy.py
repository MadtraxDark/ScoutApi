"""Best Buy Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote_plus, urljoin, urlparse

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_LEGACY_SKU_PATH = re.compile(r"/site/[^?]+\.p(?:\?|$)", re.I)
_MODERN_PRODUCT = re.compile(r"/product/[^/]+/([A-Za-z0-9]+)", re.I)
_SKU_QUERY = re.compile(r"(?:^|[?&])skuId=(\d{5,12})(?:&|$)", re.I)


class BestBuySearchAdapter:
    """Candidate discovery for bestbuy.com ``/site/searchpage.jsp``."""

    store_key: ClassVar[str] = "bestbuy"

    def build_search_request(self, query: str) -> SearchRequest:
        q = quote_plus(query.strip())
        return SearchRequest(
            url=f"https://www.bestbuy.com/site/searchpage.jsp?st={q}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for href in response.css(
            "a[href*='/product/']::attr(href), "
            "a[href*='/site/'][href*='.p']::attr(href), "
            "ol.sku-item-list a::attr(href), "
            "li.sku-item a::attr(href)"
        ).getall():
            absolute = urljoin(response.url, href.strip())
            path = urlparse(absolute).path or ""
            if "/searchpage" in path or "/site/search" in path:
                continue
            modern = _MODERN_PRODUCT.search(path)
            legacy = _LEGACY_SKU_PATH.search(absolute)
            sku_q = _SKU_QUERY.search(absolute)
            if not modern and not legacy and not sku_q:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            product_id = None
            title = None
            if modern:
                product_id = modern.group(1)
                slug_match = re.search(
                    r"/product/([^/]+)/" + re.escape(product_id),
                    path,
                    re.I,
                )
                if slug_match:
                    title = slug_match.group(1).replace("-", " ").strip() or None
            elif sku_q:
                product_id = sku_q.group(1)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    product_id=product_id,
                    metadata={"source": "bestbuy-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"
