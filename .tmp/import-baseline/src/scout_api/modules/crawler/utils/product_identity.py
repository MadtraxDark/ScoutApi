"""Category-aware brand / model / variant identity for catalog search.

``model`` is the searchable base identity (chip, phone trim, CPU SKU, RAM line).
``variant`` is an optional commercial refinement (cooler line, color/storage).

Structured PDP fields still win when they already look like a base model.
Title parsing is fallback only and abstains when confidence is low.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

IdentityConfidence = Literal[
    "structured",
    "exact_title",
    "contextual",
    "ambiguous",
]

_GPU_CHIP_RE = re.compile(
    r"\b(?:nvidia\s+)?(?:geforce\s+)?(?P<nv_fam>rtx|gtx)\s*(?P<nv_num>\d{3,4})"
    r"\s*(?P<nv_suf>ti|super)?"
    r"|\b(?:amd\s+)?(?:radeon\s+)?(?P<amd_fam>rx)\s*(?P<amd_num>\d{3,4})"
    r"\s*(?P<amd_suf>xtx|xt)?\b",
    re.IGNORECASE,
)
_IPHONE_RE = re.compile(
    r"\biphone\s*(?P<num>1[0-9]e|[6-9]e|1[0-9]|[6-9])"
    r"(?:\s*(?P<suf>pro\s*max|pro|plus))?\b",
    re.IGNORECASE,
)
_GALAXY_RE = re.compile(
    r"\bgalaxy\s*s(?P<num>\d{1,2})(?:\s*(?P<suf>ultra|plus|\+|fe))?\b",
    re.IGNORECASE,
)
_RYZEN_RE = re.compile(
    r"\b(?:amd\s+)?ryzen\s+(?P<tier>[3579])\s+(?P<sku>\d{4}[A-Za-z0-9]{0,4})\b",
    re.IGNORECASE,
)
_CORE_RE = re.compile(
    r"\b(?:intel\s+)?core\s+(?P<fam>i[3579])-?(?P<sku>\d{4,5}[A-Za-z]{0,3})\b",
    re.IGNORECASE,
)
_SSD_RE = re.compile(
    r"\b(?P<series>[89]\d0)\s*(?P<line>evo\s*plus|evo\s*pro|pro|evo)\b",
    re.IGNORECASE,
)
_CAPACITY_TOKEN = re.compile(
    r"^\d+(?:[.,]\d+)?(?:gb|tb|mb|g)$",
    re.IGNORECASE,
)
_FREQ_TOKEN = re.compile(r"^\d{3,5}(?:mhz|mt/?s)?$", re.IGNORECASE)
_X_TOKEN = re.compile(r"^\d+x$", re.IGNORECASE)
_MPNISH = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$", re.IGNORECASE)

_GPU_SPEC_NOISE = frozenset(
    {
        "placa",
        "video",
        "vídeo",
        "gpu",
        "graphics",
        "card",
        "nvidia",
        "geforce",
        "radeon",
        "gddr5",
        "gddr6",
        "gddr7",
        "gddr6x",
        "hbm",
        "hbm2",
        "hbm3",
        "pcie",
        "pci",
        "express",
        "bit",
        "bits",
        "mhz",
        "ghz",
        "gbps",
        "dlss",
        "ray",
        "tracing",
        "fp4",
        "hdmi",
        "displayport",
        "nvenc",
        "rgb",
        "argb",
        "de",
        "da",
        "do",
        "com",
        "para",
        "e",
        "and",
        "the",
        "of",
        "a",
        "o",
    }
)
_GPU_EDITION_SKIP_FOR_BRAND = frozenset(
    {
        "dual",
        "prime",
        "tuf",
        "strix",
        "shadow",
        "ventus",
        "windforce",
        "eagle",
        "aorus",
        "gaming",
        "trio",
        "rog",
        "oc",
        "edition",
        "sff",
        "founders",
        "liquid",
        "inspire",
        "suprim",
        "vanguard",
        "expert",
        "infinity",
        "phoenix",
        "challenger",
        "turbo",
        "aero",
        "white",
        "solid",
        "ampere",
        "trinity",
        "master",
        "elite",
        "soc",
        "x",
    }
)
_VARIANT_MODIFIERS = frozenset(
    {
        "oc",
        "edition",
        "overclock",
        "overclocked",
        "edicao",
        "edição",
    }
)
_CHIP_VENDOR = frozenset({"nvidia", "amd"})
# Board partners / GPU manufacturers — brands, not SKUs. Extensible gazetteer.
_GPU_BOARD_BRANDS = frozenset(
    {
        "asus",
        "msi",
        "gigabyte",
        "palit",
        "zotac",
        "pny",
        "sapphire",
        "powercolor",
        "xfx",
        "asrock",
        "evga",
        "gainward",
        "galax",
        "inno3d",
        "leadtek",
        "maxsun",
        "yeston",
        "sparkle",
        "colorful",
        "manli",
        "biostar",
        "afox",
        "onix",
        "intel",
        "acer",
        "lenovo",
        "dell",
        "hp",
        "corsair",
        "nzxt",
    }
)


@dataclass(frozen=True)
class ParsedIdentity:
    category: str | None
    brand: str | None
    model: str | None
    model_key: str | None
    variant: str | None
    variant_key: str | None
    confidence: IdentityConfidence = "ambiguous"
    product_line: str | None = None
    """Optional commercial line (TUF, ROG) — may fold into public variant."""


def fold_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.casefold().strip()


def parse_title_identity(
    title: str | None,
    *,
    category: str | None = None,
) -> ParsedIdentity:
    """Parse a product title into brand / base model / optional variant.

    Dispatches through the :class:`CategoryProfile` registry when available,
    falling back to built-in parsers for core categories.
    """
    if not title or not title.strip():
        return ParsedIdentity(category, None, None, None, None, None)
    text = title.strip()
    detected = category
    if detected is None:
        from scout_api.modules.crawler.utils.product_attributes import (
            detect_product_category,
        )

        detected = detect_product_category(text)

    from scout_api.modules.crawler.utils.category_profiles.registry import (
        ensure_profiles_loaded,
        get_profile,
    )

    ensure_profiles_loaded()
    profile = get_profile(detected)
    if profile is not None and profile.parse_title is not None:
        parsed = profile.parse_title(text, detected or profile.id)
        if parsed.brand and profile.brand_aliases:
            aliased = profile.normalize_brand(parsed.brand)
            if aliased and aliased != parsed.brand:
                return ParsedIdentity(
                    category=parsed.category,
                    brand=aliased,
                    model=parsed.model,
                    model_key=parsed.model_key,
                    variant=parsed.variant,
                    variant_key=parsed.variant_key,
                    confidence=parsed.confidence,
                    product_line=parsed.product_line,
                )
        return parsed

    # Legacy fallback if profiles not wired yet.
    parsers = {
        "gpu": _parse_gpu,
        "smartphone": _parse_phone,
        "cpu": _parse_cpu,
        "ram": _parse_ram,
        "ssd": _parse_ssd,
    }
    parser = parsers.get(detected or "")
    if parser is None:
        return ParsedIdentity(detected, None, None, None, None, None, "ambiguous")
    return parser(text, detected or "")


def looks_like_base_model(category: str | None, value: str | None) -> bool:
    """True when a structured 'model' already is the searchable base identity."""
    if not value or not str(value).strip():
        return False
    text = str(value).strip()
    cat = category or ""
    # Console / phone / CPU structured labels often mix SKU + marketing words.
    if cat == "console":
        folded = fold_identity(text)
        return any(
            token in folded
            for token in (
                "playstation",
                "ps5",
                "ps4",
                "xbox",
                "switch",
                "cfi-",
                "cfi",
            )
        ) or (not looks_like_opaque_code(text) and len(text) >= 3)
    if looks_like_opaque_code(text):
        return False
    if cat == "gpu" or extract_gpu_chip(text) is not None:
        chip = extract_gpu_chip(text)
        if chip is None:
            return False
        leftover = _gpu_variant_tokens(text, brand=None, chip=chip)
        return leftover is None or leftover[0] is None
    if cat in {"smartphone", None}:
        if _IPHONE_RE.search(text) or _GALAXY_RE.search(text):
            return True
    if cat in {"cpu", None}:
        if _RYZEN_RE.search(text) or _CORE_RE.search(text):
            return True
    if cat in {"ssd", None} and _SSD_RE.search(text):
        return True
    if cat == "ram":
        return not looks_like_opaque_code(text) and not _CAPACITY_TOKEN.fullmatch(
            fold_identity(text).replace(" ", "")
        )
    if cat in {"motherboard", "notebook", "monitor", "psu", "cooler"}:
        return not looks_like_opaque_code(text) and len(str(text).strip()) >= 3
    return False


def looks_like_opaque_code(value: str | None) -> bool:
    """Manufacturer / store codes that must not become the searchable model."""
    if not value:
        return False
    text = value.strip()
    if " " not in text and _MPNISH.fullmatch(text):
        return True
    if extract_gpu_chip(text) is not None:
        return False
    if _IPHONE_RE.search(text) or _GALAXY_RE.search(text):
        return False
    compact = re.sub(r"[^A-Za-z0-9]+", "", text)
    if len(compact) < 8:
        return bool(_MPNISH.fullmatch(text))
    has_letter = any(ch.isalpha() for ch in compact)
    has_digit = any(ch.isdigit() for ch in compact)
    if not (has_letter and has_digit):
        return False
    folded = fold_identity(compact)
    if any(
        marker in folded
        for marker in (
            "iphone",
            "galaxy",
            "rtx",
            "gtx",
            "ryzen",
            "ideapad",
            "playstation",
            "ps5",
            "ps4",
            "xbox",
            "cfi",
        )
    ):
        return False
    return True


def canonical_model_key(
    value: str | None,
    *,
    title: str | None = None,
) -> str | None:
    """Compact comparable model key (``rtx5070`` ≠ ``rtx5070ti``)."""
    for blob in (value, title, f"{value or ''} {title or ''}".strip()):
        if not blob:
            continue
        chip = extract_gpu_chip(blob)
        if chip is not None:
            return chip[1]
        phone = _phone_from_text(blob)
        if phone is not None:
            return phone[1]
        cpu = _cpu_from_text(blob)
        if cpu is not None:
            return cpu[1]
        ssd = _ssd_from_text(blob)
        if ssd is not None:
            return ssd[1]
    if not value:
        return None
    folded = fold_identity(value)
    compact = re.sub(r"[^a-z0-9]+", "", folded)
    return compact or None


def canonical_variant_key(value: str | None) -> str | None:
    """Compact commercial-variant key. Trailing OC/Edition are flags, not SKUs."""
    if not value:
        return None
    folded = fold_identity(str(value))
    compacted = re.sub(r"[^a-z0-9]+", "", folded)
    if not compacted:
        return None
    compacted = re.sub(r"edition$", "", compacted)
    compacted = re.sub(r"oc$", "", compacted)
    return compacted or None


def canonicalize_model_display(category: str | None, value: str | None) -> str | None:
    """Return the canonical display form when the value already is a base model."""
    if not value:
        return None
    text = value.strip()
    # Console structured models often include revision / CFI codes — do not
    # collapse them to a short family label via title grammar.
    if category == "console":
        return text
    parsed = parse_title_identity(text, category=category)
    if parsed.model:
        return parsed.model
    if category == "gpu":
        chip = extract_gpu_chip(text)
        if chip is not None:
            return chip[0]
    return text


def extract_gpu_chip(text: str | None) -> tuple[str, str] | None:
    """Return ``(display, key)`` for a discrete GPU chip, or None."""
    if not text:
        return None
    match = _GPU_CHIP_RE.search(text)
    if not match:
        return None
    if match.group("nv_fam"):
        family = match.group("nv_fam").lower()
        number = match.group("nv_num")
        suffix = (match.group("nv_suf") or "").lower()
        suffix_label = {"ti": "Ti", "super": "Super"}.get(suffix, suffix.title())
        display = f"GeForce {family.upper()} {number}"
        if suffix:
            display += f" {suffix_label}"
        key = f"{family}{number}{suffix}"
        return display, key
    family = (match.group("amd_fam") or "").lower()
    number = match.group("amd_num") or ""
    suffix = (match.group("amd_suf") or "").lower()
    display = f"Radeon RX {number}"
    if suffix:
        display += f" {suffix.upper()}"
    key = f"{family}{number}{suffix}"
    return display, key


def models_equivalent(left: str | None, right: str | None) -> bool:
    left_key = canonical_model_key(left)
    right_key = canonical_model_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key


def variants_equivalent(left: str | None, right: str | None) -> bool:
    left_key = canonical_variant_key(left)
    right_key = canonical_variant_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key


def _title_tokens(title: str) -> list[str]:
    cleaned = re.sub(r"\s+[-–—|/,_]+\s+", " ", title)
    cleaned = re.sub(r"[|/,_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return [token for token in cleaned.split(" ") if token and token != "-"]


def _parse_gpu(title: str, category: str) -> ParsedIdentity:
    stripped = title.strip()
    if " " not in stripped and looks_like_opaque_code(stripped):
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    chip = extract_gpu_chip(title)
    if chip is None:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    display, key = chip
    brand = _gpu_brand(title, chip)
    variant, variant_key = _gpu_variant_tokens(title, brand=brand, chip=chip)
    confidence: IdentityConfidence = "exact_title"
    if variant is None:
        confidence = "exact_title"
    else:
        confidence = "contextual"
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=variant,
        variant_key=variant_key,
        confidence=confidence,
    )


def _gpu_brand(title: str, chip: tuple[str, str]) -> str | None:
    tokens = _title_tokens(title)
    chip_index = _chip_start_index(tokens, title)
    partners: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        folded = fold_identity(token)
        if folded in _CHIP_VENDOR:
            continue
        if folded in _GPU_BOARD_BRANDS:
            partners.append((index, token))
    if partners:
        if chip_index is not None:
            before = [item for item in partners if item[0] < chip_index]
            if before:
                return _display_token(before[-1][1])
        return _display_token(partners[0][1])
    if chip_index is None:
        return None
    for token in reversed(tokens[:chip_index]):
        lower = token.casefold()
        if _is_gpu_non_brand_token(token):
            continue
        if lower in _GPU_EDITION_SKIP_FOR_BRAND or _X_TOKEN.fullmatch(lower):
            continue
        if lower in _CHIP_VENDOR:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.&-]{1,30}", token):
            continue
        return _display_token(token)
    # Founders / reference card: NVIDIA/AMD may be the board brand.
    folded = fold_identity(title)
    if "founders" in folded:
        if "nvidia" in folded or "geforce" in folded:
            return "NVIDIA"
        if "radeon" in folded:
            return "AMD"
    return None


def _chip_start_index(tokens: list[str], title: str) -> int | None:
    match = _GPU_CHIP_RE.search(title)
    if not match:
        return None
    prefix = title[: match.start()]
    prefix_tokens = _title_tokens(prefix) if prefix.strip() else []
    return len(prefix_tokens)


def _is_gpu_non_brand_token(token: str) -> bool:
    lower = token.casefold()
    if lower in _GPU_SPEC_NOISE:
        return True
    if _CAPACITY_TOKEN.fullmatch(lower.replace(" ", "")):
        return True
    if _FREQ_TOKEN.fullmatch(lower.replace(" ", "")):
        return True
    if looks_like_opaque_code(token):
        return True
    return False


def _gpu_variant_tokens(
    title: str,
    *,
    brand: str | None,
    chip: tuple[str, str],
) -> tuple[str | None, str | None]:
    tokens = _title_tokens(title)
    match = _GPU_CHIP_RE.search(title)
    chip_parts = {fold_identity(part) for part in (chip[0].split() + [chip[1]])}
    if match:
        chip_parts.update(fold_identity(part) for part in _title_tokens(match.group(0)))
    brand_parts = {fold_identity(brand)} if brand else set()
    kept: list[str] = []
    for token in tokens:
        lower = token.casefold()
        folded = fold_identity(token)
        compact = re.sub(r"[^a-z0-9]+", "", folded)
        if folded in brand_parts or compact in brand_parts:
            continue
        if folded in chip_parts or compact in chip_parts:
            continue
        if lower in {"ti", "super", "xt", "xtx"}:
            continue
        if token.isdigit():
            continue
        if "bit" in lower or lower.endswith("mhz") or lower.endswith("gbps"):
            continue
        if _is_gpu_non_brand_token(token) and not _X_TOKEN.fullmatch(lower):
            continue
        if lower in _GPU_SPEC_NOISE:
            continue
        if _CAPACITY_TOKEN.fullmatch(compact):
            continue
        if looks_like_opaque_code(token):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+/-]{0,30}", token):
            continue
        kept.append(_display_token(token))
    if not kept:
        return None, None
    distinctive = [
        token
        for token in kept
        if token.casefold() not in _VARIANT_MODIFIERS
        and not _X_TOKEN.fullmatch(token.casefold())
    ]
    if not distinctive:
        return None, None
    display = " ".join(kept)
    return display, canonical_variant_key(display)


def _display_token(token: str) -> str:
    if re.search(r"\d", token) or _X_TOKEN.fullmatch(token.casefold()):
        return token.upper() if _X_TOKEN.fullmatch(token.casefold()) else token
    if token.isupper() and len(token) <= 4:
        return token.upper() if len(token) <= 3 else token.capitalize()
    if token.isupper() and len(token) > 1:
        return token.capitalize()
    if token.casefold() in {"oc", "sff", "rgb", "aio"}:
        return token.upper()
    if token.casefold() in {"tuf", "rog"}:
        return token.upper()
    return token[0].upper() + token[1:] if token else token


def _parse_phone(title: str, category: str) -> ParsedIdentity:
    phone = _phone_from_text(title)
    if phone is None:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    display, key = phone
    brand = None
    folded = fold_identity(title)
    if "iphone" in folded or "apple" in folded:
        brand = "Apple"
    elif "galaxy" in folded or "samsung" in folded:
        brand = "Samsung"
    else:
        tokens = _title_tokens(title)
        if tokens and re.fullmatch(r"[A-Za-z][A-Za-z0-9.&-]{1,30}", tokens[0]):
            brand = _display_token(tokens[0])
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=None,
        variant_key=None,
        confidence="exact_title",
    )


def _phone_from_text(text: str) -> tuple[str, str] | None:
    match = _IPHONE_RE.search(text)
    if match:
        number = match.group("num")
        suffix = re.sub(r"\s+", " ", (match.group("suf") or "").strip())
        display = f"iPhone {number}"
        if suffix:
            display += f" {suffix.title().replace('Max', 'Max')}"
            display = display.replace("Pro Max", "Pro Max")
        key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
        return display, key
    match = _GALAXY_RE.search(text)
    if match:
        number = match.group("num")
        suffix = (match.group("suf") or "").replace("+", "plus")
        display = f"Galaxy S{number}"
        if suffix:
            display += f" {suffix.title()}"
        key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
        return display, key
    return None


def _parse_cpu(title: str, category: str) -> ParsedIdentity:
    cpu = _cpu_from_text(title)
    if cpu is None:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    display, key = cpu
    brand = "AMD" if key.startswith("ryzen") else "Intel"
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=None,
        variant_key=None,
        confidence="exact_title",
    )


def _cpu_from_text(text: str) -> tuple[str, str] | None:
    match = _RYZEN_RE.search(text)
    if match:
        display = f"Ryzen {match.group('tier')} {match.group('sku')}"
        key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
        return display, key
    match = _CORE_RE.search(text)
    if match:
        display = f"Core {match.group('fam')}-{match.group('sku')}"
        key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
        return display, key
    return None


def _parse_ram(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    ddr_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if re.fullmatch(r"ddr[345]", token.casefold())
            or token.casefold() in {"sodimm", "dimm"}
        ),
        None,
    )
    if ddr_index is None:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    leading = tokens[:ddr_index]
    while leading and leading[0].casefold() in {
        "memoria",
        "memória",
        "memory",
        "kit",
        "ram",
    }:
        leading = leading[1:]
    if not leading:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(leading[0])
    line = [_display_token(token) for token in leading[1:]]
    # Drop capacity-like leftovers accidentally before DDR.
    line = [token for token in line if not _CAPACITY_TOKEN.fullmatch(token.casefold())]
    if not line:
        return ParsedIdentity(
            category=category,
            brand=brand,
            model=None,
            model_key=None,
            variant=None,
            variant_key=None,
            confidence="ambiguous",
        )
    display = " ".join(line)
    key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=None,
        variant_key=None,
        confidence="exact_title",
    )


def _parse_ssd(title: str, category: str) -> ParsedIdentity:
    ssd = _ssd_from_text(title)
    if ssd is None:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    display, key = ssd
    tokens = _title_tokens(title)
    brand = None
    for token in tokens:
        lower = token.casefold()
        if lower in {"ssd", "nvme", "hdd", "disco"}:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.&-]{1,30}", token):
            brand = _display_token(token)
            break
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=None,
        variant_key=None,
        confidence="exact_title",
    )


def _ssd_from_text(text: str) -> tuple[str, str] | None:
    match = _SSD_RE.search(text)
    if not match:
        return None
    line = (
        re.sub(r"\s+", " ", match.group("line")).strip().title().replace("Evo", "EVO")
    )
    display = f"{match.group('series')} {line}"
    key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
    return display, key


__all__ = [
    "IdentityConfidence",
    "ParsedIdentity",
    "canonical_model_key",
    "canonical_variant_key",
    "canonicalize_model_display",
    "extract_gpu_chip",
    "fold_identity",
    "looks_like_base_model",
    "looks_like_opaque_code",
    "models_equivalent",
    "parse_title_identity",
    "variants_equivalent",
]
