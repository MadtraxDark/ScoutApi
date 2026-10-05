"""Domain types for the exchange-rate subsystem (ADR 0034).

ExchangeRate ≠ Tax ≠ IOF ≠ Shipping ≠ ImportCost.
This module resolves only the FX rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class RateType(StrEnum):
    TOURISM_BUY = "tourism_buy"
    """Dólar turismo — compra (instituição compra USD do cliente)."""
    TOURISM_SELL = "tourism_sell"
    """Dólar turismo — venda (instituição vende USD ao cliente). Business default USD→BRL."""
    PTAX_BUY = "ptax_buy"
    """PTAX BCB — compra (comercial, referência de auditoria)."""
    PTAX_SELL = "ptax_sell"
    """PTAX BCB — venda (comercial, referência de auditoria)."""
    OFFICIAL = "official"
    """Taxa referencial oficial (BCP ou SML). Default PYG→BRL."""
    MARKET = "market"
    """Mercado interbancário (usado para cross-rates de auditoria)."""


class RateStatus(StrEnum):
    FRESH = "fresh"
    """Rate fetched within the stale threshold."""
    STALE = "stale"
    """Rate older than the stale threshold but still serveable."""
    UNAVAILABLE = "unavailable"
    """No rate available (never fetched, or all providers failed)."""


@dataclass(frozen=True, slots=True)
class FetchedRate:
    """Single rate obtained from one provider fetch."""

    base_currency: str
    """Currency being priced (e.g. 'USD', 'PYG')."""
    quote_currency: str
    """Currency in which the price is expressed (e.g. 'BRL')."""
    rate_type: RateType
    rate: Decimal
    source: str
    """Short provider identifier (e.g. 'valor_data', 'bcb_ptax')."""
    source_timestamp: datetime | None
    """Timestamp embedded in the source data, if available."""
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Result of a currency conversion attempt."""

    amount: Decimal
    from_currency: str
    to_currency: str
    converted_amount: Decimal | None
    """Null when rate is unavailable."""
    rate: Decimal | None
    rate_type: RateType | None
    rate_status: RateStatus
    source: str | None
    fetched_at: datetime | None
    error: str | None = None
    """Human-readable reason when converted_amount is None."""


# Business defaults
BUSINESS_DEFAULT_RATE_TYPE: dict[tuple[str, str], RateType] = {
    ("USD", "BRL"): RateType.TOURISM_SELL,
    ("PYG", "BRL"): RateType.OFFICIAL,
    ("USD", "PYG"): RateType.OFFICIAL,
}

# Soft magnitude bands: (min, max) for sanity checks.
# Hard reject if rate falls outside these during validation.
MAGNITUDE_BANDS: dict[tuple[str, str], tuple[Decimal, Decimal]] = {
    ("USD", "BRL"): (Decimal("2"), Decimal("15")),
    ("PYG", "BRL"): (Decimal("0.00005"), Decimal("0.01")),
    ("USD", "PYG"): (Decimal("500"), Decimal("100000")),
}
