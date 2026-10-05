"""Store Search adapters — candidate discovery independent of PDP spiders."""

from .base import EmptySearchClassification, SearchRequest, StoreSearchAdapter
from .registry import (
    registered_search_store_keys,
    resolve_search_adapter,
    search_adapter_available,
)

__all__ = [
    "EmptySearchClassification",
    "SearchRequest",
    "StoreSearchAdapter",
    "registered_search_store_keys",
    "resolve_search_adapter",
    "search_adapter_available",
]
