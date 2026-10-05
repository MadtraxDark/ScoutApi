"""Marketplace configuration for Amazon regional adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AmazonMarketplace:
    """Immutable per-marketplace knobs shared by Amazon parsers."""

    country: str
    currency: str
    host: str
    default_seller: str
    in_stock_markers: tuple[str, ...]
    out_of_stock_markers: tuple[str, ...]
    ships_from_labels: tuple[str, ...]
    sold_by_labels: tuple[str, ...]
    list_price_labels: tuple[str, ...]
    pix_markers: tuple[str, ...] = ()
    installment_markers: tuple[str, ...] = ()
    prime_markers: tuple[str, ...] = ()
    subscribe_markers: tuple[str, ...] = ()
    coupon_markers: tuple[str, ...] = ()


AMAZON_BR = AmazonMarketplace(
    country="BR",
    currency="BRL",
    host="amazon.com.br",
    default_seller="Amazon.com.br",
    in_stock_markers=(
        "em estoque",
        "apenas",
        "restam",
        "pronto para envio",
    ),
    out_of_stock_markers=(
        "não disponível",
        "nao disponivel",
        "indisponível",
        "indisponivel",
        "sem estoque",
        "esgotado",
        "não temos previsão",
        "nao temos previsao",
        "atualmente indisponível",
        "atualmente indisponivel",
        # English interstitial sometimes served on .com.br PDPs.
        "currently unavailable",
        "out of stock",
        "temporarily out of stock",
    ),
    ships_from_labels=("enviado de", "enviado por", "ships from"),
    sold_by_labels=("vendido por", "sold by"),
    list_price_labels=("preço", "de:", "list price", "price"),
    pix_markers=("no pix", "via pix", "com pix"),
    installment_markers=("em até", "em ate", "x de", "parcelas"),
    prime_markers=("prime",),
    subscribe_markers=("assine e economize", "subscribe & save"),
    coupon_markers=("cupom", "coupon"),
)

AMAZON_US = AmazonMarketplace(
    country="US",
    currency="USD",
    host="amazon.com",
    default_seller="Amazon.com",
    in_stock_markers=(
        "in stock",
        "only",
        "left in stock",
        "usually ships",
    ),
    out_of_stock_markers=(
        "currently unavailable",
        "out of stock",
        "temporarily out of stock",
        "we don't know when",
        "we do not know when",
        "unavailable",
        "not available",
    ),
    ships_from_labels=("ships from",),
    sold_by_labels=("sold by",),
    list_price_labels=("list price", "was:", "typical price"),
    pix_markers=(),
    installment_markers=(" /mo", "/month", "monthly payments", "financing"),
    prime_markers=("prime",),
    subscribe_markers=("subscribe & save", "subscribe and save"),
    coupon_markers=("coupon", "apply $", "clip coupon"),
)
