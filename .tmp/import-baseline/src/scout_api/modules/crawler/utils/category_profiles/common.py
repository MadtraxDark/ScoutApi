"""Shared deterministic normalizers for product identity attributes."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

_CAPACITY = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>tb|gb|mb|kb)\b",
    re.IGNORECASE,
)
_FREQUENCY = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>mhz|ghz|hz|mt/?s)\b",
    re.IGNORECASE,
)
_LENGTH_MM = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>mm|cm|m)\b",
    re.IGNORECASE,
)
_WATT = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>w|va)\b",
    re.IGNORECASE,
)
_KIT = re.compile(
    r"(?P<a>\d+)\s*[x×]\s*(?P<b>\d+)\s*(?P<unit>gb|tb|mb)?",
    re.IGNORECASE,
)
_DDR = re.compile(r"\bddr\s*([345])\b", re.IGNORECASE)
_PCIE = re.compile(r"\bpcie?\s*(?:gen)?\s*([345](?:\.\d)?)\b", re.IGNORECASE)
_80PLUS = re.compile(r"\b80\s*plus\b|\b80plus\b", re.IGNORECASE)
_RTX_COMPACT = re.compile(
    r"\b(rtx|gtx)\s*(\d{3,4})\s*(ti|super)?\b",
    re.IGNORECASE,
)

# Evidence-based manufacturer aliases (not product SKUs).
GLOBAL_BRAND_ALIASES: dict[str, str] = {
    "asustek": "ASUS",
    "asustek computer": "ASUS",
    "asus computer": "ASUS",
    "micro-star international": "MSI",
    "micro star international": "MSI",
    "hewlett packard": "HP",
    "hewlett-packard": "HP",
    "hp inc": "HP",
    "lenovo group": "Lenovo",
    "samsung electronics": "Samsung",
    "apple computer": "Apple",
    "apple inc": "Apple",
    "amd advanced micro devices": "AMD",
    "advanced micro devices": "AMD",
    "intel corporation": "Intel",
    "nvidia corporation": "NVIDIA",
    "corsair memory": "Corsair",
    "kingston technology": "Kingston",
    "western digital": "WD",
    "wdc": "WD",
}


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.casefold().strip()


def apply_brand_alias(
    brand: str,
    extra: Mapping[str, str] | None = None,
) -> str:
    folded = fold_text(brand)
    table = {**GLOBAL_BRAND_ALIASES, **dict(extra or {})}
    if folded in table:
        return table[folded]
    # Title-case multi-word brands carefully.
    if brand.isupper() and len(brand) <= 5:
        return brand.upper()
    return brand.strip()


def normalize_capacity(value: str | None) -> str | None:
    """``128GB`` / ``1tb`` → ``128 GB`` / ``1 TB``."""
    if not value:
        return None
    match = _CAPACITY.search(value.replace(",", "."))
    if not match:
        return None
    raw_num = match.group("num")
    num = raw_num if "." in raw_num else raw_num
    return f"{num} {match.group('unit').upper()}"


def normalize_frequency(value: str | None) -> str | None:
    """``6000mhz`` / ``240hz`` → ``6000 MHz`` / ``240 Hz``."""
    if not value:
        return None
    match = _FREQUENCY.search(value.replace(",", "."))
    if not match:
        return None
    raw_unit = match.group("unit").casefold().replace("mts", "mt/s")
    unit_map = {
        "mhz": "MHz",
        "ghz": "GHz",
        "hz": "Hz",
        "mt/s": "MT/s",
    }
    unit = unit_map.get(raw_unit, match.group("unit"))
    try:
        num = str(int(float(match.group("num"))))
    except ValueError:
        num = match.group("num")
    return f"{num} {unit}"


def normalize_length(value: str | None) -> str | None:
    """``360MM`` → ``360 mm`` (does not invent fan count)."""
    if not value:
        return None
    match = _LENGTH_MM.search(value.replace(",", "."))
    if not match:
        return None
    unit = match.group("unit").lower()
    num = match.group("num")
    try:
        if "." not in num:
            num = str(int(float(num)))
    except ValueError:
        pass
    return f"{num} {unit}"


def normalize_wattage(value: str | None) -> str | None:
    if not value:
        return None
    match = _WATT.search(value.replace(",", "."))
    if not match:
        return None
    unit = match.group("unit").upper()
    try:
        num = str(int(float(match.group("num"))))
    except ValueError:
        num = match.group("num")
    return f"{num} {unit}"


def normalize_kit_config(value: str | None) -> str | None:
    """``2 X 16 GB`` → ``2x16 GB``. Does **not** invent kit from total capacity."""
    if not value:
        return None
    match = _KIT.search(value)
    if not match:
        return None
    unit = (match.group("unit") or "GB").upper()
    return f"{int(match.group('a'))}x{int(match.group('b'))} {unit}"


def normalize_memory_type(value: str | None) -> str | None:
    """``DDR 5`` / ``ddr5`` → ``DDR5``."""
    if not value:
        return None
    match = _DDR.search(value)
    if match:
        return f"DDR{match.group(1)}"
    folded = fold_text(value)
    if folded.startswith("gddr"):
        digits = re.sub(r"[^0-9]", "", folded)
        return f"GDDR{digits}" if digits else "GDDR"
    return None


def normalize_pcie(value: str | None) -> str | None:
    if not value:
        return None
    match = _PCIE.search(value)
    if not match:
        return None
    gen = match.group(1)
    if "." not in gen:
        gen = f"{gen}.0"
    return f"PCIe {gen}"


def normalize_efficiency(value: str | None) -> str | None:
    if not value:
        return None
    if not _80PLUS.search(value):
        return None
    tier = None
    for label in ("titanium", "platinum", "gold", "silver", "bronze", "white"):
        if label in fold_text(value):
            tier = label.title()
            break
    return f"80 Plus {tier}" if tier else "80 Plus"


def normalize_gpu_token(value: str | None) -> str | None:
    """``RTX5070`` → ``RTX 5070`` (suffix preserved)."""
    if not value:
        return None
    match = _RTX_COMPACT.search(value)
    if not match:
        return None
    family = match.group(1).upper()
    number = match.group(2)
    suffix = (match.group(3) or "").upper()
    if suffix == "TI":
        suffix = "Ti"
    elif suffix:
        suffix = suffix.title()
    display = f"{family} {number}"
    if suffix:
        display += f" {suffix}"
    return display


def normalize_attribute_value(key: str, value: str | None) -> str | None:
    """Dispatch common unit normalizers by attribute key."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    key_l = key.casefold()
    if key_l in {
        "storage",
        "ram",
        "vram",
        "capacity",
        "total_capacity",
        "module_capacity",
    }:
        return normalize_capacity(text) or text
    if key_l in {"frequency", "refresh_rate"}:
        return normalize_frequency(text) or text
    if key_l in {"radiator_size", "fan_size", "cooler_height", "screen_size"}:
        # screen_size often uses inches; only mm/cm via length helper when unit present
        if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm)\b", text, re.I):
            return normalize_length(text) or text
        inch = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:\"|''|in|inch|polegadas?)?",
            text,
            re.I,
        )
        if inch and key_l == "screen_size":
            num = inch.group(1).replace(",", ".")
            return f'{num}"'
        return normalize_length(text) or text
    if key_l in {"wattage", "power"}:
        return normalize_wattage(text) or text
    if key_l in {"memory_type", "ddr"}:
        return normalize_memory_type(text) or text
    if key_l in {"pcie_generation", "pcie"}:
        return normalize_pcie(text) or text
    if key_l in {"efficiency", "efficiency_rating"}:
        return normalize_efficiency(text) or text
    if key_l in {"kit_configuration", "module_count"}:
        kit = normalize_kit_config(text)
        if kit:
            return kit
    if key_l in {"gpu_model", "gpu"}:
        return normalize_gpu_token(text) or text
    return text
