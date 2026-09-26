"""Identity normalization helpers for product matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.utils.product_identity import (
    canonical_variant_key,
    extract_gpu_chip,
    parse_title_identity,
)

VARIANT_GATE_KEYS = frozenset(
    {"color", "storage", "size", "capacity", "ram", "pack", "network_lock"}
)

# Map common PT/EN/ES color labels to a single canonical token for gates.
COLOR_CANONICAL: dict[str, str] = {
    "preto": "black",
    "black": "black",
    "negro": "black",
    "noir": "black",
    # Finish + base hue (marketing labels) → base hue for gates/search.
    "titanio preto": "black",
    "titanium black": "black",
    "black titanium": "black",
    "titanio branco": "white",
    "titanium white": "white",
    "white titanium": "white",
    "titanio cinza": "gray",
    "titanium gray": "gray",
    "titanium grey": "gray",
    "gray titanium": "gray",
    "grey titanium": "gray",
    "branco": "white",
    "white": "white",
    "blanco": "white",
    "blanc": "white",
    "azul": "blue",
    "blue": "blue",
    "vermelho": "red",
    "red": "red",
    "rojo": "red",
    "verde": "green",
    "green": "green",
    # Apple BR marketing labels for iPhone 16 palette.
    "verde acinzentado": "teal",
    "verde-acinzentado": "teal",
    "verde acizentado": "teal",
    "verde-acizentado": "teal",
    "teal": "teal",
    "ultramarino": "ultramarine",
    "ultramarine": "ultramarine",
    "dourado": "gold",
    "gold": "gold",
    "rose gold": "rosegold",
    "rosegold": "rosegold",
    "ouro rosa": "rosegold",
    "prateado": "silver",
    "prata": "silver",
    "silver": "silver",
    "cinza": "gray",
    "cinza espacial": "gray",
    "space gray": "gray",
    "space grey": "gray",
    "luna grey": "gray",
    "luna gray": "gray",
    "arctic grey": "gray",
    "arctic gray": "gray",
    "platinum grey": "gray",
    "platinum gray": "gray",
    "storm grey": "gray",
    "storm gray": "gray",
    "gray": "gray",
    "grey": "gray",
    "rosa": "pink",
    "pink": "pink",
    "roxo": "purple",
    "purple": "purple",
    "lavanda": "lavender",
    "lavender": "lavender",
    # Bare finish token — only when no base hue is present in the title.
    "titanio": "titanium",
    "titanium": "titanium",
    # Orange labels occur across PT/ES/EN catalogs. Compound labels are
    # canonicalized from their translated hue + finish tokens below.
    "orange": "orange",
    "laranja": "orange",
    "naranja": "orange",
}

# Color finish words are retained as semantic qualifiers, but their translated
# forms and order are normalized. This keeps "Cosmic Orange" and
# "Laranja cósmico" equivalent without collapsing Orange into Deep Blue, etc.
_COLOR_DESCRIPTOR_CANONICAL: dict[str, str] = {
    "cosmic": "cosmic",
    "cosmico": "cosmic",
    "cosmica": "cosmic",
    "space": "space",
    "espacial": "space",
    "deep": "deep",
    "intenso": "deep",
    "intensa": "deep",
    "dark": "dark",
    "escuro": "dark",
    "escura": "dark",
    "light": "light",
    "claro": "light",
    "clara": "light",
    "arctic": "arctic",
    "artico": "arctic",
    "artica": "arctic",
}
_COLOR_HUES = frozenset(COLOR_CANONICAL.values())

# Locale / marketplace aliases → VARIANT_GATE_KEYS (applied before gate filter).
_VARIANT_KEY_ALIASES: dict[str, str] = {
    "colour": "color",
    "cor": "color",
    "color": "color",
    "armazenamento": "storage",
    "capacidade": "storage",
    "capacidade_de_armazenamento": "storage",
    "memoria_interna": "storage",
    "memoria": "storage",
    "internal_storage": "storage",
    "storage": "storage",
    "capacity": "capacity",
    "tamanho": "size",
    "size": "size",
    "ram": "ram",
    "memoria_ram": "ram",
    "pack": "pack",
    "embalagem": "pack",
    "network_lock": "network_lock",
    "carrier_status": "network_lock",
    "carrier_lock": "network_lock",
}

# Attribute value aliases are grouped by meaning, then normalized only in
# contexts where the attribute is understood. This avoids translating entire
# titles or letting text similarity override explicit variant conflicts.
_ATTRIBUTE_VALUE_ALIASES: dict[str, dict[str, str]] = {
    "network_lock": {
        "unlocked": "unlocked",
        "desbloqueado": "unlocked",
        "desbloqueada": "unlocked",
        "liberado": "unlocked",
        "liberada": "unlocked",
        "locked": "locked",
        "bloqueado": "locked",
        "bloqueada": "locked",
    }
}
_TITLE_SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    "unlocked": (
        "fully unlocked",
        "desbloqueado",
        "desbloqueada",
        "liberado",
        "liberada",
    ),
    "locked": ("bloqueado", "bloqueada"),
    "wireless": ("sem fio", "sin cables", "inalambrico", "inalambrica", "sans fil"),
    "wired": ("com fio", "con cable", "cableado", "cableada", "avec fil"),
    "refurbished": (
        "renewed",
        "reconditioned",
        "reacondicionado",
        "reacondicionada",
        "recondicionado",
        "recondicionada",
    ),
    "used": ("usado", "usada", "usados", "usadas", "seminovo", "seminova"),
    "openbox": ("open box", "caja abierta", "caixa aberta"),
    "new": ("nuevo", "nueva", "novo", "nova", "neuf", "neuve"),
}

# Preferred cross-locale color labels for progressive SERP queries.
_COLOR_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "black": ("black", "preto", "negro", "titanium black", "black titanium"),
    "white": ("white", "branco", "blanco", "titanium white", "white titanium"),
    "blue": ("blue", "azul"),
    "red": ("red", "vermelho", "rojo"),
    "green": ("green", "verde"),
    "teal": ("teal", "verde acinzentado"),
    "ultramarine": ("ultramarine", "ultramarino"),
    "pink": ("pink", "rosa"),
    "purple": ("purple", "roxo"),
    "gray": ("gray", "cinza", "grey", "titanium gray", "titanium grey"),
    "gold": ("gold", "dourado"),
    "silver": ("silver", "prata", "prateado"),
    "orange": (
        "orange",
        "laranja",
        "naranja",
        "cosmic orange",
        "laranja cosmico",
        "naranja cosmico",
    ),
}

_COLOR_SEARCH_LOCALE_LABELS: dict[str, dict[str, str]] = {
    "black": {"en": "black", "pt": "preto", "es": "negro"},
    "white": {"en": "white", "pt": "branco", "es": "blanco"},
    "blue": {"en": "blue", "pt": "azul", "es": "azul"},
    "red": {"en": "red", "pt": "vermelho", "es": "rojo"},
    "green": {"en": "green", "pt": "verde", "es": "verde"},
    "teal": {"en": "teal", "pt": "verde acinzentado", "es": "verde azulado"},
    "pink": {"en": "pink", "pt": "rosa", "es": "rosa"},
    "purple": {"en": "purple", "pt": "roxo", "es": "morado"},
    "gray": {"en": "gray", "pt": "cinza", "es": "gris"},
    "gold": {"en": "gold", "pt": "dourado", "es": "dorado"},
    "silver": {"en": "silver", "pt": "prata", "es": "plateado"},
    "orange": {"en": "orange", "pt": "laranja", "es": "naranja"},
}

# Category labels are search vocabulary, not scoring signals. The canonical
# category stays locale-neutral; each integration's configured search locale
# selects at most one localized phrase for candidate discovery.
_CATEGORY_SEARCH_LABELS: dict[str, dict[str, str]] = {
    "gpu": {"pt": "placa de video", "en": "graphics card", "es": "tarjeta grafica"},
    "cpu": {"pt": "processador", "en": "processor", "es": "procesador"},
    "motherboard": {"pt": "placa mae", "en": "motherboard", "es": "placa base"},
    "monitor": {"pt": "monitor", "en": "monitor", "es": "monitor"},
    "smartphone": {"pt": "celular", "en": "smartphone", "es": "telefono"},
    "ssd": {"pt": "ssd", "en": "ssd", "es": "ssd"},
}
_CATEGORY_SEARCH_ALIASES = {
    "graphics_card": "gpu",
    "graphics card": "gpu",
    "video_card": "gpu",
    "video card": "gpu",
    "placa_de_video": "gpu",
    "placa de video": "gpu",
    "processor": "cpu",
    "processor_cpu": "cpu",
    "placa_mae": "motherboard",
    "placa mae": "motherboard",
    "baseboard": "motherboard",
    "mobile_phone": "smartphone",
    "phone": "smartphone",
    "solid_state_drive": "ssd",
}

_CONDITION_TOKENS = frozenset(
    {
        "renewed",
        "refurbished",
        "recondicionado",
        "recondicionada",
        "reacondicionado",
        "reacondicionada",
        "reconditioned",
        "renovado",
        "renovada",
        "usado",
        "usada",
        "usados",
        "usadas",
        "used",
        "cpo",
        "open box",
        "open-box",
        "caja abierta",
        "caixa aberta",
        "seminovo",
        "seminova",
    }
)

# iPhone 16e must win over bare "16"; allow glued suffix (16e) and spaced trims.
_IPHONE_MODEL_RE = re.compile(
    r"\biphone\s*(1[0-9]e|[6-9]e|1[0-9]|[6-9])(?:\s*(pro\s*max|pro|plus))?\b",
)

# Trailing marketing noise stripped before soft model comparison.
_MODEL_NOISE_SUFFIXES: tuple[str, ...] = (
    "everydaylaptop",
    "intelcore",
    "amdryzen",
    "windows11",
    "windows10",
    "processor",
    "laptop",
    "notebook",
)

_MODEL_FAMILY_MARKERS: tuple[str, ...] = (
    "ideapad",
    "thinkpad",
    "iphone",
    "galaxy",
    "macbook",
    "vivobook",
    "pavilion",
    "inspiron",
    "nitro",
    "legion",
    "evoplus",
    "evopro",
    "playstation",
    "rtx",
    "gtx",
    "rx",
)

# Manufacturer part-number shapes frequently published in titles / model fields.
_MPN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # AMD processor OPNs (boxed and tray are distinct packages of a known CPU).
    re.compile(r"\b100[-\s]?(?:100000|000000)\d{3}[a-z]{0,3}\b"),
    # Generic regional manufacturer part number (e.g. MG7L4LL/A). The
    # slash+region suffix is required to avoid treating ordinary model tokens
    # such as A3256 as a part number.
    re.compile(r"\b[a-z]{2,4}\d[a-z0-9]{2,8}/[a-z]{1,3}\b"),
    re.compile(r"\bmz[-\s]?[a-z]\d[a-z0-9]{4,}(?:/[a-z]{2})?\b"),  # Samsung SSD
    re.compile(r"\bsm-?[a-z]?\d{3}[a-z0-9]*(?:/[a-z]{2})?\b"),  # Samsung mobile
    re.compile(r"\bcfi[-\s]?\d{4}[a-z]?\b"),  # PlayStation SKU
    re.compile(r"\bhx\d{3}[a-z0-9]{4,}\b"),  # Kingston HyperX
    # MSI / board-style PNs (e.g. 912-V532-232) and GPU marketing codes
    # (e.g. G5070-12S3C). Generic alnum+hyphen forms — not store-specific.
    re.compile(r"\b\d{3}-v\d{3}-\d{3}\b"),
    re.compile(r"\bg\d{4}-\d{1,2}[a-z0-9]{2,4}\b"),
    # Manufacturer codes such as RX-76PSWFTFY: require a letter+digit mix on
    # both sides so generation/model strings like RTX-5060 are not treated as
    # opaque part numbers.
    re.compile(
        r"\b(?=[a-z0-9-]{8,}\b)"
        r"(?=[a-z]{2,5}-[a-z0-9-]*[a-z])"
        r"(?=[a-z]{2,5}-[a-z0-9-]*\d)"
        r"[a-z]{2,5}-[a-z0-9-]{4,}\b"
    ),
    # Motherboard / board marketing PNs (TUF-GAMING-B650M-E-WIFI) and ASUS
    # board SKUs (90MB1FV0-M0EAY0). Require a digit so pure marketing phrases
    # are not treated as identifiers.
    re.compile(r"\b(?=[a-z0-9-]*\d)[a-z]{2,}(?:-[a-z0-9]{1,16}){2,}\b"),
    re.compile(r"\b90mb[a-z0-9]+-[a-z0-9]+\b"),
)

# Bare commercial connectivity labels — never map onto the color variant gate.
_WIFI_VARIANT_LABELS: frozenset[str] = frozenset(
    {
        "wifi",
        "wi-fi",
        "wi fi",
        "wireless",
        "wifi 6",
        "wifi 6e",
        "wifi 7",
        "wi-fi 6",
        "wi-fi 6e",
        "wi-fi 7",
    }
)

# Motherboard board-code capture (B650M-E, X670E-PLUS, Z790-A, …).
_MOTHERBOARD_BOARD_RE = re.compile(
    r"\b([abzhx]\d{3}m?)\s*-?\s*"
    r"(plus|pro|gaming|wifi|a|e|f|i|ii|iii)?\b",
)
_MOTHERBOARD_FAMILY_RE = re.compile(
    r"\b(tuf(?:\s+gaming)?|rog(?:\s+strix)?|prime|proart|aorus|mag|mpg|strix)\b",
)

# When metadata stores RAM as "storage", prefer SSD-sized capacities from title.
_RAM_LIKE_MAX_GB = 64
_STORAGE_LIKE_MIN_GB = 128

ACCESSORY_TOKENS = frozenset(
    {
        "capa",
        "case",
        "pelicula",
        "película",
        "cabo",
        "carregador",
        "charger",
        "suporte",
        "cover",
        "skin",
        "protetor",
        "film",
        "adapter",
        "adaptador",
        "bolsa",
        "pouch",
    }
)

# Extra merchandise bundled with the core SKU (reject when reference is bare).
# Pack-in copy (Astro's Playroom on every PS5 Slim) and marketing tokens
# (Bluetooth / 8K / DualSense singular) are NOT bundle markers.
_BUNDLE_MARKERS: tuple[str, ...] = (
    "smartwatch",
    "apple watch",
    "galaxy watch",
    "galaxy fit",
    "galaxy buds",
    "buds3",
    "buds 3",
    "fit3",
    "fit 3",
    "airpods",
    "bundle",
    "kit ",
    " combo",
    "com fone",
    "com watch",
    "+ watch",
    "+ airpods",
    "fortnite",
    "gran turismo",
    "astro bot",
    "spider-man",
    "spiderman",
    "wolverine",
    "god of war",
    "horizon forbidden",
    "com 2 jogos",
    "2 jogos",
    "+ jogo",
    "+ game",
    "two games",
)

TITLE_STOPWORDS = frozenset(
    {
        "oferta",
        "promo",
        "promocao",
        "promoção",
        "frete",
        "gratis",
        "grátis",
        "envio",
        "novo",
        "nova",
        "original",
        "oficial",
        "kit",
        "com",
        "para",
        "the",
        "and",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "na",
        "no",
        "a",
        "o",
        "e",
        "ou",
        # GPU / PDP marketing noise — presence on one listing must not veto match.
        "nvidia",
        "geforce",
        "radeon",
        "placa",
        "video",
        "vídeo",
        "dlss",
        "ray",
        "tracing",
        "fp4",
        "pcie",
        "pci",
        "express",
        "mhz",
        "gbps",
        "bit",
        "displayport",
        "hdmi",
    }
)


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.casefold().strip()


def gtin_check_digit(body: str) -> str:
    """Compute GTIN check digit for an 7/11/12/13-digit body (without check)."""
    digits = [int(ch) for ch in body]
    total = 0
    # From right: odd positions weight 3, even weight 1 (GS1).
    for index, digit in enumerate(reversed(digits)):
        total += digit * (3 if index % 2 == 0 else 1)
    return str((10 - (total % 10)) % 10)


def normalize_gtin(raw: str | None) -> str | None:
    """Normalize EAN/UPC/GTIN to digits with valid check digit when possible."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    if len(digits) == 12:
        digits = f"0{digits}"
    if len(digits) == 8:
        # GTIN-8 — validate as-is
        body, check = digits[:-1], digits[-1]
        if gtin_check_digit(body) != check:
            return None
        return digits
    if len(digits) == 13:
        body, check = digits[:-1], digits[-1]
        if gtin_check_digit(body) != check:
            return None
        return digits
    if len(digits) == 14:
        body, check = digits[:-1], digits[-1]
        if gtin_check_digit(body) != check:
            return None
        return digits
    return None


def normalize_brand(raw: str | None) -> str | None:
    if not raw:
        return None
    folded = fold_text(raw)
    folded = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    if not folded:
        return None
    # Marketplace placeholders must not hard-reject real brand matches.
    if folded in {
        "outros",
        "other",
        "others",
        "generico",
        "n a",
        "na",
        "amazon renewed store",
        "amazon renewed",
        "renewed",
    }:
        return None
    return folded


def normalize_model(raw: str | None) -> str | None:
    if not raw:
        return None
    folded = fold_text(raw)
    folded = re.sub(r"[^a-z0-9]+", "", folded)
    return folded or None


def compact_model(model: str) -> str:
    """Strip trailing marketing/CPU noise for soft model comparison."""
    current = model
    changed = True
    while changed:
        changed = False
        for suffix in _MODEL_NOISE_SUFFIXES:
            if current.endswith(suffix) and len(current) - len(suffix) >= 6:
                current = current[: -len(suffix)]
                changed = True
                break
    return current


def _cpu_family(model: str) -> str | None:
    if "ryzen" in model or "amd" in model:
        return "amd"
    if "intel" in model:
        return "intel"
    return None


def _cpu_family_from_text(text: str | None) -> str | None:
    """Detect AMD vs Intel from model/title marketing copy."""
    folded = fold_text(text or "")
    if not folded:
        return None
    if re.search(r"\b(ryzen|amd)\b", folded):
        return "amd"
    if re.search(
        r"\bintel\b|\bcore\s*(?:ultra|i[3579]|[3579])\b",
        folded,
    ):
        return "intel"
    return None


def _cpu_signature(text: str | None) -> str | None:
    """Normalize a concrete CPU SKU from title/model when present."""
    folded = fold_text(text or "")
    if not folded:
        return None
    match = re.search(r"\bi([3579])\s*-?\s*(\d{4,5}[a-z0-9]{0,4})\b", folded)
    if match:
        return f"i{match.group(1)}-{match.group(2)}"
    match = re.search(r"\b(?:core\s*)?i([3579])\s*-?\s*(n\d{3}[a-z]*)\b", folded)
    if match:
        return f"i{match.group(1)}-{match.group(2)}"
    match = re.search(r"\bcore\s*([3579])\s+(\d{3}[a-z]*)\b", folded)
    if match:
        return f"core{match.group(1)}-{match.group(2)}"
    match = re.search(
        r"\bcore\s+ultra\s*([3579])\s*-?\s*(\d{3,5}[a-z]{0,3})\b",
        folded,
    )
    if match:
        return f"coreultra{match.group(1)}-{match.group(2)}"
    match = re.search(
        r"\b(?:amd\s+)?(?:ryzen\s*([3579])|r([3579]))\s*[- ]*"
        r"(\d{4,5}[a-z0-9]{0,4})\b",
        folded,
    )
    if match:
        tier = match.group(1) or match.group(2)
        return f"ryzen{tier}-{match.group(3)}"
    return None


def _cpu_socket(text: str | None) -> str | None:
    folded = fold_text(text or "")
    match = re.search(
        r"\b(?:socket\s*)?(am\s*\d+|swrx\d+|sp\d+|lga\s*\d{3,4})\b", folded
    )
    if not match:
        return None
    socket = re.sub(r"\s+", "", match.group(1))
    return socket


def processor_socket(identity: ProductIdentity) -> str | None:
    """Extract an explicitly listed socket from processor identity text."""
    return _cpu_socket(_identity_blob(identity.model, identity.title))


def processor_model_exact(
    reference: ProductIdentity, candidate: ProductIdentity
) -> bool:
    """Whether both listings expose the same explicit processor SKU."""
    left = _cpu_signature(_identity_blob(reference.model, reference.title))
    right = _cpu_signature(_identity_blob(candidate.model, candidate.title))
    return bool(left and right and left == right)


def _model_has_family(model: str) -> bool:
    return any(marker in model for marker in _MODEL_FAMILY_MARKERS)


def normalize_mpn(raw: str | None) -> str | None:
    """Collapse manufacturer part numbers to a comparable alphanumeric token."""
    if not raw:
        return None
    return normalize_model(raw)


def _format_cfi_mpn_display(normalized: str) -> str | None:
    """Rebuild ``CFI-2115B`` from compacted ``cfi2115b`` for SERP queries."""
    match = re.fullmatch(r"cfi(\d{4})([a-z]?)", normalized)
    if not match:
        return None
    return f"CFI-{match.group(1)}{match.group(2).upper()}"


def _format_samsung_mobile_mpn_display(normalized: str) -> str | None:
    """Rebuild ``SM-S938BZ/DS`` style from a compacted token for SERP queries."""
    if not normalized.startswith("sm") or len(normalized) < 8:
        return None
    body = normalized[2:]
    if len(body) >= 2 and body[-2:] in {"ds", "de", "du", "bds"}:
        # Keep common dual-SIM suffix after slash when present in source titles.
        if body.endswith("ds") and len(body) > 2:
            return f"SM-{body[:-2].upper()}/DS"
    return f"SM-{body.upper()}"


def _format_samsung_mpn_display(normalized: str) -> str | None:
    """Rebuild ``MZ-V9S1T0B/AM`` style from a compacted token for SERP queries."""
    if normalized.startswith("sm"):
        return _format_samsung_mobile_mpn_display(normalized)
    if not normalized.startswith("mz") or len(normalized) < 10:
        return None
    body = normalized[2:]
    if len(body) >= 2 and body[-2:] in {"am", "eu", "ap"}:
        return f"MZ-{body[:-2].upper()}/{body[-2:].upper()}"
    return f"MZ-{body.upper()}"


def extract_mpn_forms(*texts: str | None) -> tuple[str | None, str | None]:
    """Return ``(normalized, display)`` MPN forms for matching vs SERP queries.

    Amazon and other retailers rank hyphenated manufacturer PNs
    (``MZ-V9S1T0B/AM``) far above compacted tokens (``mzv9s1t0bam``).
    """
    forms = extract_all_mpn_forms(*texts)
    if not forms:
        return None, None
    return forms[0]


def extract_all_mpn_forms(*texts: str | None) -> list[tuple[str, str]]:
    """Return every distinct ``(normalized, display)`` MPN found in texts.

    Board numbers and marketing model codes often co-occur for the same SKU
    (e.g. ``912-V532-232`` and ``G5070-12S3C``). Collecting all of them keeps
    SERP queries and exact-MPN matching generic without hardcoding pairs.
    """
    parts = [part for part in texts if part]
    if not parts:
        return []
    joined = " ".join(parts)
    folded = fold_text(joined)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    raw_patterns = (
        re.compile(r"\b[A-Za-z]{2,4}\d[A-Za-z0-9]{2,8}/[A-Za-z]{1,3}\b"),
        re.compile(r"\bMZ[-\s]?[A-Za-z]\d[A-Za-z0-9]{4,}(?:/[A-Za-z]{2})?\b"),
        re.compile(r"\bSM[-\s]?[A-Za-z]?\d{3}[A-Za-z0-9]*(?:/[A-Za-z]{2})?\b"),
        re.compile(r"\bCFI[-\s]?\d{4}[A-Za-z]?\b"),
        re.compile(r"\bHX\d{3}[A-Za-z0-9]{4,}\b"),
        re.compile(r"\b\d{3}-V\d{3}-\d{3}\b", re.IGNORECASE),
        re.compile(r"\bG\d{4}-\d{1,2}[A-Za-z0-9]{2,4}\b", re.IGNORECASE),
        re.compile(r"\b[A-Za-z]{2,5}-[A-Za-z0-9-]{4,}\b"),
    )
    for pattern in _MPN_PATTERNS:
        for match in pattern.finditer(folded):
            normalized = normalize_mpn(match.group(0))
            if not normalized or len(normalized) < 8 or normalized in seen:
                continue
            display: str | None = None
            for raw_pattern in raw_patterns:
                for raw_match in raw_pattern.finditer(joined):
                    if normalize_mpn(raw_match.group(0)) != normalized:
                        continue
                    display = raw_match.group(0).strip().upper().replace(" ", "")
                    if "/" not in display and normalized.startswith("mz"):
                        display = _format_samsung_mpn_display(normalized) or display
                    if normalized.startswith("cfi"):
                        display = _format_cfi_mpn_display(normalized) or display
                    break
                if display is not None:
                    break
            if display is None:
                display = (
                    _format_samsung_mpn_display(normalized)
                    or _format_cfi_mpn_display(normalized)
                    or match.group(0).upper()
                )
            seen.add(normalized)
            found.append((normalized, display))
    return found


def extract_mpn(*texts: str | None) -> str | None:
    """Pull a normalized manufacturer PN from model/title text when present."""
    normalized, _display = extract_mpn_forms(*texts)
    return normalized


def _expand_compact_phone_model(compact: str | None) -> str | None:
    """Expand compacted phone tokens (``galaxys25ultra``) into SERP phrases."""
    token = re.sub(r"[^a-z0-9]+", "", fold_text(compact or ""))
    if not token:
        return None
    match = re.fullmatch(r"galaxys(\d{1,2})(ultra|plus|fe)?", token)
    if match:
        suffix = match.group(2) or ""
        return f"galaxy s{match.group(1)}" + (f" {suffix}" if suffix else "")
    match = re.fullmatch(
        r"iphone(1[0-9]e|[6-9]e|1[0-9]|[6-9])(promax|pro|plus)?",
        token,
    )
    if match:
        base = match.group(1)
        suffix = match.group(2) or ""
        if suffix == "promax":
            suffix = "pro max"
        return f"iphone {base}" + (f" {suffix}" if suffix else "")
    return None


def model_search_phrase(*, model: str | None, title: str | None) -> str | None:
    """Human-spaced commercial model for SERP (not the compacted identity token).

    Compacted identity tokens (``galaxys25ultra``, ``990evoplus``) are weak on
    retailer SERPs. Prefer spaced commercial phrases derived from title/model.
    """
    folded = fold_text(title or "")
    model_fold = fold_text(model or "")
    blob = f"{model_fold} {folded}".strip()
    match = re.search(
        r"\b([89]\d0)\s*(evo\s*plus|evo\s*pro|pro|evo)\b",
        blob,
    )
    if match:
        edition = re.sub(r"\s+", " ", match.group(2)).strip()
        # Compact identity tokens yield "evoplus"; SERP needs "evo plus".
        edition = re.sub(r"\bevo(plus|pro)\b", r"evo \1", edition)
        return f"{match.group(1)} {edition}"
    match = re.search(r"\b(rtx|gtx)\s*(\d{4})\s*(ti|super)?\b", blob)
    if match:
        return " ".join(part for part in match.groups() if part)
    match = _IPHONE_MODEL_RE.search(blob)
    if match:
        base = match.group(1)
        suffix = (match.group(2) or "").strip()
        return f"iphone {base}" + (f" {suffix}" if suffix else "")
    # Galaxy S-series: never emit brand+storage alone without the series.
    match = re.search(
        r"\bgalaxy\s*s(\d{1,2})(?:\s*(ultra|plus|\+|fe))?\b",
        blob,
    )
    if match:
        suffix = (match.group(2) or "").replace("+", "plus").strip()
        phrase = f"galaxy s{match.group(1)}"
        if suffix:
            phrase = f"{phrase} {suffix}"
        return phrase
    expanded_phone = _expand_compact_phone_model(model) or _expand_compact_phone_model(
        model_fold
    )
    if expanded_phone:
        return expanded_phone
    match = re.search(r"\bideapad\s*slim\s*(\d+i?)\b", blob)
    if match:
        return f"ideapad slim {match.group(1)}"
    # Motherboards: commercial board code (+ optional family / Wi-Fi).
    mb_phrase = _motherboard_board_search_phrase(blob)
    if mb_phrase:
        return mb_phrase
    # Consoles: expand compacted identity (playstation5digital) for SERP.
    # Omit "slim" from the primary phrase — Shopping China (and similar) treat
    # "slim" as a hard token and return [] / wrong Pro SKUs when combined with
    # brand+storage. Slim remains optional evidence, not a SERP requirement.
    if "playstation5" in model_fold or re.search(r"\b(?:playstation|ps)\s*5\b", folded):
        parts = ["playstation 5"]
        console_edition = _console_edition_signature(
            blob
        ) or _console_edition_signature(folded)
        if console_edition:
            parts.append(console_edition)
        return " ".join(parts)
    if model and " " in (title or ""):
        # Last resort: structured alphanumeric model codes are useful SERP keys
        # even when no category-specific commercial phrase exists. Retrieval
        # may use them broadly; MatchingEngine remains authoritative.
        compact_model = re.sub(r"[^a-z0-9]+", "", model_fold)
        if re.fullmatch(
            r"(?=[a-z0-9]{4,}$)(?=.*[a-z])(?=.*\d)[a-z0-9]+", compact_model
        ):
            return model_fold
        if not looks_like_mpn(model):
            return None
    return None


def title_hint_from_url(url: str | None) -> str | None:
    """Re-export: SERP title hint from PDP URL slug (crawler fingerprints)."""
    from scout_api.modules.crawler.core.fingerprints import (
        title_hint_from_url as _title_hint_from_url,
    )

    return _title_hint_from_url(url)


def serp_candidate_text(candidate: Any) -> str:
    """Title preferred; URL slug fallback for prefilter/ranking."""
    title = getattr(candidate, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return title_hint_from_url(getattr(candidate, "url", None)) or ""


def enrich_candidate_title(candidate: Any) -> Any:
    """Fill empty SERP titles from the URL slug without inventing product facts."""
    title = getattr(candidate, "title", None)
    if isinstance(title, str) and title.strip():
        return candidate
    hint = title_hint_from_url(getattr(candidate, "url", None))
    if not hint:
        return candidate
    if hasattr(candidate, "model_copy"):
        return candidate.model_copy(update={"title": hint})
    return candidate


def rank_candidates_for_query(
    candidates: list[Any],
    query: str,
) -> list[Any]:
    """Stable-reorder SERP hits so query-relevant titles/MPNs surface first.

    Does not drop candidates — only improves retrieval when the retailer ranks
    sibling SKUs above the exact PN/series match.

    Ranking uses title + URL **path** only. Amazon SERP links embed the full
    ``keywords=`` query in the query-string, which would otherwise make every
    card look like an exact compact match.
    """
    from urllib.parse import urlsplit

    q_fold = fold_text(query)
    q_tokens = {tok for tok in re.findall(r"[a-z0-9]+", q_fold) if len(tok) >= 2}
    q_compact = re.sub(r"[^a-z0-9]+", "", q_fold)
    # Tokens that often distinguish variants (colors, storage) — boost title hits.
    variantish = {
        tok
        for tok in q_tokens
        if tok in COLOR_CANONICAL
        or tok in COLOR_CANONICAL.values()
        or re.fullmatch(r"\d+gb", tok)
        or re.fullmatch(r"\d+tb", tok)
    }

    def sort_key(candidate: Any) -> tuple[int, int, int, int]:
        title = fold_text(serp_candidate_text(candidate))
        raw_url = getattr(candidate, "url", None) or ""
        path = urlsplit(raw_url).path or ""
        path_fold = fold_text(path)
        title_compact = re.sub(r"[^a-z0-9]+", "", title)
        path_compact = re.sub(r"[^a-z0-9]+", "", path_fold)
        exact = (
            1
            if q_compact
            and len(q_compact) >= 8
            and (q_compact in title_compact or q_compact in path_compact)
            else 0
        )
        title_tokens_set = set(re.findall(r"[a-z0-9]+", title))
        path_tokens_set = set(re.findall(r"[a-z0-9]+", path_fold))
        token_hits = len(q_tokens & (title_tokens_set | path_tokens_set))
        variant_hits = len(variantish & (title_tokens_set | path_tokens_set))
        return (exact, variant_hits, token_hits, min(len(title), 200))

    return sorted(candidates, key=sort_key, reverse=True)


def looks_like_mpn(token: str | None) -> bool:
    """True when a normalized model string looks like an opaque manufacturer PN."""
    if not token or len(token) < 8:
        return False
    if _model_has_family(token):
        return False
    if re.fullmatch(r"mz[a-z0-9]{6,}", token):
        return True
    if re.fullmatch(r"cfi\d{4}[a-z]?", token):
        return True
    # Mixed letter+digit opaque codes without commercial family markers.
    has_letter = any(ch.isalpha() for ch in token)
    has_digit = any(ch.isdigit() for ch in token)
    return has_letter and has_digit and not token.startswith(("iphone", "rtx", "gtx"))


def _gpu_signature(text: str | None) -> str | None:
    chip = extract_gpu_chip(text)
    return chip[1] if chip else None


def _localized_category_search_term(
    category: str | None, locale: str | None
) -> str | None:
    if not category or not locale:
        return None
    folded_category = fold_text(category).replace(" ", "_")
    canonical = _CATEGORY_SEARCH_ALIASES.get(folded_category, folded_category)
    labels = _CATEGORY_SEARCH_LABELS.get(canonical)
    return labels.get(locale.split("-", maxsplit=1)[0].casefold()) if labels else None


def _is_wifi_variant_label(text: str | None) -> bool:
    if not text:
        return False
    folded = re.sub(r"\s+", " ", fold_text(text)).strip()
    return folded in _WIFI_VARIANT_LABELS


def _wifi_presence(text: str | None) -> bool | None:
    """True/False when Wi-Fi is explicit; None when unknown (missing ≠ conflict)."""
    folded = fold_text(text or "")
    if not folded:
        return None
    if re.search(r"\bwi-?fi\b|\bwireless\b", folded):
        return True
    return None


def _motherboard_board_signature(text: str | None) -> str | None:
    """Compact board SKU (b650me / b650mplus) for equality checks."""
    folded = fold_text(text or "")
    if not folded:
        return None
    # Prefer hyphenated board codes first (B650M-E, B650M-PLUS).
    match = re.search(
        r"\b([abzhx]\d{3}m?)\s*-\s*([a-z0-9]+)\b",
        folded,
    )
    if match:
        return f"{match.group(1)}{match.group(2)}"
    # Common motherboard board suffixes can be separated by spaces in titles
    # (e.g. A520M K V2 / A520M DS3H V2). Keep this generic and exclude common
    # platform/spec tokens so AM4, DDR4, and similar text are not SKU suffixes.
    spaced = re.search(
        r"\b([abzhx]\d{3}m?)\s+([a-z]{1,4}\d[a-z0-9]{0,3}|[a-z])"
        r"(?:\s+v\d+)?\b",
        folded,
    )
    if spaced:
        suffix = spaced.group(2)
        if suffix not in {"am4", "am5", "ddr3", "ddr4", "ddr5"}:
            return f"{spaced.group(1)}{suffix}"
    match = _MOTHERBOARD_BOARD_RE.search(folded)
    if not match:
        return None
    base = match.group(1)
    suffix = (match.group(2) or "").strip()
    if suffix in {"wifi", "gaming"}:
        suffix = ""
    return f"{base}{suffix}" if suffix else base


def _motherboard_board_search_phrase(text: str | None) -> str | None:
    """Human-spaced board phrase for SERP (b650m-e wifi)."""
    folded = fold_text(text or "")
    if not folded:
        return None
    match = re.search(
        r"\b([abzhx]\d{3}m?)\s*-\s*([a-z0-9]+)\b",
        folded,
    )
    if match:
        board = f"{match.group(1)}-{match.group(2)}"
    else:
        match = _MOTHERBOARD_BOARD_RE.search(folded)
        if not match:
            return None
        base = match.group(1)
        suffix = (match.group(2) or "").strip()
        if suffix and suffix not in {"wifi", "gaming"}:
            board = f"{base}-{suffix}" if len(suffix) > 1 else f"{base}-{suffix}"
        else:
            board = base
    family = _MOTHERBOARD_FAMILY_RE.search(folded)
    parts: list[str] = []
    if family:
        fam = re.sub(r"\s+", " ", family.group(1)).strip()
        parts.append(fam)
    parts.append(board)
    if _wifi_presence(folded):
        parts.append("wifi")
    return " ".join(parts)


def _motherboard_family_phrase(text: str | None) -> str | None:
    match = _MOTHERBOARD_FAMILY_RE.search(fold_text(text or ""))
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _gpu_edition_signature(text: str | None) -> str | None:
    """Commercial cooler/edition line for discrete GPUs (missing ≠ conflict).

    Category-aware title parsing keeps cooler lines (Shadow 3X, Dual OC) out of
    the chip model. Missing edition is unknown, not a mismatch.
    """
    if not text:
        return None
    parsed = parse_title_identity(text, category="gpu")
    if parsed.variant_key:
        return parsed.variant_key
    return canonical_variant_key(parsed.variant) if parsed.variant else None


def _gpu_vram_from_text(text: str | None) -> str | None:
    folded = fold_text(text or "")
    if not folded or not _gpu_signature(folded):
        return None
    match = re.search(r"\b(8|10|12|16|20|24)\s*g(?:b|ddr)?\b", folded)
    if not match:
        return None
    return f"{match.group(1)}gb"


def _memory_type_signature(text: str | None) -> str | None:
    match = re.search(r"\bgddr\s*([567])\b", fold_text(text or ""))
    if not match:
        return None
    return f"gddr{match.group(1)}"


def _ssd_signature(text: str | None) -> str | None:
    folded = fold_text(text or "")
    match = re.search(
        r"\b([89]\d0)\s*(evo\s*plus|evo\s*pro|pro|evo)\b",
        folded,
    )
    if not match:
        return None
    return normalize_model(f"{match.group(1)} {match.group(2)}")


def _phone_signature(text: str | None) -> str | None:
    folded = fold_text(text or "")
    match = _IPHONE_MODEL_RE.search(folded)
    if match:
        suffix = (match.group(2) or "").replace(" ", "")
        return normalize_model(f"iphone {match.group(1)}{suffix}")
    # Galaxy Z Fold / Flip before bare digit series.
    match = re.search(r"\bgalaxy\s*z\s*(fold|flip)\s*(\d{1,2})\b", folded)
    if match:
        return normalize_model(f"galaxyz{match.group(1)}{match.group(2)}")
    # Galaxy S / A / M / F / Note generations (S25 Ultra ≠ A56).
    match = re.search(
        r"\bgalaxy\s*([samf]|note)\s*(\d{1,2})(?:\s*(ultra|plus|\+|fe))?\b",
        folded,
    )
    if match:
        line = match.group(1)
        suffix = (match.group(3) or "").replace("+", "plus")
        return normalize_model(f"galaxy{line}{match.group(2)}{suffix}")
    return None


def _console_edition_signature(text: str | None) -> str | None:
    """Return ``digital`` / ``disc`` when the listing explicitly states edition.

    Missing edition is ``None`` (unknown) — never invent from marketing noise.
    """
    folded = fold_text(text or "")
    if not folded:
        return None
    # Require console family context so "digital" camera kits don't fire.
    if not re.search(r"\b(?:playstation|ps)\s*5\b|\bps5\b|\bcfi-?\d", folded):
        return None
    if re.search(
        r"\bdigital(?:\s+edition)?\b|\bedicao\s+digital\b|\bedici[oó]n\s+digital\b",
        folded,
    ):
        return "digital"
    if re.search(
        r"\bdisc(?:\s+version|\s+edition)?\b|\bblu-?ray\b|\boptical\b|"
        r"\bedicao\s+(?:fisica|disco)\b",
        folded,
    ):
        return "disc"
    return None


def _controller_count_signature(text: str | None) -> int | None:
    """Explicit controller quantity when stated; ``None`` means unknown."""
    folded = fold_text(text or "")
    if not folded:
        return None
    match = re.search(
        r"\b(\d+)\s*(?:controles?|controllers?|dualsense)\b",
        folded,
    )
    if match:
        count = int(match.group(1))
        if 1 <= count <= 4:
            return count
    if re.search(r"\b(?:dois|two)\s+controles?\b|\bdualsense\s*x\s*2\b", folded):
        return 2
    if re.search(r"\b(?:um|one|1)\s+controle\b", folded):
        return 1
    return None


def _ddr_signature(text: str | None) -> str | None:
    match = re.search(r"\bddr\s*([45])\b", fold_text(text or ""))
    if not match:
        return None
    return f"ddr{match.group(1)}"


def _edition_token(identity: ProductIdentity) -> str | None:
    blob = identity.title
    from_text = _gpu_edition_signature(blob)
    raw = from_text or identity.variant_attrs.get("edition")
    if not raw:
        return None
    return normalize_variant_value("edition", str(raw)) or None


def _edition_search_phrase(identity: ProductIdentity) -> str | None:
    """Spaced commercial edition for SERP (``shadow 3x oc``), not compacted token."""
    parsed = parse_title_identity(identity.title, category="gpu")
    if parsed.variant:
        return re.sub(r"\s+", " ", parsed.variant).strip().casefold()
    raw = identity.variant_attrs.get("edition")
    if not raw:
        return None
    token = fold_text(str(raw))
    token = re.sub(r"(\d+)x", r" \1x ", token)
    token = re.sub(r"oc$", " oc", token)
    token = re.sub(r"([a-z])(\d)", r"\1 \2", token)
    return re.sub(r"\s+", " ", token).strip() or None


def _identity_blob(model: str | None, title: str | None) -> str:
    return f"{model or ''} {title or ''}".strip()


def critical_identity_conflict(
    reference: ProductIdentity,
    candidate: ProductIdentity,
) -> str | None:
    """Return a blocker code when critical category attributes diverge.

    Precision-first: incompatible GPU suffix, phone trim, SSD series, DDR
    generation, console edition (digital≠disc), explicit controller counts,
    or product storage capacity must not be compensated by title similarity.

    Missing attributes are **not** conflicts — only explicit disagreements.
    """
    checks: tuple[tuple[str, str | None, str | None], ...] = (
        (
            "gpu",
            _gpu_signature(_identity_blob(reference.model, reference.title)),
            _gpu_signature(_identity_blob(candidate.model, candidate.title)),
        ),
        (
            "gpu_edition",
            _edition_token(reference),
            _edition_token(candidate),
        ),
        (
            "ssd",
            _ssd_signature(_identity_blob(reference.model, reference.title)),
            _ssd_signature(_identity_blob(candidate.model, candidate.title)),
        ),
        (
            "phone",
            _phone_signature(_identity_blob(reference.model, reference.title)),
            _phone_signature(_identity_blob(candidate.model, candidate.title)),
        ),
        (
            "ddr",
            _ddr_signature(_identity_blob(reference.model, reference.title)),
            _ddr_signature(_identity_blob(candidate.model, candidate.title)),
        ),
        (
            "console_edition",
            _console_edition_signature(
                _identity_blob(reference.model, reference.title)
            ),
            _console_edition_signature(
                _identity_blob(candidate.model, candidate.title)
            ),
        ),
        (
            "motherboard",
            _motherboard_board_signature(
                _identity_blob(reference.model, reference.title)
            ),
            _motherboard_board_signature(
                _identity_blob(candidate.model, candidate.title)
            ),
        ),
    )
    for name, left, right in checks:
        if left and right and left != right:
            return f"{name}_mismatch:{left}!={right}"

    left_cpu_text = _identity_blob(reference.model, reference.title)
    right_cpu_text = _identity_blob(candidate.model, candidate.title)
    left_cpu = _cpu_signature(left_cpu_text)
    right_cpu = _cpu_signature(right_cpu_text)
    if left_cpu and right_cpu and left_cpu != right_cpu:
        return f"processor_model_mismatch:{left_cpu}!={right_cpu}"
    if (left_cpu or right_cpu) and (
        reference.category == "cpu" or candidate.category == "cpu"
    ):
        left_socket = _cpu_socket(left_cpu_text)
        right_socket = _cpu_socket(right_cpu_text)
        if left_socket and right_socket and left_socket != right_socket:
            return f"processor_socket_mismatch:{left_socket}!={right_socket}"

    ref_wifi = _wifi_presence(_identity_blob(reference.model, reference.title))
    cand_wifi = _wifi_presence(_identity_blob(candidate.model, candidate.title))
    # Explicit wifi vs non-wifi only when both sides declare presence/absence.
    # Missing Wi-Fi mention remains unknown (missing ≠ conflict).
    if ref_wifi is True and cand_wifi is False:
        return "wifi_mismatch:true!=false"
    if ref_wifi is False and cand_wifi is True:
        return "wifi_mismatch:false!=true"

    ref_controllers = _controller_count_signature(reference.title)
    cand_controllers = _controller_count_signature(candidate.title)
    if (
        ref_controllers is not None
        and cand_controllers is not None
        and ref_controllers != cand_controllers
    ):
        return f"controller_count_mismatch:{ref_controllers}!={cand_controllers}"

    ref_vram = reference.variant_attrs.get("vram") or _gpu_vram_from_text(
        _identity_blob(reference.model, reference.title)
    )
    cand_vram = candidate.variant_attrs.get("vram") or _gpu_vram_from_text(
        _identity_blob(candidate.model, candidate.title)
    )
    if ref_vram and cand_vram:
        left = normalize_variant_value("vram", str(ref_vram))
        right = normalize_variant_value("vram", str(cand_vram))
        if left and right and left != right:
            return f"vram_mismatch:{left}!={right}"

    ref_storage = reference.variant_attrs.get("storage") or reference.variant_attrs.get(
        "capacity"
    )
    cand_storage = candidate.variant_attrs.get(
        "storage"
    ) or candidate.variant_attrs.get("capacity")
    if not ref_storage:
        ref_storage = _storage_like_from_title(reference.title)
    if not cand_storage:
        cand_storage = _storage_like_from_title(candidate.title)
    if ref_storage and cand_storage:
        left = normalize_variant_value("storage", ref_storage)
        right = normalize_variant_value("storage", cand_storage)
        left_gb = _capacity_gb(left)
        right_gb = _capacity_gb(right)
        if (
            left_gb is not None
            and right_gb is not None
            and left_gb >= _STORAGE_LIKE_MIN_GB
            and right_gb >= _STORAGE_LIKE_MIN_GB
            and left_gb != right_gb
        ):
            return f"storage_mismatch:{left}!={right}"
    return None


def models_compatible(
    left: str | None,
    right: str | None,
    *,
    left_title: str | None = None,
    right_title: str | None = None,
) -> bool:
    """True when model tokens refer to the same product line (not opaque SKUs)."""
    left_blob = _identity_blob(left, left_title)
    right_blob = _identity_blob(right, right_title)
    left_mpns = {norm for norm, _ in extract_all_mpn_forms(left, left_title)}
    right_mpns = {norm for norm, _ in extract_all_mpn_forms(right, right_title)}
    left_mpn = next(iter(sorted(left_mpns)), None)
    right_mpn = next(iter(sorted(right_mpns)), None)
    if left_mpns and right_mpns and left_mpns & right_mpns:
        return True

    left_ssd = _ssd_signature(left_blob)
    right_ssd = _ssd_signature(right_blob)
    if left_ssd and right_ssd:
        return left_ssd == right_ssd

    left_gpu = _gpu_signature(left_blob)
    right_gpu = _gpu_signature(right_blob)
    if left_gpu and right_gpu:
        return left_gpu == right_gpu

    left_phone = _phone_signature(left_blob)
    right_phone = _phone_signature(right_blob)
    if left_phone and right_phone:
        return left_phone == right_phone

    left_board = _motherboard_board_signature(left_blob)
    right_board = _motherboard_board_signature(right_blob)
    if left_board and right_board:
        return left_board == right_board

    if not left or not right:
        # One side only has a commercial series inferred from the other title.
        if left_ssd and left_ssd == infer_model_from_title(right_title):
            return True
        if right_ssd and right_ssd == infer_model_from_title(left_title):
            return True
        return False

    family_a = _cpu_family(left) or _cpu_family_from_text(left_blob)
    family_b = _cpu_family(right) or _cpu_family_from_text(right_blob)
    if family_a and family_b and family_a != family_b:
        return False
    sig_a = _cpu_signature(left_blob)
    sig_b = _cpu_signature(right_blob)
    if sig_a and sig_b and sig_a != sig_b:
        return False
    if left == right:
        return True

    # Opaque MPN on one side + commercial series on the other: accept when the
    # same MPN appears in the commercial side's title and series signatures match.
    right_compact = normalize_model(right_title or "") or ""
    left_compact = normalize_model(left_title or "") or ""
    if looks_like_mpn(left) and right_ssd and left_mpn and left_mpn in right_compact:
        return True
    if looks_like_mpn(right) and left_ssd and right_mpn and right_mpn in left_compact:
        return True

    a, b = compact_model(left), compact_model(right)
    if a == b:
        return True
    # Naming drift: IdeaPad Slim 3 vs Slim 3i
    if a + "i" == b or b + "i" == a:
        return True

    # PlayStation 5 family: edition/slim suffixes are optional when not conflicting.
    # Explicit digital≠disc is handled by critical_identity_conflict.
    if ("playstation5" in a or a.startswith("ps5")) and (
        "playstation5" in b or b.startswith("ps5")
    ):
        left_ed = _console_edition_signature(left_blob)
        right_ed = _console_edition_signature(right_blob)
        if left_ed and right_ed and left_ed != right_ed:
            return False
        return True

    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 10 or not longer.startswith(shorter):
        return False
    rest = longer[len(shorter) :]
    if not rest:
        return True
    if rest[0].isdigit():
        # Different chassis / generation code (e.g. …3 + 15irh10…)
        return False
    # Critical suffixes must not be soft-matched (Ti / Super / Pro / Max / Plus…).
    if rest in {"ti", "super", "oc", "pro", "plus", "max", "ultra"}:
        return False
    # Console edition / slim as optional soft suffixes (conflict gated elsewhere).
    if rest in {"digital", "disc", "bluray", "slim", "ps5"}:
        return True
    return rest in {"i", "air"}


def infer_model_from_title(title: str | None) -> str | None:
    """Best-effort commercial model token from title when structured model is weak."""
    folded = fold_text(title or "")
    if not folded:
        return None
    phone = _phone_signature(folded)
    if phone:
        # Re-space for display-ish tokens used only as identity model keys.
        return phone
    match = re.search(r"\bideapad\s*slim\s*(\d+i?)\b", folded)
    if match:
        return normalize_model(f"ideapad slim {match.group(1)}")
    ssd = _ssd_signature(folded)
    if ssd:
        return ssd
    gpu = _gpu_signature(folded)
    if gpu:
        return gpu
    match = re.search(
        r"\b(?:playstation|ps)\s*5(?:\s*(slim))?(?:\s*(digital|disc|bluray))?\b",
        folded,
    )
    if match:
        parts = ["playstation5"]
        if match.group(1):
            parts.append(match.group(1))
        if match.group(2):
            parts.append(match.group(2))
        else:
            # Edition may appear away from the "PS5" token (e.g. CFI code in between).
            edition = _console_edition_signature(folded)
            if edition:
                parts.append(edition)
        if re.search(r"\bslim\b", folded) and "slim" not in parts:
            parts.insert(1, "slim")
        return normalize_model(" ".join(parts))
    if re.search(r"\bps5\b", folded):
        parts = ["playstation5"]
        if re.search(r"\bslim\b", folded):
            parts.append("slim")
        edition = _console_edition_signature(folded)
        if edition:
            parts.append(edition)
        return normalize_model(" ".join(parts))
    board = _motherboard_board_signature(folded)
    if board:
        return board
    return None


def resolve_model(raw_model: str | None, title: str | None) -> str | None:
    """Prefer commercial/family model strings; keep MPN only when nothing better."""
    blob = _identity_blob(raw_model, title)
    # Discrete GPUs: chip identity (rtx5070 / rtx5070ti) is the model token;
    # cooler lines (Shadow 3X) live in variant_attrs.edition, not model.
    gpu = _gpu_signature(blob)
    if gpu:
        return gpu

    structured = normalize_model(raw_model)
    inferred = infer_model_from_title(title)
    if structured and _model_has_family(structured) and not looks_like_mpn(structured):
        # Generic "PlayStation 5" / "PS5" in structured fields often omits Slim /
        # Digital / Disc. Prefer the title-inferred token when it only *adds*
        # edition/revision detail (never when it invents a different family).
        if inferred:
            base = compact_model(structured)
            rich = compact_model(inferred)
            if rich.startswith(base) and len(rich) > len(base):
                return inferred
        return structured
    if inferred:
        return inferred
    if structured and not looks_like_mpn(structured):
        return structured
    if structured and looks_like_mpn(structured) and inferred:
        return inferred
    return structured or inferred


def console_soft_model_title_exempt(
    reference: ProductIdentity,
    candidate: ProductIdentity,
) -> bool:
    """Title similarity is complementary for consoles — not a soft-model gate.

    Sparse listings (Shopping China) vs marketing-heavy titles (KaBuM / Amazon)
    often score below ``SOFT_MODEL_TITLE_MIN`` even when edition + storage agree.
    Explicit digital≠disc / controller / storage conflicts stay in
    ``critical_identity_conflict``.
    """
    left = compact_model(reference.model or "")
    right = compact_model(candidate.model or "")
    if not left or not right:
        return False
    if not (
        ("playstation5" in left or left.startswith("ps5"))
        and ("playstation5" in right or right.startswith("ps5"))
    ):
        return False
    if not models_compatible(
        reference.model,
        candidate.model,
        left_title=reference.title,
        right_title=candidate.title,
    ):
        return False
    return critical_identity_conflict(reference, candidate) is None


def gpu_soft_model_title_exempt(
    reference: ProductIdentity,
    candidate: ProductIdentity,
) -> bool:
    """Title noise (DLSS / MHz / bus) must not veto GPU chip+edition identity."""
    left = _gpu_signature(_identity_blob(reference.model, reference.title))
    right = _gpu_signature(_identity_blob(candidate.model, candidate.title))
    if not left or not right or left != right:
        return False
    return critical_identity_conflict(reference, candidate) is None


def motherboard_soft_model_title_exempt(
    reference: ProductIdentity,
    candidate: ProductIdentity,
) -> bool:
    """Long SEO titles vs short SERP cards must not veto identical board SKUs."""
    left = _motherboard_board_signature(
        _identity_blob(reference.model, reference.title)
    )
    right = _motherboard_board_signature(
        _identity_blob(candidate.model, candidate.title)
    )
    if not left or not right or left != right:
        return False
    if not models_compatible(
        reference.model,
        candidate.model,
        left_title=reference.title,
        right_title=candidate.title,
    ):
        return False
    return critical_identity_conflict(reference, candidate) is None


def _canonical_color(text: str) -> str:
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text in COLOR_CANONICAL:
        return COLOR_CANONICAL[text]
    words = text.split()
    components: list[str] = []
    for word in words:
        if word in COLOR_CANONICAL:
            components.append(COLOR_CANONICAL[word])
        elif word in _COLOR_DESCRIPTOR_CANONICAL:
            components.append(_COLOR_DESCRIPTOR_CANONICAL[word])
        else:
            components = []
            break
    hues = [word for word in components if word in _COLOR_HUES]
    if len(hues) == 1:
        return " ".join(sorted(components))
    # Legacy finish labels can resolve to a known hue through a complete suffix.
    for index in range(len(words)):
        suffix = " ".join(words[index:])
        if suffix in COLOR_CANONICAL:
            return COLOR_CANONICAL[suffix]
    return text


def normalize_variant_value(key: str, value: str) -> str:
    """Canonicalize variant values so gates compare apples-to-apples."""
    text = fold_text(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text
    if key == "screen_size":
        compacted = re.sub(r"\s+", "", text)
        match = re.fullmatch(
            r"(\d+(?:\.\d+)?)(?:in|inch|inches|pol|polegadas|\"|')?", compacted
        )
        if match:
            return f"{match.group(1)}in"
        return compacted
    if key == "resolution":
        dimensions = re.search(r"(\d{3,5})\s*x\s*(\d{3,5})", text)
        if dimensions:
            return f"{dimensions.group(1)}x{dimensions.group(2)}"
    if key == "refresh_rate":
        return re.sub(r"\s+hz\b", "hz", text)
    if key in {"storage", "capacity", "ram", "size", "vram"}:
        # "256 gb" / "256GB" / "12G" → "256gb" / "12gb"
        compacted = re.sub(r"\s+", "", text)
        match = re.fullmatch(r"(\d+)(gb|tb|mb|g|mm|cm|in|\"|')?", compacted)
        if match:
            unit = match.group(2) or ""
            if unit == "g":
                unit = "gb"
            return f"{match.group(1)}{unit}"
        return compacted
    if key == "edition":
        return canonical_variant_key(text) or re.sub(r"[^a-z0-9]+", "", text)
    if key == "color":
        return _canonical_color(text)
    if key in _ATTRIBUTE_VALUE_ALIASES:
        return _ATTRIBUTE_VALUE_ALIASES[key].get(text, text)
    return text


def _capacity_gb(value: str) -> int | None:
    match = re.fullmatch(r"(\d+)(gb|tb)?", value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2) or "gb"
    if unit == "tb":
        return amount * 1024
    if unit == "gb":
        return amount
    return None


def _storage_like_from_title(title: str | None) -> str | None:
    """Pick the largest SSD/HDD-like capacity mentioned in the title."""
    best: tuple[int, str] | None = None
    for amount_s, unit in re.findall(
        r"\b(\d+)\s*(gb|tb)\b",
        fold_text(title or ""),
        flags=re.I,
    ):
        amount = int(amount_s)
        unit_n = unit.lower()
        gb = amount * 1024 if unit_n == "tb" else amount
        if gb < _STORAGE_LIKE_MIN_GB and unit_n != "tb":
            continue
        token = normalize_variant_value("storage", f"{amount}{unit_n}")
        if best is None or gb > best[0]:
            best = (gb, token)
    return best[1] if best else None


def refine_storage_attrs(attrs: dict[str, str], title: str | None) -> None:
    """Replace RAM-sized 'storage' with SSD capacity inferred from the title."""
    title_storage = _storage_like_from_title(title)
    if not title_storage:
        return
    current = attrs.get("storage")
    if current is None:
        attrs["storage"] = title_storage
        return
    current_gb = _capacity_gb(current)
    if current_gb is not None and current_gb <= _RAM_LIKE_MAX_GB:
        attrs["storage"] = title_storage


def _color_label_from_title(title: str | None) -> str | None:
    """Pick the longest known color label mentioned in the title.

    Prefers multi-word marketing compounds (``titânio preto``) over bare finish
    tokens (``titânio``) so search/gates use the base hue when available.
    """
    folded = re.sub(r"[-_]+", " ", fold_text(title or ""))
    folded = re.sub(r"\s+", " ", folded).strip()
    if not folded:
        return None
    # Longest catalog label first so "verde acinzentado" wins over "verde".
    for label in sorted(COLOR_CANONICAL, key=len, reverse=True):
        if re.search(rf"\b{re.escape(label)}\b", folded):
            return label
    # For translated compounds, find a short span whose terms are all known
    # hue/finish concepts (e.g. "laranja cósmico" or "cosmic orange").
    words = folded.split()
    for width in range(min(3, len(words)), 0, -1):
        for start in range(len(words) - width + 1):
            span = " ".join(words[start : start + width])
            normalized = _canonical_color(span)
            if normalized != span or (
                set(normalized.split()) & _COLOR_HUES
                and all(
                    word in COLOR_CANONICAL or word in _COLOR_DESCRIPTOR_CANONICAL
                    for word in span.split()
                )
            ):
                return span
    return None


# Marketing / SEO tokens safe to drop from last-resort title-window SERP queries.
# Never drop brand/model/capacity tokens — those belong in structured ladders.
_SEARCH_TITLE_NOISE: frozenset[str] = frozenset(
    {
        "placa",
        "video",
        "geforce",
        "nvidia",
        "amd",
        "radeon",
        "ray",
        "tracing",
        "dlss",
        "fp4",
        "mhz",
        "bit",
        "bits",
        "pcie",
        "pci",
        "express",
        # Smartphone PDP noise (cameras, battery, connectivity slogans).
        # Do NOT drop model family tokens (galaxy/iphone) — they are identity.
        "celular",
        "smartphone",
        "telefone",
        "ai",
        "cam",
        "camera",
        "cameras",
        "quadrupla",
        "tripla",
        "bateria",
        "battery",
        "mah",
        "dual",
        "chip",
        "mp",
        "ios",
        "android",
        "tela",
        "display",
        "polegadas",
        "inch",
        "inches",
    }
)


def canonicalize_variant_key(key: str, value: str) -> str:
    """Map locale attribute names onto VARIANT_GATE_KEYS.

    ``tamanho`` / ``size`` values that look like device storage (≥128GB) are
    promoted to ``storage`` so phone/SSD capacities participate in the gate.
    """
    key_n = fold_text(key).replace(" ", "_")
    mapped = _VARIANT_KEY_ALIASES.get(key_n, key_n)
    if mapped == "size":
        norm = normalize_variant_value("storage", value)
        gb = _capacity_gb(norm)
        if gb is not None and gb >= _STORAGE_LIKE_MIN_GB:
            return "storage"
    return mapped


def parse_variant_attributes(
    variant: str | None,
    *,
    specifications: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Parse `color: …; storage: …` variant strings plus optional specs."""
    attrs: dict[str, str] = {}

    def _store(raw_key: str, raw_value: str) -> None:
        text = str(raw_value).strip()
        if not text or text.lower() == "none":
            return
        key_n = canonicalize_variant_key(raw_key, text)
        if key_n not in VARIANT_GATE_KEYS:
            return
        if key_n in attrs:
            return
        if key_n in {"storage", "capacity", "ram", "size"}:
            # Slot counts ("4") without a capacity unit are not RAM identity.
            if key_n == "ram" and re.fullmatch(
                r"\d{1,2}", re.sub(r"\s+", "", fold_text(text))
            ):
                return
            attrs[key_n] = normalize_variant_value(key_n, text)
        else:
            folded = (
                normalize_variant_value(key_n, text)
                if key_n == "network_lock"
                else re.sub(r"\s+", " ", fold_text(text)).strip()
            )
            if folded:
                attrs[key_n] = folded

    if variant:
        for part in variant.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            _store(key, value)
    for source in (specifications or {}, extra or {}):
        for key, value in source.items():
            if value is None:
                continue
            _store(str(key), str(value))
    return {k: v for k, v in attrs.items() if k in VARIANT_GATE_KEYS}


def variants_equal(key: str, left: str, right: str) -> bool:
    """Compare variant values with canonicalization (storage units, color synonyms).

    For color: bare finish tokens (``titânio`` / ``titanium``) are compatible
    with finish+hue (``titânio preto`` → black). That is missing specificity,
    not a real conflict — Amazon titles often omit the hue word.
    """
    left_n = normalize_variant_value(key, left)
    right_n = normalize_variant_value(key, right)
    if left_n == right_n:
        return True
    if key == "color":
        return _colors_compatible(left_n, right_n)
    return False


def variant_comparison_uncertain(key: str, left: str, right: str) -> bool:
    """Return true when an explicit color uses values outside the known lexicon."""
    if key != "color" or variants_equal(key, left, right):
        return False
    return not (_color_hues_from_value(left) and _color_hues_from_value(right))


def _color_hues_from_value(value: str) -> set[str]:
    normalized = normalize_variant_value("color", value)
    return set(normalized.split()) & _COLOR_HUES


_FINISH_ONLY_COLORS = frozenset({"titanium"})
_FINISH_HUE_COLORS = frozenset({"black", "white", "gray"})


def _colors_compatible(left_n: str, right_n: str) -> bool:
    """True when colors are the same family with incomplete evidence on one side."""
    if left_n == right_n:
        return True
    left_words, right_words = set(left_n.split()), set(right_n.split())
    left_hues = left_words & _COLOR_HUES
    right_hues = right_words & _COLOR_HUES
    # Retailers often omit a finish qualifier ("Orange" vs "Cosmic Orange").
    # The explicit base hue must still agree; distinct hues remain a hard gate.
    if left_hues and left_hues == right_hues:
        return True
    pair = {left_n, right_n}
    if pair & _FINISH_ONLY_COLORS and pair & _FINISH_HUE_COLORS:
        return True
    return False


def variant_key(attrs: dict[str, str]) -> str | None:
    if not attrs:
        return None
    return "|".join(f"{k}={attrs[k]}" for k in sorted(attrs))


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    folded = fold_text(title)
    # Normalize a controlled set of translated attribute phrases. This helps
    # lexical evidence without translating or discarding the full title.
    for canonical, aliases in _TITLE_SEMANTIC_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            folded = re.sub(rf"\b{re.escape(alias)}\b", canonical, folded)
    # Compact capacity units so "128 GB" and "128GB" share a token.
    folded = re.sub(r"\b(\d+)\s*(gb|tb|mb)\b", r"\1\2", folded)

    # Keep manufacturer PNs as a single token before punctuation stripping.
    def _compact_mpn(match: re.Match[str]) -> str:
        return normalize_mpn(match.group(0)) or match.group(0)

    for pattern in _MPN_PATTERNS:
        folded = pattern.sub(_compact_mpn, folded)

    # Preserve discriminating board suffixes (B650M-E) before stopword "e"
    # would erase the letter after hyphen→space normalization.
    def _compact_board(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}"

    folded = re.sub(
        r"\b([abzhx]\d{3}m?)\s*-\s*([a-z0-9]+)\b",
        _compact_board,
        folded,
    )
    folded = re.sub(r"[^a-z0-9\s]+", " ", folded)
    tokens = [t for t in folded.split() if t and t not in TITLE_STOPWORDS]
    # Map single-token color synonyms so PT/EN titles overlap.
    tokens = [
        COLOR_CANONICAL.get(token, _COLOR_DESCRIPTOR_CANONICAL.get(token, token))
        for token in tokens
    ]
    return " ".join(tokens)


def title_tokens(title: str | None) -> set[str]:
    return {t for t in normalize_title(title).split() if t}


def token_set_ratio(a: str | None, b: str | None) -> float:
    """Order-insensitive token overlap ratio in [0, 1]."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(len(ta), len(tb))


def looks_like_accessory(title: str | None, *, reference_title: str | None) -> bool:
    """True when candidate title looks like an accessory of the reference product."""
    cand = title_tokens(title)
    ref = title_tokens(reference_title)
    if not cand:
        return False
    has_accessory = bool(cand & ACCESSORY_TOKENS)
    ref_has_accessory = bool(ref & ACCESSORY_TOKENS)
    return has_accessory and not ref_has_accessory


def looks_like_bundle(title: str | None, *, reference_title: str | None) -> bool:
    """True when candidate adds extra merchandise absent from the reference.

    Pack-in games (Astro's Playroom) and marketing tokens (Bluetooth, 8K,
    DualSense singular) are not treated as bundles. Explicit extra games
    (Fortnite, GT7, …) or kit/watch merchandise are.
    """
    cand = fold_text(title or "")
    ref = fold_text(reference_title or "")
    if not cand:
        return False
    cand_bundle = any(marker in cand for marker in _BUNDLE_MARKERS)
    ref_bundle = any(marker in ref for marker in _BUNDLE_MARKERS)
    return cand_bundle and not ref_bundle


_SYSTEM_FORM_TOKENS = frozenset(
    {
        "notebook",
        "laptop",
        "ultrabook",
        "chromebook",
        "minipc",
        "mini pc",
        "all in one",
        "all-in-one",
    }
)


def looks_like_computing_system(title: str | None) -> bool:
    """True for notebooks/laptops/desktops that embed a GPU as a component."""
    folded = fold_text(title or "")
    if not folded:
        return False
    if any(token in folded for token in _SYSTEM_FORM_TOKENS):
        return True
    # "GAMING A16 … Intel Core i7 … 32GB DDR5 … SSD … GeForce RTX 5060"
    has_cpu = bool(
        re.search(
            r"\b(intel\s+core|core\s*i[3579]|ryzen\s*[3579]|i[3579]-\d{4,})\b",
            folded,
        )
    )
    has_system_mem = bool(
        re.search(r"\b\d+\s*gb\s*(ddr[45]|ram)\b", folded)
        or re.search(r"\bssd\b", folded)
    )
    has_display = bool(re.search(r"\b\d{2}\s*(inch|hz|wuxga|fhd|qhd)\b", folded))
    return has_cpu and has_system_mem and (has_display or "gaming a" in folded)


def looks_like_discrete_gpu(title: str | None) -> bool:
    """True for add-in-board / graphics-card listings (not systems with a GPU)."""
    if looks_like_computing_system(title):
        return False
    folded = fold_text(title or "")
    if not folded:
        return False
    if ("placa" in folded and "video" in folded) or "tarjeta de video" in folded:
        return True
    if "graphics card" in folded or "placa grafica" in folded:
        return True
    if not _gpu_signature(folded):
        return False
    # Chip + cooler/VRAM marketing without notebook markers → discrete card.
    return any(
        marker in folded
        for marker in (
            "gddr",
            "windforce",
            "gaming oc",
            "eagle",
            "aero",
            "dual oc",
            "shadow",
            "ventus",
            "tuf",
            "aorus",
        )
    ) or ("geforce" in folded or "radeon" in folded)


def form_factor_conflict(
    reference_title: str | None,
    candidate_title: str | None,
) -> str | None:
    """Reject notebook/system listings when the reference is a discrete GPU."""
    if looks_like_discrete_gpu(reference_title) and looks_like_computing_system(
        candidate_title
    ):
        return "form_factor_mismatch:gpu!=system"
    if looks_like_computing_system(reference_title) and looks_like_discrete_gpu(
        candidate_title
    ):
        return "form_factor_mismatch:system!=gpu"
    return None


def looks_like_used_condition(title: str | None) -> bool:
    """True when the listing title indicates refurbished / used / CPO stock."""
    folded = fold_text(title or "")
    if not folded:
        return False
    for token in _CONDITION_TOKENS:
        if " " in token or "-" in token:
            if token in folded:
                return True
        elif re.search(rf"\b{re.escape(token)}\b", folded):
            return True
    return False


def _network_lock_from_title(title: str | None) -> str | None:
    folded = fold_text(title or "")
    if re.search(
        r"\b(?:fully\s+)?unlocked\b|\b(?:desbloquead[oa]|liberad[oa])\b", folded
    ):
        return "unlocked"
    if re.search(r"\b(?:locked|bloquead[oa])\b", folded):
        return "locked"
    return None


def condition_conflict(
    reference_title: str | None,
    candidate_title: str | None,
) -> str | None:
    """Reject used/refurbished candidates when the reference is a new listing."""
    if looks_like_used_condition(candidate_title) and not looks_like_used_condition(
        reference_title
    ):
        return "condition_mismatch:new!=used"
    return None


@dataclass(frozen=True)
class ProductIdentity:
    gtin: str | None
    brand: str | None
    model: str | None
    title: str
    title_normalized: str
    variant_attrs: dict[str, str] = field(default_factory=dict)
    store: str | None = None
    product_id: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    mpn: str | None = None
    mpn_display: str | None = None
    mpn_aliases: frozenset[str] = field(default_factory=frozenset)
    model_numbers: frozenset[str] = field(default_factory=frozenset)
    monitor_model_code: str | None = None
    category: str | None = None

    @property
    def variant_key(self) -> str | None:
        return variant_key(self.variant_attrs)


def identity_from_price_item(item: ProductPriceItem) -> ProductIdentity:
    meta = item.metadata if isinstance(item.metadata, dict) else {}
    raw_specs = meta.get("specifications")
    specs: dict[str, Any] = raw_specs if isinstance(raw_specs, dict) else {}
    extra: dict[str, Any] = {}
    for key in (
        "color",
        "storage",
        "size",
        "capacity",
        "ram",
        "vram",
        "edition",
        "network_lock",
    ):
        value = meta.get(key)
        if (
            value is not None
            and str(value).strip()
            and str(value).strip().lower() != "none"
        ):
            extra[key] = value
    # Bare Magalu-style variant ("Preto") → treat as color when no key:value form.
    # Wi-Fi / wireless labels are connectivity attributes, never color gates.
    variant = item.variant
    if variant and ":" not in variant:
        if _is_wifi_variant_label(variant):
            pass
        else:
            gpu_blob = _identity_blob(item.model, item.title)
            if extract_gpu_chip(gpu_blob) is not None or extract_gpu_chip(variant):
                extra.setdefault("edition", variant)
            elif "color" not in extra:
                extra["color"] = variant
    # Pull storage tokens from title when structured variant lacks them.
    attrs = parse_variant_attributes(
        variant if variant and ":" in variant else None,
        specifications=specs,
        extra=extra,
    )
    refine_storage_attrs(attrs, item.title)
    if "color" not in attrs:
        title_color = _color_label_from_title(item.title)
        if title_color:
            attrs["color"] = title_color
    if "network_lock" not in attrs:
        network_lock = _network_lock_from_title(item.title)
        if network_lock:
            attrs["network_lock"] = network_lock

    blob = _identity_blob(item.model, item.title)
    # GPU VRAM is often mis-tagged as RAM by generic parsers — promote when GPU.
    if _gpu_signature(blob):
        if "vram" not in attrs:
            vram = (
                attrs.pop("ram", None)
                if attrs.get("ram")
                else _gpu_vram_from_text(blob)
            )
            if vram:
                attrs["vram"] = normalize_variant_value("vram", str(vram))
        elif "ram" in attrs and normalize_variant_value(
            "ram", str(attrs["ram"])
        ) == normalize_variant_value("vram", str(attrs["vram"])):
            attrs.pop("ram", None)
        if "edition" not in attrs:
            edition = _gpu_edition_signature(item.title) or _gpu_edition_signature(
                " ".join(str(v) for v in specs.values() if v)
            )
            if edition:
                attrs["edition"] = normalize_variant_value("edition", edition)
        memory_type = _memory_type_signature(blob) or _memory_type_signature(
            " ".join(str(v) for v in specs.values() if v)
        )
        if memory_type and "memory_type" not in attrs:
            attrs["memory_type"] = memory_type

    category = meta.get("category")
    if not category:
        from scout_api.modules.crawler.utils.product_attributes import (
            detect_product_category,
        )

        category = detect_product_category(item.title)
    category = str(category).casefold() if category else None
    monitor_model_code = None
    model = resolve_model(item.model, item.title)
    if category == "monitor":
        if attrs.get("size"):
            attrs["size"] = normalize_variant_value("screen_size", attrs["size"])
        from scout_api.modules.crawler.utils.product_attributes import (
            resolve_product_identity,
        )

        monitor_attributes = resolve_product_identity(
            specifications=specs,
            title=item.title,
            attributes=("screen_size", "resolution", "refresh_rate", "panel"),
            category="monitor",
        )
        for key in ("screen_size", "resolution", "refresh_rate", "panel"):
            value = monitor_attributes.value(key)
            if value:
                attrs[key] = normalize_variant_value(key, value)
        structured_monitor_code = bool(
            item.model
            and parse_title_identity(item.model, category="monitor").confidence
            == "exact_title"
        )
        for text in (item.title, item.model):
            parsed = parse_title_identity(text, category="monitor")
            if parsed.product_line and (not item.model or structured_monitor_code):
                model = normalize_model(parsed.product_line)
            elif (
                parsed.confidence == "contextual"
                and parsed.model
                and (not item.model or structured_monitor_code)
            ):
                model = normalize_model(parsed.model)
            if parsed.confidence == "exact_title" and parsed.model:
                monitor_model_code = re.sub(r"\s+", "", parsed.model).upper()
                break
    # Spec REFERÊNCIA / manufacturer codes feed MPN extraction alongside sku/title.
    spec_mpn_bits = [
        str(specs[key])
        for key in specs
        if fold_text(str(key))
        in {
            "referencia",
            "reference",
            "mpn",
            "part number",
            "codigo do fabricante",
            "manufacturer code",
        }
    ]
    mpn_forms = extract_all_mpn_forms(item.model, item.sku, item.title, *spec_mpn_bits)
    mpn = mpn_forms[0][0] if mpn_forms else None
    mpn_display = mpn_forms[0][1] if mpn_forms else None
    mpn_aliases = frozenset(norm for norm, _disp in mpn_forms)
    model_number_values: set[str] = set()
    for key, value in specs.items():
        if fold_text(str(key)) in {
            "model number",
            "numero do modelo",
            "numero de modelo",
            "manufacturer model number",
        }:
            model_number_values.add(normalize_model(str(value)) or "")
    if _phone_signature(_identity_blob(item.model, item.title)):
        model_number_values.update(
            match.group(0).lower()
            for match in re.finditer(r"\bA\d{4}\b", item.title or "", re.I)
        )
    model_number_values.discard("")
    brand = normalize_brand(item.brand)
    if brand is None:
        # Title often starts with the real brand when PDP brand is a placeholder.
        title_brand = fold_text((item.title or "").split(" ")[0] if item.title else "")
        if title_brand and title_brand not in {"iphone", "galaxy", "smartphone"}:
            brand = normalize_brand(title_brand)
        if brand is None and "iphone" in fold_text(item.title or ""):
            brand = "apple"
    return ProductIdentity(
        gtin=normalize_gtin(item.gtin),
        brand=brand,
        model=model,
        title=item.title,
        title_normalized=normalize_title(item.title),
        variant_attrs=attrs,
        store=item.store,
        product_id=item.product_id,
        price=item.price,
        currency=item.currency,
        mpn=mpn,
        mpn_display=mpn_display,
        mpn_aliases=mpn_aliases,
        model_numbers=frozenset(model_number_values),
        monitor_model_code=monitor_model_code,
        category=category,
    )


def identity_reference_item(
    title: str,
    *,
    brand: str | None = None,
    model: str | None = None,
    variant: str | None = None,
    category: str | None = None,
) -> ProductPriceItem:
    """Build a synthetic reference listing from product identity only.

    Used for Search+Match discovery without feeding known store URLs/IDs.
    Placeholder commercial fields satisfy ``ProductPriceItem`` validation;
    ``ProductMatchService.match_from_item`` clears price before scoring.
    """
    from datetime import UTC, datetime

    from scout_api.modules.crawler.utils.product_attributes import (
        resolve_product_identity,
    )

    bundle = resolve_product_identity(title=title, category=category)
    resolved_brand = brand or bundle.value("brand")
    resolved_model = model or bundle.value("model")
    resolved_variant = variant
    if resolved_variant is None:
        edition = bundle.value("edition")
        if edition:
            resolved_variant = str(edition)
    slug = fold_text(
        " ".join(p for p in (resolved_brand, resolved_model, resolved_variant) if p)
        or title
    )
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "identity"
    meta: dict[str, Any] = {"identity_only": True, "source": "identity-reference"}
    if category:
        meta["category"] = category
    return ProductPriceItem.model_validate(
        {
            "store": "synthetic",
            "country": "BR",
            "product_id": f"identity:{slug}",
            "url": f"scout://identity/{slug}",
            "canonical_url": f"scout://identity/{slug}",
            "title": title,
            "brand": resolved_brand,
            "model": resolved_model,
            "variant": resolved_variant,
            "currency": "BRL",
            # Schema requires price > 0; match_from_item clears it for scoring.
            "price": Decimal("1.00"),
            "scraped_at": datetime.now(UTC),
            "metadata": meta,
        }
    )


# Prefer color/storage queries before the brand+series-only drop so Amazon
# SERPs that promote sibling colors still surface the right card early.
def build_search_queries(
    identity: ProductIdentity, *, locale: str | None = None
) -> list[str]:
    """Ordered SERP queries: GTIN → display MPN → spaced series → title tokens.

    Compacted identity tokens (``990evoplus``, ``mzv9s1t0bam``) are weak on
    Amazon-like SERPs; prefer hyphenated PNs and human-spaced model phrases.
    Color queries prefer the integration locale while retaining cross-locale
    aliases as fallbacks; technical identifiers are never localized.

    For discrete GPUs, prefer progressive commercial queries
    (brand + chip + edition + VRAM) before noisy SEO title prefixes.
    """
    queries: list[str] = []

    def add(raw: str | None) -> None:
        text = (raw or "").strip()
        if text and text not in queries:
            queries.append(text)

    def insert_category_context(
        parts: tuple[str | None, ...] | list[str | None],
    ) -> None:
        category_term = _localized_category_search_term(identity.category, locale)
        base = " ".join(part for part in parts if part)
        if not category_term or not base:
            return
        localized_query = f"{base} {category_term}"
        if localized_query in queries:
            return
        # Keep one category-aware candidate query close to the strong core
        # queries while leaving identifier and model-only forms available.
        index = 2 if identity.gtin else 1
        queries.insert(min(index, len(queries)), localized_query)

    add(identity.gtin)

    # CPU catalogs often normalize the model field to a glued token (e.g.
    # ``ryzen75800x3d``), while store search indexes natural model phrases.
    # Keep query generation category-specific and retain the full SKU suffix.
    cpu_model = _cpu_signature(_identity_blob(identity.model, identity.title))
    if cpu_model and identity.category == "cpu":
        cpu_phrase = re.sub(
            r"^(ryzen|coreultra|core)([3579])[- ]", r"\1 \2 ", cpu_model
        )
        cpu_phrase = re.sub(r"^i([3579])-", r"Core i\1-", cpu_phrase)
        cpu_phrase = re.sub(r"\s+", " ", cpu_phrase).strip()
        if identity.brand:
            add(f"{identity.brand} {cpu_phrase}")
        add(cpu_phrase)

    # Collect manufacturer PN displays for later (after category-specific ladders).
    alias_displays: list[str] = []
    if identity.mpn_display:
        alias_displays.append(identity.mpn_display)
    for _norm, display in extract_all_mpn_forms(
        identity.mpn_display,
        identity.mpn,
        identity.model,
        identity.title,
        *identity.mpn_aliases,
    ):
        if display not in alias_displays:
            alias_displays.append(display)

    # Monitor model codes are often extracted into their dedicated field while
    # generic identity resolution reduces ``model`` to the product family.
    # Keep the exact display code near the top of SERP discovery in that case.
    if identity.category == "monitor" and identity.monitor_model_code:
        monitor_code = fold_text(identity.monitor_model_code)
        if monitor_code:
            if identity.brand:
                add(f"{identity.brand} {monitor_code}")
            add(monitor_code)
    elif identity.category == "monitor":
        parsed_monitor = parse_title_identity(identity.title, category="monitor")
        monitor_family = parsed_monitor.product_line or parsed_monitor.model
        if monitor_family:
            monitor_family = monitor_family.casefold().strip()
            family_parts = [
                part
                for part in (
                    identity.brand,
                    monitor_family,
                    identity.variant_attrs.get("screen_size"),
                    identity.variant_attrs.get("refresh_rate"),
                )
                if part
            ]
            if identity.brand:
                add(f"{identity.brand} {monitor_family}")
            add(" ".join(family_parts))

    series = model_search_phrase(model=identity.model, title=identity.title)
    storage = identity.variant_attrs.get("storage") or identity.variant_attrs.get(
        "capacity"
    )
    vram = identity.variant_attrs.get("vram")
    color = identity.variant_attrs.get("color")
    edition = _edition_search_phrase(identity)
    memory_type = identity.variant_attrs.get("memory_type") or _memory_type_signature(
        _identity_blob(identity.model, identity.title)
    )
    gpu = _gpu_signature(_identity_blob(identity.model, identity.title))
    console_edition = _console_edition_signature(
        _identity_blob(identity.model, identity.title)
    )
    series_fold = fold_text(series or "")
    if console_edition and console_edition in series_fold:
        console_edition = None

    # --- GPU progressive ladder (discriminating attrs first, noise last) ---
    # Prefer commercial identity before bare board/marketing PNs: BR SERPs often
    # surface marketplace siblings for opaque codes while Shadow/VRAM queries
    # rediscover the exact cooler line.
    if gpu:
        spaced_gpu = re.sub(
            r"(rtx|gtx|rx)(\d{3,4})(ti|super|xt)?", r"\1 \2 \3", gpu
        ).strip()
        spaced_gpu = re.sub(r"\s+", " ", spaced_gpu).strip()
        capacity = vram or storage
        ladder: list[list[str]] = []
        core = [p for p in (identity.brand, spaced_gpu) if p]
        if core:
            add(" ".join(core))
        # Preserve a manufacturer's opaque part number early enough to fit
        # within the per-store search budget after a GTIN/core query.
        for display in alias_displays:
            add(display)
        full = [
            p for p in (identity.brand, spaced_gpu, edition, capacity, memory_type) if p
        ]
        ladder.append(full)
        ladder.append([p for p in (identity.brand, spaced_gpu, edition, capacity) if p])
        ladder.append([p for p in (identity.brand, spaced_gpu, edition) if p])
        ladder.append([p for p in (identity.brand, spaced_gpu, capacity) if p])
        ladder.append([p for p in (identity.brand, spaced_gpu) if p])
        if edition and capacity:
            ladder.append([p for p in (spaced_gpu, edition, capacity) if p])
        for parts in ladder:
            if parts:
                add(" ".join(parts))
        category_term = _localized_category_search_term(identity.category, locale)
        if category_term and full:
            category_query = " ".join((*full, category_term))
            # The localized query must land inside the five-query store budget.
            if category_query not in queries:
                queries.insert(
                    min(4 if identity.gtin else 3, len(queries)), category_query
                )
        if capacity:
            add(" ".join(p for p in (identity.brand, spaced_gpu, capacity) if p))
        if identity.mpn:
            add(identity.mpn)
        return queries

    # --- Motherboard progressive ladder (board code first, SEO noise last) ---
    board_phrase = _motherboard_board_search_phrase(
        _identity_blob(identity.model, identity.title)
    )
    board_sig = _motherboard_board_signature(
        _identity_blob(identity.model, identity.title)
    )
    if board_phrase or board_sig:
        family = _motherboard_family_phrase(
            _identity_blob(identity.model, identity.title)
        )
        wifi = "wifi" if _wifi_presence(identity.title) else None
        # Spaced board token for SERP (prefer hyphenated form from phrase).
        spaced_board = None
        if board_phrase:
            # Drop family/wifi from phrase to get bare board when needed.
            spaced_board = board_phrase
            for drop in filter(None, (family, wifi)):
                spaced_board = re.sub(
                    rf"\b{re.escape(drop)}\b",
                    "",
                    spaced_board,
                    flags=re.I,
                )
            spaced_board = re.sub(r"\s+", " ", spaced_board).strip() or board_phrase
        elif board_sig:
            spaced_board = board_sig
        for display in alias_displays:
            add(display)
            if identity.brand:
                add(f"{identity.brand} {display}")
        ladder_mb: list[list[str]] = []
        ladder_mb.append([p for p in (identity.brand, family, spaced_board, wifi) if p])
        ladder_mb.append([p for p in (identity.brand, spaced_board, wifi) if p])
        ladder_mb.append([p for p in (family, spaced_board, wifi) if p])
        ladder_mb.append([p for p in (spaced_board, wifi) if p])
        if spaced_board:
            ladder_mb.append([spaced_board])
        for parts in ladder_mb:
            if parts:
                add(" ".join(parts))
        insert_category_context((identity.brand, family, spaced_board, wifi))
        if identity.mpn:
            add(identity.mpn)
            if identity.brand:
                add(f"{identity.brand} {identity.mpn}")
        return queries

    # Bare manufacturer PN ranks best on Amazon BR for exact SKU recovery
    # (non-GPU / non-motherboard categories).
    for display in alias_displays:
        add(display)
        if identity.brand:
            add(f"{identity.brand} {display}")

    # Colorless series+storage first: locale color tokens (branco/black) often
    # miss on foreign SERPs (Shopping China) even when the SKU is present.
    # Magento (Nissei) often ranks family tokens better *without* capacity —
    # emit brand+series early so SEARCH stays broad before MATCH confirms
    # storage on the PDP.
    if identity.brand and series:
        add(f"{identity.brand} {series}")
    if series:
        add(series)

    series_parts = [part for part in (identity.brand, series) if part]
    capacity = storage or vram
    if capacity:
        series_parts.append(capacity)
    if console_edition:
        series_parts.append(console_edition)
    if edition and not gpu:
        series_parts.append(str(edition))
    add(" ".join(series_parts) if series_parts else None)

    # Brandless series+storage early — some catalogs (Shopping China) rank
    # better without the brand token and empty out on brand+slim combos.
    if series and capacity:
        add(f"{series} {capacity}")
    elif series and console_edition:
        add(f"{series} {console_edition}")

    color_labels: list[str] = []
    if color:
        canon = normalize_variant_value("color", color)
        hue = next((word for word in canon.split() if word in _COLOR_HUES), canon)
        language = (locale or "").replace("_", "-").split("-", 1)[0].lower()
        preferred = _COLOR_SEARCH_LOCALE_LABELS.get(hue, {}).get(language)
        if preferred:
            color_labels.append(preferred)
        if color not in color_labels:
            color_labels.append(color)
        synonyms = _COLOR_SEARCH_SYNONYMS.get(canon, ()) + _COLOR_SEARCH_SYNONYMS.get(
            hue, (canon,)
        )
        for synonym in synonyms:
            if synonym not in color_labels:
                color_labels.append(synonym)

    for color_label in color_labels:
        colored = [part for part in (identity.brand, series) if part]
        if capacity:
            colored.append(capacity)
        if console_edition:
            colored.append(console_edition)
        colored.append(color_label)
        add(" ".join(colored))

    # Progressive drop: keep critical model+storage without brand/color.
    if series and capacity:
        add(f"{series} {capacity}")
    if series and console_edition:
        add(f"{series} {console_edition}")
    if identity.brand and series:
        add(f"{identity.brand} {series}")

    # Compacted fallbacks for catalogs that index alnum-only PNs.
    if identity.mpn:
        add(identity.mpn)
        if identity.brand:
            add(f"{identity.brand} {identity.mpn}")

    tokens = identity.title_normalized.split()
    if tokens:
        # Drop marketing/noise tokens before taking a title window.
        filtered = [tok for tok in tokens if tok not in _SEARCH_TITLE_NOISE]
        window = filtered[:8] if filtered else tokens[:8]
        add(" ".join(window))
    if series:
        insert_category_context((identity.brand, series))
    return queries
