from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Availability = Literal["available", "out_of_stock", "unavailable"]

# OpenAPI /docs: Pydantic serializes Decimal as string + unbounded pattern;
# Swagger invents absurd examples from that pattern unless examples are explicit.
_METADATA_EXAMPLE: dict[str, Any] = {
    "source": {
        "price": "structured-data",
        "original_price": "list-price",
        "pix_price": "payment-method-pix",
    }
}

_PRODUCT_OFFER_EXAMPLE: dict[str, Any] = {
    "store": "magazineluiza",
    "country": "BR",
    "product_id": "238803400",
    "sku": "238803400",
    "seller": "Magazine Luiza",
    "url": "https://www.magazineluiza.com.br/apple-iphone-16-128gb/p/238803400",
    "canonical_url": "https://www.magazineluiza.com.br/apple-iphone-16-128gb/p/238803400",
    "currency": "BRL",
    "price": "4799.00",
    "original_price": "5299.00",
    "discount_percentage": "9.44",
    "pix_price": "4559.05",
    "installment_price": "479.90",
    "installment_count": 10,
    "availability": "available",
    "available": True,
    "scraped_at": "2026-03-19T15:30:00Z",
    "metadata": _METADATA_EXAMPLE,
}

_PRODUCT_PRICE_ITEM_EXAMPLE: dict[str, Any] = {
    **_PRODUCT_OFFER_EXAMPLE,
    "gtin": "0195949821482",
    "title": "Apple iPhone 16 128GB Preto",
    "brand": "Apple",
    "model": "iPhone 16",
    "variant": "128 GB Preto",
    "shipping_price": "0.00",
    "images": [],
    "image_candidates": [],
    "last_changed_at": None,
}


class ImageCandidate(BaseModel):
    """External gallery candidate from crawl — not a catalog ProductImage."""

    source_url: str = Field(description="URL externa da imagem candidata.")
    position: int = Field(ge=0, description="Ordem sugerida na galeria.")
    alt: str | None = Field(
        default=None, description="Texto alternativo quando houver."
    )
    width: int | None = Field(default=None, description="Largura quando conhecida.")
    height: int | None = Field(default=None, description="Altura quando conhecida.")


def candidates_from_urls(urls: list[str]) -> list[ImageCandidate]:
    """Build positional candidates from spider URL lists (no Drive persist)."""
    return [
        ImageCandidate(source_url=url, position=index)
        for index, url in enumerate(urls)
        if url
    ]


class ProductOffer(BaseModel):
    """Instantâneo comercial para checagem leve de preço/disponibilidade."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={"examples": [_PRODUCT_OFFER_EXAMPLE]},
    )

    store: str = Field(description="Identificador da loja.")
    country: str = Field(description="Código do país da oferta.")
    product_id: str = Field(description="Identificador do produto na loja.")
    sku: str | None = Field(default=None, description="SKU na loja, quando disponível.")
    seller: str | None = Field(default=None, description="Nome do vendedor da oferta.")
    url: str = Field(description="URL da página da oferta.")
    canonical_url: str = Field(description="URL canônica da oferta.")
    currency: str = Field(description="Moeda ISO da oferta (ex.: BRL, USD).")
    price: Decimal = Field(
        gt=0,
        description="Preço atual da oferta.",
        examples=["4799.00"],
    )
    original_price: Decimal | None = Field(
        default=None,
        description="Preço original antes do desconto, quando disponível.",
        examples=["5299.00"],
    )
    discount_percentage: Decimal | None = Field(
        default=None,
        description="Percentual de desconto em relação ao preço original.",
        examples=["9.44"],
    )
    pix_price: Decimal | None = Field(
        default=None,
        description="Preço no Pix quando a loja o expõe; null se inexistente.",
        examples=["4559.05"],
    )
    installment_price: Decimal | None = Field(
        default=None,
        description="Valor de cada parcela, quando disponível.",
        examples=["479.90"],
    )
    installment_count: int | None = Field(
        default=None,
        description="Número de parcelas, quando disponível.",
        examples=[10],
    )
    availability: Availability = Field(
        default="available",
        description="Disponibilidade normalizada da oferta.",
    )
    available: bool = Field(
        default=True,
        description="Indicação booleana de disponibilidade.",
    )
    scraped_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Momento em que a oferta foi coletada.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadados auxiliares (origens de campos, flags de coleta).",
        examples=[_METADATA_EXAMPLE],
    )


class ProductDetails(BaseModel):
    """Identidade de catálogo e campos descritivos do scrape completo."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    product_id: str = Field(description="Identificador do produto na loja.")
    sku: str | None = Field(default=None, description="SKU na loja, quando disponível.")
    gtin: str | None = Field(default=None, description="GTIN/EAN/UPC quando conhecido.")
    title: str = Field(description="Título do produto.")
    brand: str | None = Field(default=None, description="Marca.")
    model: str | None = Field(
        default=None,
        description=(
            "Modelo base pesquisável (família/chip), sem a implementação comercial."
        ),
    )
    variant: str | None = Field(
        default=None,
        description=(
            "Refinamento opcional (edição, cor, capacidade). Ausente não é conflito."
        ),
    )
    description: str | None = Field(
        default=None, description="Descrição textual do produto."
    )
    specifications: dict[str, Any] = Field(
        default_factory=dict,
        description="Especificações estruturadas quando disponíveis.",
    )
    images: list[str] = Field(
        default_factory=list, description="URLs de imagens do produto."
    )
    image_candidates: list[ImageCandidate] = Field(
        default_factory=list,
        description=(
            "Candidatas externas para revisão no PriceScout "
            "(ainda não persistem no Drive)."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadados auxiliares (origens de campos, flags de coleta).",
        examples=[_METADATA_EXAMPLE],
    )


class ProductPriceItem(BaseModel):
    """Item completo normalizado; valores monetários são sempre Decimal."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={"examples": [_PRODUCT_PRICE_ITEM_EXAMPLE]},
    )
    store: str = Field(description="Identificador da loja.")
    country: str = Field(description="Código do país da oferta.")
    product_id: str = Field(description="Identificador do produto na loja.")
    sku: str | None = Field(default=None, description="SKU na loja, quando disponível.")
    gtin: str | None = Field(default=None, description="GTIN/EAN/UPC quando conhecido.")
    title: str = Field(description="Título do produto.")
    brand: str | None = Field(default=None, description="Marca.")
    model: str | None = Field(
        default=None,
        description=(
            "Modelo base pesquisável (família/chip), sem a implementação comercial."
        ),
    )
    variant: str | None = Field(
        default=None,
        description=(
            "Refinamento opcional (edição, cor, capacidade). Ausente não é conflito."
        ),
    )
    seller: str | None = Field(default=None, description="Nome do vendedor da oferta.")
    url: str = Field(description="URL da página da oferta.")
    canonical_url: str = Field(description="URL canônica da oferta.")
    currency: str = Field(description="Moeda ISO da oferta (ex.: BRL, USD).")
    price: Decimal = Field(
        gt=0,
        description="Preço atual da oferta.",
        examples=["4799.00"],
    )
    original_price: Decimal | None = Field(
        default=None,
        description="Preço original antes do desconto, quando disponível.",
        examples=["5299.00"],
    )
    discount_percentage: Decimal | None = Field(
        default=None,
        description="Percentual de desconto em relação ao preço original.",
        examples=["9.44"],
    )
    pix_price: Decimal | None = Field(
        default=None,
        description="Preço no Pix quando a loja o expõe; null se inexistente.",
        examples=["4559.05"],
    )
    availability: Availability = Field(
        default="available",
        description="Disponibilidade normalizada da oferta.",
    )
    installment_price: Decimal | None = Field(
        default=None,
        description="Valor de cada parcela, quando disponível.",
        examples=["479.90"],
    )
    installment_count: int | None = Field(
        default=None,
        description="Número de parcelas, quando disponível.",
        examples=[10],
    )
    shipping_price: Decimal | None = Field(
        default=None,
        description="Preço do frete quando disponível; pode ser 0.00.",
        examples=["0.00"],
    )
    available: bool = Field(
        default=True,
        description="Indicação booleana de disponibilidade.",
    )
    images: list[str] = Field(
        default_factory=list, description="URLs de imagens do produto."
    )
    image_candidates: list[ImageCandidate] = Field(
        default_factory=list,
        description=(
            "Candidatas externas para revisão no PriceScout "
            "(ainda não persistem no Drive)."
        ),
    )
    scraped_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Momento em que a oferta foi coletada.",
    )
    last_changed_at: datetime | None = Field(
        default=None,
        description="Última mudança relevante detectada no histórico da oferta.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadados auxiliares (origens de campos, flags de coleta).",
        examples=[_METADATA_EXAMPLE],
    )


def compose_product_price_item(
    offer: ProductOffer,
    details: ProductDetails,
    *,
    shipping_price: Decimal | None = None,
    last_changed_at: datetime | None = None,
) -> ProductPriceItem:
    """Merge commercial offer and catalog details into the public full item."""
    offer_meta = offer.metadata if isinstance(offer.metadata, dict) else {}
    details_meta = details.metadata if isinstance(details.metadata, dict) else {}
    merged_source: dict[str, Any] = {}
    offer_source = offer_meta.get("source")
    details_source = details_meta.get("source")
    if isinstance(offer_source, dict):
        merged_source.update(offer_source)
    if isinstance(details_source, dict):
        merged_source.update(details_source)

    metadata = {
        **details_meta,
        **offer_meta,
        "source": merged_source
        or offer_meta.get("source")
        or details_meta.get("source"),
    }
    # Matching identity reads structured specs from metadata when present.
    if details.specifications and "specifications" not in metadata:
        metadata["specifications"] = dict(details.specifications)
    return ProductPriceItem(
        store=offer.store,
        country=offer.country,
        product_id=offer.product_id or details.product_id,
        sku=offer.sku or details.sku,
        gtin=details.gtin,
        title=details.title,
        brand=details.brand,
        model=details.model,
        variant=details.variant,
        seller=offer.seller,
        url=offer.url,
        canonical_url=offer.canonical_url,
        currency=offer.currency,
        price=offer.price,
        original_price=offer.original_price,
        discount_percentage=offer.discount_percentage,
        pix_price=offer.pix_price,
        installment_price=offer.installment_price,
        installment_count=offer.installment_count,
        shipping_price=shipping_price,
        available=offer.available,
        availability=offer.availability,
        images=list(details.images),
        image_candidates=(
            list(details.image_candidates)
            if details.image_candidates
            else candidates_from_urls(list(details.images))
        ),
        scraped_at=offer.scraped_at,
        last_changed_at=last_changed_at,
        metadata=metadata,
    )


def product_offer_from_price_item(item: ProductPriceItem) -> ProductOffer:
    """Project a cached full item into the lightweight offer contract."""
    return ProductOffer(
        store=item.store,
        country=item.country,
        product_id=item.product_id,
        sku=item.sku,
        seller=item.seller,
        url=item.url,
        canonical_url=item.canonical_url,
        currency=item.currency,
        price=item.price,
        original_price=item.original_price,
        discount_percentage=item.discount_percentage,
        pix_price=item.pix_price,
        installment_price=item.installment_price,
        installment_count=item.installment_count,
        availability=item.availability,
        available=item.available,
        scraped_at=item.scraped_at,
        metadata=item.metadata,
    )
