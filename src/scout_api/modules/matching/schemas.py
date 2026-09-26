"""Pydantic schemas and search candidate DTOs for product matching."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from scout_api.core.http_url import AbsoluteHttpUrl
from scout_api.modules.crawler.models.product import ProductOffer, ProductPriceItem
from scout_api.modules.images.schemas import ApprovedImageInput, ProductImageView
from scout_api.modules.matching.search_candidate import SearchCandidate

MatchDecision = Literal["auto_match", "review", "reject"]
ListingStatus = Literal["active", "removed", "review"]
OfferEventType = Literal[
    "offer_created",
    "unchanged",
    "price_changed",
    "seller_changed",
    "availability_changed",
    "offer_removed",
    "out_of_stock",
    "new_offer",
    "scrape_failed",
    "gtin_learned",
    "promotion_activated",
    "promotion_expired",
    "promotion_updated",
]
RefreshStatus = Literal[
    "unchanged",
    "changed",
    "removed",
    "out_of_stock",
    "scrape_failed",
    "new_offer",
]

__all__ = [
    "SearchCandidate",
    "MatchDecision",
    "ListingStatus",
    "OfferEventType",
    "RefreshStatus",
    "MatchReason",
    "MatchRequest",
    "MatchHit",
    "MatchStoreError",
    "MatchResponse",
    "MatchProgressEvent",
    "OfferRefreshRequest",
    "OfferSnapshotView",
    "OfferEventView",
    "OfferRefreshResult",
    "OfferRefreshResponse",
    "ProductRegisterRequest",
    "ProductUpdateRequest",
    "ProductListingView",
    "ProductView",
    "ProductRegisterResponse",
    "ProductSearchResponse",
    "ProductListResponse",
    "StoreInfo",
    "StoreListResponse",
    "ApprovedImageInput",
    "ProductImageView",
    "MatchRunStatus",
    "MatchStoreRunStatus",
    "MatchRunStatusView",
    "MatchRunListResponse",
    "MatchCandidateLogView",
    "MatchStoreRunView",
    "MatchRunDetailView",
    "NotificationView",
    "NotificationListResponse",
    "UnreadCountResponse",
]


class MatchReason(BaseModel):
    code: str
    detail: str
    score: float | None = None


class MatchRequest(BaseModel):
    reference_url: AbsoluteHttpUrl = Field(
        description="URL do produto de referência usado como base do matching."
    )
    canonical_product_id: UUID | None = Field(
        default=None,
        description=(
            "Quando informado, persiste matches neste produto canônico "
            "existente (evita reparentar listings e criar duplicata)."
        ),
    )
    stores: list[str] | None = Field(
        default=None,
        description="Lista de lojas a consultar; se omitida, usa o conjunto padrão.",
    )
    include_review: bool = Field(
        default=False,
        description="Quando true, inclui candidatas classificadas como review.",
    )
    persist: bool = Field(
        default=True,
        description="Quando true, persiste produto canônico e listings no banco.",
    )
    include_images: bool = Field(
        default=False,
        description="Quando true, inclui galeria de imagens no scraping de referência.",
    )
    max_candidates_per_store: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Máximo de candidatos avaliados por loja.",
    )


class MatchHit(BaseModel):
    store: str
    store_display_name: str | None = Field(
        default=None,
        description="Nome amigável da loja para UI (ex.: Amazon Brasil).",
    )
    country: str
    listing_id: UUID | None = None
    decision: MatchDecision
    confidence: Decimal = Field(
        description="Confiança do match entre 0 e 1.",
        examples=["0.9700"],
    )
    reasons: list[MatchReason] = Field(default_factory=list)
    product: ProductPriceItem
    search_query: str | None = None


class MatchStoreError(BaseModel):
    store: str
    store_display_name: str | None = Field(
        default=None,
        description="Nome amigável da loja para UI.",
    )
    code: str
    message: str


class MatchResponse(BaseModel):
    canonical_product_id: UUID | None = None
    reference: ProductPriceItem
    matches: list[MatchHit] = Field(default_factory=list)
    unmatched_stores: list[str] = Field(default_factory=list)
    errors: list[MatchStoreError] = Field(default_factory=list)
    discovered_gtin: str | None = None
    gtin_source: str | None = None
    """``reference`` or ``auto_match:<store>`` when a trusted GTIN was learned."""


class MatchProgressEvent(BaseModel):
    """Evento de progresso interno do Product Match (não é mais SSE público)."""

    type: str = Field(
        description=(
            "store_started | searching | candidates_found | scraping_candidate | "
            "matched | no_match | error | completed | search_progress"
        )
    )
    store: str | None = None
    display_name: str | None = None
    stage: str = ""
    status: Literal[
        "pending", "running", "success", "warning", "error", "no_result"
    ] = "running"
    message: str = ""
    candidate_count: int | None = None
    sequence: int | None = None
    result: MatchResponse | None = None


class OfferRefreshRequest(BaseModel):
    canonical_product_id: UUID | None = Field(
        default=None,
        description="Produto canônico cujas ofertas devem ser atualizadas.",
    )
    listing_ids: list[UUID] | None = Field(
        default=None,
        description="Listings específicos a atualizar.",
    )
    urls: list[AbsoluteHttpUrl] | None = Field(
        default=None,
        description="URLs avulsas de oferta a atualizar.",
    )
    include_details: bool = Field(
        default=False,
        description="Quando true, inclui snapshots e eventos detalhados na resposta.",
    )


class OfferSnapshotView(BaseModel):
    price: Decimal | None = Field(
        default=None,
        description="Preço registrado no snapshot (moeda original da loja).",
        examples=["4799.00"],
    )
    currency: str | None = Field(default=None, description="Moeda ISO do snapshot.")
    seller: str | None = Field(default=None, description="Vendedor no snapshot.")
    availability: str | None = Field(
        default=None, description="Disponibilidade normalizada no snapshot."
    )
    available: bool | None = Field(
        default=None, description="Indicação booleana de disponibilidade."
    )
    scraped_at: datetime | None = Field(
        default=None, description="Momento da coleta do snapshot."
    )
    fingerprint: str | None = Field(
        default=None, description="Fingerprint do estado comercial."
    )
    # Pure FX conversion (ADR 0034) — never overwrites price/currency.
    converted_price_brl: Decimal | None = Field(
        default=None,
        description=(
            "Valor de referência em BRL = price × cotação. "
            "Sem IOF/impostos/frete. Null se câmbio indisponível."
        ),
        examples=["5300.00"],
    )
    exchange_rate: Decimal | None = Field(
        default=None,
        description="Cotação usada na conversão.",
        examples=["5.3034"],
    )
    exchange_rate_type: str | None = Field(
        default=None,
        description="Tipo da taxa (tourism_sell, official…).",
    )
    exchange_rate_status: str | None = Field(
        default=None,
        description="fresh | stale | unavailable.",
    )
    exchange_rate_source: str | None = None
    exchange_rate_updated_at: datetime | None = None


class OfferEventView(BaseModel):
    event_type: OfferEventType
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detected_at: datetime


class OfferRefreshResult(BaseModel):
    listing_id: UUID | None = None
    store: str | None = None
    url: str
    status: RefreshStatus
    events: list[OfferEventView] = Field(default_factory=list)
    previous: OfferSnapshotView | None = None
    current: OfferSnapshotView | None = None
    offer: ProductOffer | None = None
    error: str | None = None


class OfferRefreshResponse(BaseModel):
    results: list[OfferRefreshResult] = Field(default_factory=list)


class ProductRegisterRequest(BaseModel):
    """Cadastro de produto canônico, com listing de loja opcional.

    Deduplica por GTIN, identificadores de loja, SKU ou URL canônica.
    Produtos/listings existentes são reutilizados sem sobrescrita silenciosa.
    """

    model_config = {"extra": "forbid"}

    title: str = Field(
        min_length=1,
        max_length=512,
        description="Título do produto canônico.",
    )
    brand: str | None = Field(default=None, max_length=128, description="Marca.")
    model: str | None = Field(default=None, max_length=128, description="Modelo.")
    variant: str | None = Field(
        default=None,
        max_length=256,
        description="Variante legível (cor, capacidade, etc.).",
    )
    variant_key: str | None = Field(
        default=None,
        max_length=256,
        description="Chave normalizada da variante para deduplicação.",
    )
    gtin: str | None = Field(
        default=None, max_length=32, description="GTIN/EAN/UPC quando conhecido."
    )
    attributes: dict[str, Any] | None = Field(
        default=None, description="Atributos adicionais do produto."
    )
    store: str | None = Field(
        default=None, max_length=64, description="Loja do listing opcional."
    )
    country: str | None = Field(
        default=None, max_length=8, description="País do listing (código curto)."
    )
    product_id: str | None = Field(
        default=None,
        max_length=128,
        description="Identificador do produto na loja.",
    )
    sku: str | None = Field(
        default=None, max_length=128, description="SKU na loja, quando disponível."
    )
    url: AbsoluteHttpUrl | None = Field(
        default=None, description="URL da página do produto na loja."
    )
    canonical_url: AbsoluteHttpUrl | None = Field(
        default=None, description="URL canônica do listing na loja."
    )
    price: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Preço atual observado no preview/crawl. "
            "Quando informado com listing, cria o OfferSnapshot inicial."
        ),
        examples=["4799.00"],
    )
    pix_price: Decimal | None = Field(
        default=None,
        gt=0,
        description="Preço no Pix observado no preview, quando houver.",
        examples=["4559.05"],
    )
    original_price: Decimal | None = Field(
        default=None,
        gt=0,
        description="Preço original/list price observado no preview.",
        examples=["5299.00"],
    )
    currency: str | None = Field(
        default=None,
        max_length=8,
        description="Moeda ISO do preço (ex.: BRL). Default BRL se houver preço.",
    )
    seller: str | None = Field(
        default=None,
        max_length=256,
        description="Vendedor observado no preview.",
    )
    available: bool | None = Field(
        default=None,
        description="Disponibilidade booleana observada no preview.",
    )
    images: list[ApprovedImageInput] = Field(
        default_factory=list,
        description=(
            "Imagens aprovadas na revisão (URLs externas). "
            "Persistidas somente após este cadastro — nunca no crawl."
        ),
    )


class ProductListingView(BaseModel):
    id: UUID
    store: str
    country: str
    product_id: str
    sku: str | None = None
    gtin: str | None = None
    url: str
    canonical_url: str
    status: str
    title: str | None = None
    match_decision: str
    confidence: Decimal = Field(
        description="Confiança do match entre 0 e 1.",
        examples=["0.9700"],
    )
    created_at: datetime
    updated_at: datetime
    # Monitoring / commercial snapshot for PriceScout (ADR 0030).
    monitoring_enabled: bool = True
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    last_successful_check_at: datetime | None = None
    consecutive_failures: int = 0
    price: Decimal | None = Field(
        default=None,
        description="Último preço persistido (snapshot).",
        examples=["4799.00"],
    )
    currency: str | None = None
    seller: str | None = None
    availability: str | None = None
    available: bool | None = None
    pix_price: Decimal | None = Field(default=None, examples=["4559.05"])
    original_price: Decimal | None = Field(default=None, examples=["5299.00"])
    promotion_status: str = "none"
    promotion_expires_at: datetime | None = None
    promotion_type: str | None = None
    promotion_price: Decimal | None = None
    promotion_conditions: dict[str, Any] = Field(default_factory=dict)
    promotion_commercially_active: bool = False
    # Pure FX conversion (ADR 0034) — never overwrites price/currency.
    converted_price_brl: Decimal | None = Field(
        default=None,
        description=(
            "Valor de referência em BRL = price × cotação (sem impostos/tarifas)."
        ),
        examples=["5300.00"],
    )
    exchange_rate: Decimal | None = Field(
        default=None,
        description="Cotação usada (1 unidade da moeda original = N BRL).",
        examples=["5.3034"],
    )
    exchange_rate_type: str | None = None
    exchange_rate_status: str | None = Field(
        default=None,
        description="fresh | stale | unavailable.",
    )
    exchange_rate_source: str | None = None
    exchange_rate_updated_at: datetime | None = None


class ProductView(BaseModel):
    id: UUID
    title: str
    brand: str | None = None
    model: str | None = None
    variant_key: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    gtins: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    listings: list[ProductListingView] = Field(default_factory=list)
    images: list[ProductImageView] = Field(default_factory=list)
    primary_image_url: str | None = Field(
        default=None,
        description=(
            "URL de capa para cards: AVIF se ready, senão original. "
            "Prefira este campo na listagem; não espere optimized_status."
        ),
    )


class ProductUpdateRequest(BaseModel):
    """Atualização explícita de campos editáveis do produto canônico."""

    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, min_length=1, max_length=512)
    brand: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    variant: str | None = Field(default=None, max_length=256)
    attributes: dict[str, Any] | None = Field(
        default=None,
        description="Atributos a mesclar (não substitui o mapa inteiro).",
    )


class ProductRegisterResponse(BaseModel):
    created: bool
    listing_created: bool = False
    product: ProductView
    listing: ProductListingView | None = None


class ProductSearchResponse(BaseModel):
    items: list[ProductView] = Field(default_factory=list)
    count: int = Field(description="Número de produtos retornados.")


class ProductListResponse(BaseModel):
    items: list[ProductView] = Field(default_factory=list)
    count: int = Field(description="Número de itens nesta página.")
    limit: int
    offset: int
    total: int | None = Field(
        default=None,
        description="Total visível ao usuário quando calculado.",
    )


class StoreInfo(BaseModel):
    key: str
    display_name: str = Field(
        description="Nome amigável para UI (ex.: Amazon Brasil). Não usar key na UI."
    )
    country: str
    currency: str
    supported_country_currency_pairs: list[tuple[str, str]] = Field(
        description="Pares país/moeda declarados pela integração cadastrada."
    )
    domains: list[str]
    implemented: bool
    supports_search: bool
    supports_images: bool
    match_enabled: bool = Field(
        default=True,
        description=(
            "Quando false, a loja fica fora do Product Match automático "
            "(ainda pode aparecer em GET /stores e no crawl manual)."
        ),
    )
    match_disabled_reason: str | None = Field(
        default=None,
        description=(
            "Motivo legível quando match_enabled=false (ex.: login instability)."
        ),
    )
    image_fetch_cost: Literal["low", "high", "unsupported"] = Field(
        default="low",
        description=(
            "Custo relativo de extrair URLs da galeria (não baixa binários). "
            "Usado pelo frontend para default do checkbox include_images."
        ),
    )
    default_include_images: bool = Field(
        default=True,
        description=(
            "Sugestão de default para include_images no preview "
            "(true quando supports_images e image_fetch_cost=low)."
        ),
    )
    logo_svg: str | None = None
    logo_url: str | None = None
    logo_mime_type: str | None = None
    logo_processing_status: Literal["ready", "pending", "processing", "failed"] = (
        "ready"
    )


class StoreMetadataUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    display_name: str = Field(min_length=1, max_length=160)
    logo_svg: str | None = Field(default=None, max_length=2_097_152)


class StoreMetadataUpdateResponse(BaseModel):
    store: StoreInfo


class StoreListResponse(BaseModel):
    stores: list[StoreInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistent Match Run API (ADR 0036) — polling, not SSE.
# ---------------------------------------------------------------------------

MatchRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
MatchStoreRunStatus = Literal["pending", "running", "match", "no_match", "error"]


class MatchRunStatusView(BaseModel):
    """Compact payload for polling — no candidates/logs."""

    id: UUID
    product_id: UUID
    status: MatchRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    last_activity_at: datetime
    claimed_at: datetime | None = None
    active_since: datetime | None = Field(
        default=None,
        description=(
            "Início da attempt ativa (claimed_at) ou started_at — "
            "base do timer UX; não inclui downtime offline."
        ),
    )
    attempts: int = 0
    total_duration_ms: int | None = None
    stores_total: int = 0
    stores_completed: int = 0
    matches_found: int = 0
    no_matches: int = 0
    errors: int = 0
    failure_code: str | None = None
    failure_message: str | None = None
    already_active: bool = Field(
        default=False,
        description="True quando POST reutilizou uma Run ativa existente.",
    )


class MatchRunListResponse(BaseModel):
    items: list[MatchRunStatusView] = Field(default_factory=list)


class MatchCandidateLogView(BaseModel):
    sequence: int = 0
    title: str | None = None
    url: str | None = None
    store_product_id: str | None = None
    decision: str | None = None
    confidence: Decimal | None = None
    reasons: list[str] = Field(default_factory=list)
    embedding_evidence: dict[str, Any] | None = None
    duration_ms: int | None = None


class MatchStoreRunView(BaseModel):
    id: UUID
    store: str
    store_display_name: str | None = None
    status: MatchStoreRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    queries: list[str] = Field(default_factory=list)
    queries_count: int = 0
    candidates_found: int = 0
    candidates_evaluated: int = 0
    matched_url: str | None = None
    matched_title: str | None = None
    matched_price: Decimal | None = None
    matched_currency: str | None = None
    matched_confidence: Decimal | None = None
    matched_reasons: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    search_duration_ms: int | None = None
    candidate_fetch_duration_ms: int | None = None
    candidates: list[MatchCandidateLogView] = Field(default_factory=list)


class MatchRunDetailView(BaseModel):
    run: MatchRunStatusView
    stores: list[MatchStoreRunView] = Field(default_factory=list)


class NotificationView(BaseModel):
    id: UUID
    type: str
    product_id: UUID | None = None
    match_run_id: UUID | None = None
    title: str
    message: str
    created_at: datetime
    read_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationListResponse(BaseModel):
    items: list[NotificationView] = Field(default_factory=list)
    unread_count: int = 0


class UnreadCountResponse(BaseModel):
    unread_count: int = 0
