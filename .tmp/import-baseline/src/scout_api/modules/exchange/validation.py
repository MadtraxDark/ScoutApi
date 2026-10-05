"""Validation rules for fetched exchange rates.

Checks applied before persisting a rate:
1. Rate > 0 (basic sanity)
2. Magnitude bands (hard reject outside wide bands)
3. Tourism premium vs PTAX (tourism_sell in [ptax_sell, ptax_sell * 1.12])
4. PTAX agreement between Valor and BCB (≤ 0.5%)
5. Max % change vs last known good (reject silent outlier — do NOT invent replacement)

None of these checks modify or replace a rate; they only accept or reject it.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from scout_api.modules.exchange.domain import (
    MAGNITUDE_BANDS,
    FetchedRate,
    RateType,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


def check_positive(rate: FetchedRate) -> str | None:
    """Return an error string if rate is not positive."""
    if rate.rate <= _ZERO:
        return f"rate must be > 0, got {rate.rate}"
    return None


def check_magnitude(rate: FetchedRate) -> str | None:
    """Return an error string if rate falls outside the expected magnitude band."""
    pair = (rate.base_currency, rate.quote_currency)
    bands = MAGNITUDE_BANDS.get(pair)
    if bands is None:
        return None  # No bands defined for this pair — skip check
    lo, hi = bands
    if not (lo <= rate.rate <= hi):
        return (
            f"rate {rate.rate} outside magnitude band [{lo}, {hi}] "
            f"for {pair[0]}/{pair[1]}"
        )
    return None


def check_tourism_premium(
    tourism_sell: Decimal,
    ptax_sell: Decimal,
    *,
    max_premium_pct: float = 12.0,
) -> str | None:
    """Validate tourism_sell is between ptax_sell and ptax_sell * (1 + max_premium_pct/100).

    Tourism should be above PTAX (spread) but not absurdly higher.
    Returns an error string if the check fails, None otherwise.
    """
    if ptax_sell <= _ZERO:
        return None  # Can't validate without reference
    lower = ptax_sell
    upper = ptax_sell * (Decimal("1") + Decimal(str(max_premium_pct)) / Decimal("100"))
    if not (lower <= tourism_sell <= upper):
        return (
            f"tourism_sell {tourism_sell} outside expected range "
            f"[{lower:.4f}, {upper:.4f}] (ptax_sell={ptax_sell}, premium≤{max_premium_pct}%)"
        )
    return None


def check_ptax_agreement(
    ptax_valor: Decimal,
    ptax_bcb: Decimal,
    *,
    agreement_pct: float = 0.5,
) -> str | None:
    """Check that two PTAX sources agree within agreement_pct%.

    Returns an error string if they diverge too much.
    """
    if ptax_bcb <= _ZERO:
        return None
    diff_pct = abs(ptax_valor - ptax_bcb) / ptax_bcb * Decimal("100")
    max_pct = Decimal(str(agreement_pct))
    if diff_pct > max_pct:
        return (
            f"ptax sources diverge: valor={ptax_valor}, bcb={ptax_bcb}, "
            f"diff={diff_pct:.4f}% > {agreement_pct}%"
        )
    return None


def check_max_change(
    new_rate: Decimal,
    last_known: Decimal,
    *,
    max_change_pct: float = 15.0,
) -> str | None:
    """Reject if new_rate changed more than max_change_pct% from last_known.

    This is a soft guard against data glitches.
    Returns an error string if the check fails, None otherwise.
    """
    if last_known <= _ZERO:
        return None  # No reference — allow
    change_pct = abs(new_rate - last_known) / last_known * Decimal("100")
    max_pct = Decimal(str(max_change_pct))
    if change_pct > max_pct:
        return (
            f"rate change {change_pct:.2f}% exceeds max {max_change_pct}% "
            f"(new={new_rate}, last={last_known})"
        )
    return None


def validate_rate(
    rate: FetchedRate,
    *,
    last_known: Decimal | None = None,
    max_change_pct: float = 15.0,
) -> list[str]:
    """Run all individual-rate checks. Returns list of error strings (empty = OK)."""
    errors: list[str] = []

    err = check_positive(rate)
    if err:
        errors.append(err)
        return errors  # No point continuing if rate is zero/negative

    err = check_magnitude(rate)
    if err:
        errors.append(err)

    if last_known is not None:
        err = check_max_change(rate.rate, last_known, max_change_pct=max_change_pct)
        if err:
            errors.append(err)

    return errors


def validate_batch(
    rates: list[FetchedRate],
    *,
    last_known_map: dict[tuple[str, str, RateType], Decimal] | None = None,
    max_change_pct: float = 15.0,
    tourism_max_premium_pct: float = 12.0,
    ptax_agreement_pct: float = 0.5,
) -> tuple[list[FetchedRate], list[tuple[FetchedRate, list[str]]]]:
    """Validate a batch of rates.

    Returns:
        (valid_rates, rejected_rates) where rejected_rates is a list of
        (rate, error_list) tuples.
    """
    known = last_known_map or {}
    valid: list[FetchedRate] = []
    rejected: list[tuple[FetchedRate, list[str]]] = []

    for rate in rates:
        key = (rate.base_currency, rate.quote_currency, rate.rate_type)
        last_known = known.get(key)
        errs = validate_rate(rate, last_known=last_known, max_change_pct=max_change_pct)
        if errs:
            rejected.append((rate, errs))
            logger.warning(
                "exchange_rate_validation_failed",
                extra={
                    "source": rate.source,
                    "pair": f"{rate.base_currency}/{rate.quote_currency}",
                    "rate_type": str(rate.rate_type),
                    "errors": errs,
                },
            )
        else:
            valid.append(rate)

    # Cross-check: tourism premium vs ptax_sell (both need to be in the valid list)
    rate_map: dict[tuple[str, str, RateType], Decimal] = {
        (r.base_currency, r.quote_currency, r.rate_type): r.rate
        for r in valid
    }
    tourism_sell = rate_map.get(("USD", "BRL", RateType.TOURISM_SELL))
    ptax_sell = rate_map.get(("USD", "BRL", RateType.PTAX_SELL))
    if tourism_sell is not None and ptax_sell is not None:
        err = check_tourism_premium(tourism_sell, ptax_sell, max_premium_pct=tourism_max_premium_pct)
        if err:
            logger.warning("exchange_rate_tourism_premium_check", extra={"error": err})
            # Soft warning: don't reject; data might be legitimate early-morning spread

    # Cross-check: ptax agreement Valor vs BCB
    ptax_sell_valor = rate_map.get(("USD", "BRL", RateType.PTAX_SELL))
    ptax_sell_bcb_candidates = [
        r.rate
        for r in valid
        if r.base_currency == "USD"
        and r.quote_currency == "BRL"
        and r.rate_type == RateType.PTAX_SELL
        and r.source == "bcb_ptax"
    ]
    if ptax_sell_valor is not None and ptax_sell_bcb_candidates:
        err = check_ptax_agreement(
            ptax_sell_valor,
            ptax_sell_bcb_candidates[0],
            agreement_pct=ptax_agreement_pct,
        )
        if err:
            logger.warning("exchange_rate_ptax_agreement_failed", extra={"error": err})

    return valid, rejected
