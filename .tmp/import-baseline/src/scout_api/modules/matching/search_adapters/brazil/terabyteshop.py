"""TerabyteShop Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote_plus, urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_PRODUCT_ID_RE = re.compile(r"/produto/(\d+)", re.I)


class TerabyteShopSearchAdapter:
    """Candidate discovery for terabyteshop.com.br ``/busca?str=…``."""

    store_key: ClassVar[str] = "terabyteshop"

    def build_search_request(self, query: str) -> SearchRequest:
        return SearchRequest(
            url=(
                "https://www.terabyteshop.com.br/busca?"
                f"str={quote_plus(query.strip())}"
            ),
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for href in response.css(
            "a[href*='/produto/']::attr(href), .product-item a::attr(href)"
        ).getall():
            absolute = urljoin(response.url, (href or "").strip())
            if "/produto/" not in absolute:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            match = _PRODUCT_ID_RE.search(canonical)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    product_id=match.group(1) if match else None,
                    metadata={"source": "terabyte-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"
