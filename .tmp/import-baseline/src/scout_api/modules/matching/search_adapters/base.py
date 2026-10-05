"""Store Search port — candidate discovery only (not PDP scraping)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, runtime_checkable

from scrapy.http import Response

from scout_api.modules.matching.search_candidate import SearchCandidate

EmptySearchClassification = Literal["genuine_empty", "incomplete", "unknown"]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Minimal fetchable search request (GET URL today; extensible later)."""

    url: str
    method: str = "GET"
    prefer_browser: bool = False


@runtime_checkable
class StoreSearchAdapter(Protocol):
    """Independent capability: discover PDP candidates from a store SERP/API."""

    store_key: ClassVar[str]

    def build_search_request(self, query: str) -> SearchRequest:
        """Build the request used to fetch search results."""
        ...

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        """Parse SERP/API response into search candidates."""
        ...

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        """Distinguish genuine zero hits from incomplete/blocked shells."""
        ...
