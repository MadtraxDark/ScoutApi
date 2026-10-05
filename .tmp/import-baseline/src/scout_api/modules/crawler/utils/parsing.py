import re
from decimal import Decimal, InvalidOperation

from ..core.exceptions import MissingPriceError


def parse_money(value: str | None, currency: str) -> Decimal:
    if not value or not value.strip():
        raise MissingPriceError("Preço não encontrado")
    cleaned = re.sub(r"[^\d,.-]", "", value.replace("−", "-"))
    if currency == "BRL" or currency == "PYG":
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MissingPriceError(f"Preço inválido: {value!r}") from exc
    if amount <= 0:
        raise MissingPriceError(f"Preço não positivo: {value!r}")
    return amount
