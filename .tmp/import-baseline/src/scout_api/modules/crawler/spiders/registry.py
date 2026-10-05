"""Discover store spiders from ``allowed_domains`` without hardcoding imports."""

from __future__ import annotations

import importlib
import pkgutil
from urllib.parse import urlparse

from ..core.exceptions import RequestError
from ..stores import STORE_CONFIGS
from . import __name__ as spiders_package_name
from . import __path__ as spiders_package_path
from .base import BaseStoreSpider


def _spider_classes() -> tuple[type[BaseStoreSpider], ...]:
    classes: list[type[BaseStoreSpider]] = []
    seen: set[type[BaseStoreSpider]] = set()
    for module_info in pkgutil.walk_packages(
        spiders_package_path, f"{spiders_package_name}."
    ):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseStoreSpider)
                and value is not BaseStoreSpider
                and value not in seen
                and getattr(value, "allowed_domains", None)
            ):
                seen.add(value)
                classes.append(value)
    return tuple(classes)


def _hostname_matches(hostname: str, domain: str) -> bool:
    normalized = domain.lower().removeprefix("www.")
    return hostname == normalized or hostname.endswith(f".{normalized}")


def resolve_store_spider(url: str) -> BaseStoreSpider:
    """Map a product URL hostname to the store spider adapter."""
    hostname = (urlparse(url).hostname or "").lower()
    for spider_cls in _spider_classes():
        if any(
            _hostname_matches(hostname, domain) for domain in spider_cls.allowed_domains
        ):
            return spider_cls()
    raise RequestError(
        "Nenhum spider disponível para este domínio",
        code="UNSUPPORTED_STORE",
        url=url,
    )


def resolve_spider_by_store_key(store_key: str) -> BaseStoreSpider:
    """Resolve a spider by catalog key (e.g. ``amazon_br``, ``kabum``)."""
    key = store_key.strip().lower()
    config = STORE_CONFIGS.get(key)
    if config is None or not config.implemented:
        raise RequestError(
            f"Loja não suportada: {store_key}",
            code="UNSUPPORTED_STORE",
            url=None,
        )

    for spider_cls in _spider_classes():
        if getattr(spider_cls, "name", None) == key:
            return spider_cls()

    primary_domain = config.domains[0].lower()
    for spider_cls in _spider_classes():
        if spider_cls.store != config.key:
            continue
        if any(
            _hostname_matches(primary_domain, domain)
            or domain.lower().removeprefix("www.") == primary_domain
            for domain in spider_cls.allowed_domains
        ):
            return spider_cls()
        if spider_cls.country == config.country:
            return spider_cls()

    raise RequestError(
        f"Nenhum spider disponível para a loja {store_key}",
        code="UNSUPPORTED_STORE",
        url=None,
    )
