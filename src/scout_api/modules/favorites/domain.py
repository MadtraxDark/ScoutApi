"""Price drop evaluation shared by notification and email content."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def effective_offer_price(snapshot: dict[str, Any] | None) -> Decimal | None:
    """Match PriceScout: PIX, current offer price, then original as a last fallback."""
    if not snapshot or snapshot.get("available") is False:
        return None
    return (
        _money(snapshot.get("pix_price"))
        or _money(snapshot.get("price"))
        or _money(snapshot.get("original_price"))
    )


@dataclass(frozen=True)
class PriceDrop:
    old_price: Decimal
    new_price: Decimal
    currency: str

    @property
    def difference(self) -> Decimal:
        return self.old_price - self.new_price

    @property
    def percent(self) -> Decimal:
        return (self.difference / self.old_price) * Decimal("100")


def evaluate_price_drop(
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> PriceDrop | None:
    if event_type != "price_changed" or not before or not after:
        return None
    old_currency = before.get("currency")
    new_currency = after.get("currency")
    if not old_currency or old_currency != new_currency:
        return None
    old_price = effective_offer_price(before)
    new_price = effective_offer_price(after)
    if old_price is None or new_price is None or new_price >= old_price:
        return None
    return PriceDrop(
        old_price=old_price, new_price=new_price, currency=str(new_currency)
    )
