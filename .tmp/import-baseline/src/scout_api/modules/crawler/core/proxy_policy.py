"""Store-aware proxy routing policy for paid residential egress."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ..stores import StoreConfig


class ProxyPolicy(StrEnum):
    """How a store may use ``CAMOUFOX_PROXY_URL``.

    Paid proxy must be avoided whenever possible.

    ``FALLBACK`` — direct first; proxy only after classified ``UPSTREAM_BLOCKED``.
    ``DIRECT`` — never use the paid proxy.
    ``REQUIRED`` — always use proxy (legacy; prefer FALLBACK for cost control).
    """

    FALLBACK = "fallback"
    DIRECT = "direct"
    REQUIRED = "required"


def resolve_store_config(url: str) -> StoreConfig | None:
    """Map a product URL hostname to ``StoreConfig`` when known."""
    from ..stores import STORE_CONFIGS

    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not hostname:
        return None
    for config in STORE_CONFIGS.values():
        for domain in config.domains:
            normalized = domain.lower().removeprefix("www.")
            if hostname == normalized or hostname.endswith(f".{normalized}"):
                return config
    return None


def proxy_policy_for_url(
    url: str, *, default: ProxyPolicy = ProxyPolicy.FALLBACK
) -> ProxyPolicy:
    """Resolve proxy policy for a URL without scattering store name checks."""
    config = resolve_store_config(url)
    if config is None:
        return default
    return config.proxy_policy
