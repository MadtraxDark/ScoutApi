"""Amazon Brazil Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote_plus

from scrapy.http import Response

from scout_api.modules.matching.search_adapters.amazon.parse import (
    classify_amazon_empty_result,
    parse_amazon_search_results,
)
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate


class AmazonBrazilSearchAdapter:
    """Candidate discovery for amazon.com.br ``/s?k=…``."""

    store_key: ClassVar[str] = "amazon_br"

    def build_search_request(self, query: str) -> SearchRequest:
        return SearchRequest(
            url=f"https://www.amazon.com.br/s?k={quote_plus(query.strip())}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        return parse_amazon_search_results(
            response,
            host="amazon.com.br",
            source="amazon-br-search",
        )

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return classify_amazon_empty_result(response)
