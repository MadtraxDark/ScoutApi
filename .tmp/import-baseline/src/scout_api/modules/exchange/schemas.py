"""Pydantic schemas for the exchange-rate API endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ExchangeRateView(BaseModel):
    """Public view of a single exchange rate."""

    base_currency: str = Field(description="Moeda base (ex.: USD, PYG).")
    quote_currency: str = Field(description="Moeda cotada (ex.: BRL).")
    rate_type: str = Field(description="Tipo da taxa (tourism_sell, ptax_sell, official…).")
    rate: Decimal = Field(description="Taxa de câmbio (Decimal).", examples=["5.3034"])
    source: str = Field(description="Provedor da taxa.")
    source_timestamp: datetime | None = Field(
        default=None, description="Timestamp da fonte, quando disponível."
    )
    fetched_at: datetime = Field(description="Momento da coleta.")
    status: str = Field(description="fresh | stale | unavailable.")
    consecutive_failures: int = Field(default=0)
    updated_at: datetime


class ExchangeRateListResponse(BaseModel):
    items: list[ExchangeRateView] = Field(default_factory=list)
    count: int


class ExchangeRateDiagnosticsResponse(BaseModel):
    """Admin diagnostics — includes scheduler state."""

    rates: list[ExchangeRateView] = Field(default_factory=list)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    stale_count: int = 0
    unavailable_pairs: list[str] = Field(default_factory=list)


class ExchangeRateRefreshResponse(BaseModel):
    """Response from POST /exchange-rates/refresh."""

    fetched: int
    valid: int
    rejected: int
    persisted: int
    errors: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    duration_ms: int
    next_refresh_at: str | None = None


# ---- Conversion fields added to ProductListingView / OfferSnapshotView ----

class ConversionFields(BaseModel):
    """Valor convertido em BRL (apenas FX — sem impostos/tarifas)."""

    converted_price_brl: Decimal | None = Field(
        default=None,
        description=(
            "Valor de referência em BRL = preço estrangeiro × cotação. "
            "Não inclui IOF, imposto, frete nem tarifas. Null se indisponível."
        ),
        examples=["5300.00"],
    )
    exchange_rate: Decimal | None = Field(
        default=None,
        description="Cotação usada (1 unidade da moeda original = N BRL).",
        examples=["5.3034"],
    )
    exchange_rate_type: str | None = Field(
        default=None,
        description="Tipo da taxa (tourism_sell, official…).",
    )
    exchange_rate_status: str | None = Field(
        default=None,
        description="Status da taxa: fresh, stale ou unavailable.",
    )
    exchange_rate_source: str | None = Field(
        default=None,
        description="Provedor da taxa de câmbio.",
    )
    exchange_rate_updated_at: datetime | None = Field(
        default=None,
        description="Momento em que a taxa foi coletada/atualizada.",
    )
