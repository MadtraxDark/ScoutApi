"""Independent registry of Store Search adapters (not PDP spiders)."""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from scout_api.modules.crawler.core.exceptions import RequestError

if TYPE_CHECKING:
    from .base import StoreSearchAdapter

_ADAPTERS: dict[str, type] | None = None


def _discover_adapters() -> dict[str, type]:
    """Walk search_adapters subpackages for classes with store_key + Protocol shape."""
    from . import __name__ as package_name
    from . import __path__ as package_path
    from .base import StoreSearchAdapter

    found: dict[str, type] = {}
    for module_info in pkgutil.walk_packages(package_path, f"{package_name}."):
        if module_info.name.endswith(".base") or module_info.name.endswith(".registry"):
            continue
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            store_key = getattr(value, "store_key", None)
            if not isinstance(store_key, str) or not store_key:
                continue
            # Concrete adapters expose the three search methods.
            if not callable(getattr(value, "build_search_request", None)):
                continue
            if not callable(getattr(value, "parse_candidates", None)):
                continue
            if not callable(getattr(value, "classify_empty_result", None)):
                continue
            # Skip Protocol itself if ever imported as a class.
            if value is StoreSearchAdapter:
                continue
            found[store_key] = value
    return found


def _adapters() -> dict[str, type]:
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = _discover_adapters()
    return _ADAPTERS


def reset_search_adapter_registry() -> None:
    """Clear cached discovery (tests)."""
    global _ADAPTERS
    _ADAPTERS = None


def registered_search_store_keys() -> tuple[str, ...]:
    """Catalog keys with a registered StoreSearchAdapter."""
    return tuple(sorted(_adapters()))


def search_adapter_available(store_key: str) -> bool:
    return store_key.strip().lower() in _adapters()


def resolve_search_adapter(store_key: str) -> StoreSearchAdapter:
    """Instantiate the Search adapter for ``store_key``."""
    key = store_key.strip().lower()
    cls = _adapters().get(key)
    if cls is None:
        raise RequestError(
            f"Busca não suportada para a loja {store_key}",
            code="SEARCH_UNSUPPORTED",
            url=None,
        )
    return cls()  # type: ignore[return-value]
