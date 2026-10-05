"""Helper to attach currency conversion fields to product/offer views.

Graceful: if the exchange-rate subsystem is unavailable or the DB has no
rates, the product/offer is returned without conversion fields (all None).
Never raises; never fails the product fetch.

ExchangeRate ≠ Tax/IOF. Converted values are indicative only.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from scout_api.modules.exchange.conversion_service import convert_safe

logger = logging.getLogger(__name__)


def attach_conversion(
    price: Decimal | None,
    currency: str | None,
    *,
    session: Session,
) -> dict[str, object]:
    """Compute conversion fields for a price/currency pair.

    Returns a dict with keys matching ConversionFields:
        converted_price_brl, exchange_rate, exchange_rate_type,
        exchange_rate_status, exchange_rate_source, exchange_rate_updated_at

    All values are None when conversion is unavailable or currency == 'BRL'.
    Pure FX only — never adds taxes or fees.
    """
    empty: dict[str, object] = {
        "converted_price_brl": None,
        "exchange_rate": None,
        "exchange_rate_type": None,
        "exchange_rate_status": None,
        "exchange_rate_source": None,
        "exchange_rate_updated_at": None,
    }

    if price is None or currency is None:
        return empty

    # No conversion needed for BRL prices — original already is BRL.
    if currency.upper() == "BRL":
        return empty

    try:
        result = convert_safe(price, currency, session=session)
    except Exception as exc:  # noqa: BLE001
        logger.debug("attach_conversion_error", extra={"error": str(exc)})
        return empty

    if result is None:
        return empty

    return {
        "converted_price_brl": result.converted_amount,
        "exchange_rate": result.rate,
        "exchange_rate_type": str(result.rate_type) if result.rate_type else None,
        "exchange_rate_status": str(result.rate_status),
        "exchange_rate_source": result.source,
        "exchange_rate_updated_at": result.fetched_at,
    }
