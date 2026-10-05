"""Decimal parsing helpers for exchange-rate providers.

Never use float for monetary math. All parse functions return Decimal.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Presentation quantum for BRL monetary amounts (not applied to rates).
BRL_MONEY_QUANTUM = Decimal("0.01")


def quantize_brl_money(amount: Decimal) -> Decimal:
    """Round a BRL amount at the presentation boundary only."""
    return amount.quantize(BRL_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def parse_br_decimal(value: str) -> Decimal:
    """Parse a Brazilian-formatted decimal string to Decimal.

    Handles both comma-as-decimal-separator (e.g. '5,3034') and
    period-as-thousand-separator (e.g. '5.948,28' → '5948.28').

    Raises:
        ValueError: if the string cannot be parsed as a decimal number.
    """
    cleaned = value.strip()
    # Remove leading/trailing whitespace and non-breaking spaces
    cleaned = cleaned.replace("\xa0", "").replace("\u200b", "")
    if not cleaned:
        raise ValueError(f"Empty decimal string: {value!r}")

    # Brazilian format: period as thousands separator, comma as decimal sep.
    # e.g. '1.161,47' → '1161.47'; '5,3034' → '5.3034'; '5.948,28' → '5948.28'
    if "," in cleaned:
        # Has comma — treat period as thousand separator
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # No comma — period may be decimal separator or thousand separator
        # If there are multiple periods, they are thousand separators
        parts = cleaned.split(".")
        if len(parts) > 2:
            # Multiple dots → all are thousand separators, no decimal
            cleaned = cleaned.replace(".", "")
        # else: single dot or no dot → standard decimal

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse BR decimal: {value!r}") from exc


def parse_py_decimal(value: str) -> Decimal:
    """Parse a Paraguayan-formatted decimal string (same as BR format).

    BCP uses comma as decimal separator and period as thousand separator,
    same as Brazil. Delegates to parse_br_decimal.
    """
    return parse_br_decimal(value)
