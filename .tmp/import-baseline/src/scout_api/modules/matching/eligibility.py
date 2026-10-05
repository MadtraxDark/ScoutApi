"""Product Match store eligibility — Search registry ∩ match_enabled catalog."""

from __future__ import annotations

from scout_api.modules.crawler.stores import match_enabled_store_keys
from scout_api.modules.matching.search_adapters.registry import (
    registered_search_store_keys,
)


def eligible_match_store_keys() -> tuple[str, ...]:
    """Stores allowed in Product Match auto-discovery.

    Intersection of:
    - ``STORE_CONFIGS`` with ``implemented=True`` and ``match_enabled=True``
    - registered ``StoreSearchAdapter`` keys

    Does **not** inspect PDP spiders or ``supports_search``.
    """
    search_ok = set(registered_search_store_keys())
    return tuple(key for key in match_enabled_store_keys() if key in search_ok)
