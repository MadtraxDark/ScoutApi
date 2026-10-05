"""Magazine Luiza Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import quote_plus, urljoin, urlparse

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import (
    canonicalize_url,
    title_hint_from_url,
)
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate


class MagazineLuizaSearchAdapter:
    """Candidate discovery for magazineluiza.com.br ``/busca/{query}/``."""

    store_key: ClassVar[str] = "magazineluiza"

    def build_search_request(self, query: str) -> SearchRequest:
        q = quote_plus(query.strip())
        return SearchRequest(
            url=f"https://www.magazineluiza.com.br/busca/{q}/",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for href in response.css(
            "a[data-testid='product-card-link']::attr(href), a[href*='/p/']::attr(href)"
        ).getall():
            absolute = urljoin(response.url, href.strip())
            if "/p/" not in absolute:
                continue
            path = urlparse(absolute).path or ""
            if "/busca/" in path:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            product_id = None
            match = re.search(r"/p/([^/?]+)", path)
            if match:
                product_id = match.group(1)
            title = title_hint_from_url(absolute)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    product_id=product_id,
                    metadata={"source": "magalu-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"
