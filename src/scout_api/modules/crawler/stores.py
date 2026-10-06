"""Central catalog: adding a store does not require changing crawler core."""

from dataclasses import dataclass
from typing import Literal

from .core.proxy_policy import ProxyPolicy

ImageFetchCost = Literal["low", "high", "unsupported"]


@dataclass(frozen=True)
class StoreConfig:
    key: str
    country: str
    currency: str
    domains: tuple[str, ...]
    implemented: bool = False
    proxy_policy: ProxyPolicy = ProxyPolicy.FALLBACK
    supports_images: bool = True
    # UX / cost hint for clients (PriceScout checkbox default). Never exposes
    # proxy URLs or bypass details.
    image_fetch_cost: ImageFetchCost = "low"
    # Product Match auto-discovery. When False the store stays crawlable and
    # listed, but is omitted from eligible match targets (not ERROR / NO_MATCH).
    match_enabled: bool = True
    match_disabled_reason: str | None = None
    # User-facing label (never the catalog slug). Catalog dict key stays snake_case.
    display_name: str = ""

    # Language of the store's *search index* for localizable query attributes
    # (color/category labels). STORE COUNTRY ≠ SEARCH LANGUAGE — set explicitly
    # when the catalog indexes English (or another language) despite country=PY.
    # Product identifiers and model phrases are never translated.
    search_locale: str | None = None

    # Valid markets belong to the integration. Empty uses its configured default.
    supported_country_currency_pairs: tuple[tuple[str, str], ...] = ()

    @property
    def market_pairs(self) -> tuple[tuple[str, str], ...]:
        return self.supported_country_currency_pairs or ((self.country, self.currency),)

    @property
    def query_locale(self) -> str:
        """Search-attribute locale: explicit ``search_locale``, else country fallback."""
        if self.search_locale:
            return self.search_locale
        # Last-resort only for stores without integration evidence yet.
        return {"BR": "pt-BR", "US": "en-US", "PY": "es-PY"}.get(
            self.country.upper(), "en"
        )

    @property
    def default_include_images(self) -> bool:
        """True when gallery extraction is cheap enough to opt-in by default."""
        return self.supports_images and self.image_fetch_cost == "low"

    @property
    def label(self) -> str:
        """Friendly store name for UI / SSE; falls back to ``key`` only if unset."""
        return self.display_name or self.key


STORE_CONFIGS = {
    "kabum": StoreConfig(
        "kabum",
        "BR",
        "BRL",
        ("kabum.com.br",),
        True,
        display_name="KaBuM!",
        search_locale="pt-BR",
    ),
    "magazineluiza": StoreConfig(
        "magazineluiza",
        "BR",
        "BRL",
        ("magazineluiza.com.br",),
        True,
        display_name="Magazine Luiza",
        search_locale="pt-BR",
    ),
    "mercadolivre": StoreConfig(
        "mercadolivre",
        "BR",
        "BRL",
        ("mercadolivre.com.br", "produto.mercadolivre.com.br"),
        True,
        proxy_policy=ProxyPolicy.FALLBACK,
        # Temporary: login / soft-auth instability on live SERP+PDP.
        # Re-enable via match_enabled=True when auth wall is stable.
        match_enabled=False,
        match_disabled_reason="login instability",
        display_name="Mercado Livre",
        search_locale="pt-BR",
    ),
    "pichau": StoreConfig(
        "pichau",
        "BR",
        "BRL",
        ("pichau.com.br",),
        True,
        display_name="Pichau",
        search_locale="pt-BR",
    ),
    "terabyteshop": StoreConfig(
        "terabyteshop",
        "BR",
        "BRL",
        ("terabyteshop.com.br",),
        True,
        display_name="TerabyteShop",
        search_locale="pt-BR",
    ),
    "shopee": StoreConfig(
        "shopee",
        "BR",
        "BRL",
        ("shopee.com.br",),
        True,
        proxy_policy=ProxyPolicy.FALLBACK,
        # Gallery URLs come from the already-fetched PDP payload (no CDN
        # download in preview). Default checkbox stays off — PDP itself is
        # browser-heavy and often proxied.
        supports_images=True,
        image_fetch_cost="high",
        # Temporary: login / session-gate instability on live match.
        # Re-enable via match_enabled=True when auth wall is stable.
        match_enabled=False,
        match_disabled_reason="login instability",
        display_name="Shopee",
        search_locale="pt-BR",
    ),
    "aliexpress": StoreConfig(
        "aliexpress",
        "BR",
        "BRL",
        ("aliexpress.com",),
        True,
        proxy_policy=ProxyPolicy.FALLBACK,
        image_fetch_cost="high",
        display_name="AliExpress",
        # BR storefront UI is pt-BR; search terms for localizable attrs follow UI.
        search_locale="pt-BR",
    ),
    "amazon_br": StoreConfig(
        "amazon",
        "BR",
        "BRL",
        ("amazon.com.br",),
        True,
        display_name="Amazon Brasil",
        search_locale="pt-BR",
    ),
    "amazon_us": StoreConfig(
        "amazon",
        "US",
        "USD",
        ("amazon.com",),
        True,
        display_name="Amazon US",
        search_locale="en-US",
    ),
    "bestbuy": StoreConfig(
        "bestbuy",
        "US",
        "USD",
        ("bestbuy.com",),
        True,
        display_name="Best Buy",
        search_locale="en-US",
    ),
    "ebay": StoreConfig(
        "ebay", "US", "USD", ("ebay.com",), display_name="eBay", search_locale="en-US"
    ),
    "gamestop": StoreConfig(
        "gamestop",
        "US",
        "USD",
        ("gamestop.com",),
        display_name="GameStop",
        search_locale="en-US",
    ),
    "newegg": StoreConfig(
        "newegg",
        "US",
        "USD",
        ("newegg.com",),
        display_name="Newegg",
        search_locale="en-US",
    ),
    "microcenter": StoreConfig(
        "microcenter",
        "US",
        "USD",
        ("microcenter.com",),
        display_name="Micro Center",
        search_locale="en-US",
    ),
    "nissei": StoreConfig(
        "nissei",
        "PY",
        "PYG",
        ("nissei.com",),
        True,
        display_name="Nissei",
        # Search path is /br/ (PT UI), but catalog titles/swatches index English
        # color tokens (Black). Country=PY must not force es-PY query attrs.
        search_locale="en-US",
    ),
    "cellshop": StoreConfig(
        "cellshop", "PY", "PYG", ("cellshop.com",), display_name="Cellshop"
    ),
    "stargames": StoreConfig(
        "stargames",
        "PY",
        "PYG",
        ("stargames.com.py",),
        display_name="Star Games",
    ),
    "shoppingchina": StoreConfig(
        "shoppingchina",
        "PY",
        "PYG",
        ("shoppingchina.com.py", "shoppingchina.com.br"),
        True,
        display_name="Shopping China",
        # Catalog titles use English color tokens (BLACK). Live quick_search:
        # `black` recovers the SKU; `negro` returned a wrong sibling.
        search_locale="en-US",
        supported_country_currency_pairs=(("PY", "PYG"), ("PY", "BRL")),
    ),
    "visaovip": StoreConfig(
        "visaovip",
        "PY",
        "USD",
        ("visaovip.com",),
        True,
        display_name="Visão VIP",
        # UI default is pt-BR, but search index/product titles are English-first
        # for electronics; country=PY must not force es-PY color tokens.
        search_locale="en-US",
        supported_country_currency_pairs=(("PY", "USD"),),
    ),
}


def implemented_store_keys() -> tuple[str, ...]:
    """Catalog keys marked ``implemented=True`` (dynamic match / crawl targets)."""
    return tuple(key for key, config in STORE_CONFIGS.items() if config.implemented)


def match_enabled_store_keys() -> tuple[str, ...]:
    """Implemented catalog keys allowed to participate in Product Match."""
    return tuple(
        key
        for key, config in STORE_CONFIGS.items()
        if config.implemented and config.match_enabled
    )


def store_display_name(store_key: str) -> str:
    """User-facing label for a catalog key (never invents from underscore replace)."""
    config = STORE_CONFIGS.get(store_key)
    if config is not None and config.display_name:
        return config.display_name
    return store_key


def resolve_store_config_by_hostname(hostname: str) -> StoreConfig | None:
    """Match a hostname to ``StoreConfig`` via registered domains (no hardcoding)."""
    host = hostname.strip().lower().removeprefix("www.")
    if not host:
        return None
    for config in STORE_CONFIGS.values():
        for domain in config.domains:
            d = domain.lower().removeprefix("www.")
            if host == d or host.endswith("." + d):
                return config
    return None
