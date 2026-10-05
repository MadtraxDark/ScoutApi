from scout_api.modules.crawler.spiders.registry import (
    resolve_spider_by_store_key,
    resolve_store_spider,
)
from scout_api.modules.crawler.stores import STORE_CONFIGS


def eligible_match_store_keys() -> tuple[str, ...]:
    """Lazy re-export — canonical implementation is matching.eligibility."""
    from scout_api.modules.matching.eligibility import (
        eligible_match_store_keys as _eligible,
    )

    return _eligible()


def store_match_disabled_reason(store_key: str) -> str | None:
    config = STORE_CONFIGS.get(store_key)
    if config is None:
        return None
    if config.match_enabled:
        return None
    return config.match_disabled_reason


def stores_supporting_search() -> tuple[str, ...]:
    """Deprecated alias for registered Search adapter keys (lazy import)."""
    from scout_api.modules.matching.search_adapters.registry import (
        registered_search_store_keys,
    )

    return registered_search_store_keys()


__all__ = [
    "resolve_store_spider",
    "resolve_spider_by_store_key",
    "stores_supporting_search",
    "eligible_match_store_keys",
    "store_match_disabled_reason",
]
