"""Currency conversion service for the exchange-rate subsystem.

Pure FX only:

    foreign_price × exchange_rate = BRL reference value

Never adds IOF, import tax, ICMS, sales tax, shipping, card fees, bank fees,
remittance fees, travel costs, customs, or any other surcharge.

Business defaults:
- USD→BRL: tourism_sell (institution sells USD to the consumer)
- PYG→BRL: official (BCP referential)

Offer.currency drives the pair. Paraguay priced in USD uses USD→BRL directly
(never USD→PYG→BRL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.exchange.domain import (
    BUSINESS_DEFAULT_RATE_TYPE,
    ConversionResult,
    RateStatus,
    RateType,
)
from scout_api.modules.exchange.models import ExchangeRateLatest
from scout_api.modules.exchange.money import quantize_brl_money
from scout_api.modules.exchange.repository import ExchangeRateRepository

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


def _to_status(row: ExchangeRateLatest, *, stale_after_seconds: int) -> RateStatus:
    """Derive current RateStatus from the DB row."""
    try:
        return RateStatus(row.status)
    except ValueError:
        pass
    # Fallback: check age
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    if row.fetched_at < cutoff:
        return RateStatus.STALE
    return RateStatus.FRESH


def convert(
    amount: Decimal,
    from_currency: str,
    to_currency: str = "BRL",
    *,
    rate_type: RateType | None = None,
    session: Session,
    settings: Settings | None = None,
) -> ConversionResult:
    """Convert amount from from_currency to to_currency.

    Args:
        amount: Amount to convert (Decimal, > 0 expected).
        from_currency: Source currency ISO code (e.g. 'USD', 'PYG').
        to_currency: Target currency (default 'BRL').
        rate_type: Override rate type; uses business default if None.
        session: SQLAlchemy session.
        settings: App settings (defaults to global).

    Returns:
        ConversionResult — never raises.
    """
    cfg = settings or get_settings()

    if from_currency == to_currency:
        return ConversionResult(
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            converted_amount=amount,
            rate=Decimal("1"),
            rate_type=RateType.OFFICIAL,
            rate_status=RateStatus.FRESH,
            source="identity",
            fetched_at=datetime.now(UTC),
        )

    # Determine rate_type
    effective_rt = rate_type
    if effective_rt is None:
        effective_rt = BUSINESS_DEFAULT_RATE_TYPE.get((from_currency, to_currency))

    repo = ExchangeRateRepository(session)

    row: ExchangeRateLatest | None = None
    if effective_rt is not None:
        row = repo.get_latest(from_currency, to_currency, effective_rt)

    # Fallback: try any available rate_type for this pair
    if row is None:
        rows = repo.list_latest(base_currency=from_currency, quote_currency=to_currency)
        if rows:
            # Prefer fresh over stale, then prefer business default types
            preference_order = [
                RateType.TOURISM_SELL,
                RateType.OFFICIAL,
                RateType.PTAX_SELL,
                RateType.MARKET,
                RateType.TOURISM_BUY,
                RateType.PTAX_BUY,
            ]
            fresh_rows = [r for r in rows if r.status == "fresh"]
            candidate_pool = fresh_rows if fresh_rows else rows
            for pref in preference_order:
                for r in candidate_pool:
                    if r.rate_type == str(pref):
                        row = r
                        break
                if row is not None:
                    break
            if row is None:
                row = candidate_pool[0]

    if row is None:
        logger.debug(
            "exchange_rate_unavailable",
            extra={"from": from_currency, "to": to_currency, "rate_type": str(effective_rt)},
        )
        return ConversionResult(
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            converted_amount=None,
            rate=None,
            rate_type=effective_rt,
            rate_status=RateStatus.UNAVAILABLE,
            source=None,
            fetched_at=None,
            error=f"No rate available for {from_currency}/{to_currency}",
        )

    rate = Decimal(str(row.rate))
    if rate <= _ZERO:
        return ConversionResult(
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            converted_amount=None,
            rate=None,
            rate_type=RateType(row.rate_type),
            rate_status=RateStatus.UNAVAILABLE,
            source=row.source,
            fetched_at=row.fetched_at,
            error="Rate is zero or negative",
        )

    # Keep full rate precision; round only the monetary BRL amount.
    raw_converted = amount * rate
    converted = (
        quantize_brl_money(raw_converted) if to_currency.upper() == "BRL" else raw_converted
    )
    status = _to_status(row, stale_after_seconds=cfg.exchange_rate_stale_after_seconds)

    return ConversionResult(
        amount=amount,
        from_currency=from_currency,
        to_currency=to_currency,
        converted_amount=converted,
        rate=rate,
        rate_type=RateType(row.rate_type),
        rate_status=status,
        source=row.source,
        fetched_at=row.fetched_at,
    )


def convert_safe(
    amount: Decimal | None,
    from_currency: str | None,
    *,
    session: Session,
    settings: Settings | None = None,
) -> ConversionResult | None:
    """Safe wrapper: returns None if amount or currency is None/zero."""
    if amount is None or from_currency is None:
        return None
    try:
        return convert(
            amount,
            from_currency,
            session=session,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("exchange_convert_safe_error", extra={"error": str(exc)})
        return None
