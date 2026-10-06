"""Validation and outlier tests for exchange rates."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scout_api.modules.exchange.domain import FetchedRate, RateType
from scout_api.modules.exchange.validation import (
    check_max_change,
    check_tourism_premium,
    validate_batch,
    validate_rate,
)

NOW = datetime(2026, 9, 22, tzinfo=UTC)


def _rate(
    *,
    rate: Decimal,
    rate_type: RateType = RateType.TOURISM_SELL,
    source: str = "test",
    base: str = "USD",
    quote: str = "BRL",
) -> FetchedRate:
    return FetchedRate(
        base_currency=base,
        quote_currency=quote,
        rate_type=rate_type,
        rate=rate,
        source=source,
        source_timestamp=None,
        fetched_at=NOW,
    )


def test_outlier_100x_rejected() -> None:
    errs = validate_rate(
        _rate(rate=Decimal("550")),
        last_known=Decimal("5.30"),
        max_change_pct=15.0,
    )
    assert errs
    assert any("change" in e or "magnitude" in e for e in errs)


def test_reasonable_change_accepted() -> None:
    errs = validate_rate(
        _rate(rate=Decimal("5.40")),
        last_known=Decimal("5.30"),
        max_change_pct=15.0,
    )
    assert errs == []


def test_tourism_premium_band() -> None:
    assert check_tourism_premium(Decimal("5.30"), Decimal("5.12")) is None
    assert check_tourism_premium(Decimal("4.00"), Decimal("5.12")) is not None
    assert check_tourism_premium(Decimal("7.00"), Decimal("5.12")) is not None


def test_max_change_helper() -> None:
    assert (
        check_max_change(Decimal("550"), Decimal("5.5"), max_change_pct=15) is not None
    )
    assert check_max_change(Decimal("5.6"), Decimal("5.5"), max_change_pct=15) is None


def test_validate_batch_keeps_good_rates() -> None:
    rates = [
        _rate(rate=Decimal("5.30"), rate_type=RateType.TOURISM_SELL),
        _rate(rate=Decimal("5.12"), rate_type=RateType.PTAX_SELL, source="bcb_ptax"),
        _rate(rate=Decimal("550"), rate_type=RateType.TOURISM_BUY),  # outlier magnitude
    ]
    valid, rejected = validate_batch(rates, max_change_pct=15.0)
    assert len(valid) >= 2
    assert any(r.rate_type == RateType.TOURISM_BUY for r, _ in rejected)
