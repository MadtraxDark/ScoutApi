"""Generic product-attribute resolution: selected variant first, URL last.

Priority per attribute:

1. selected variant / selected SKU structured state
2. explicit specifications
3. structured product fields (JSON-LD, etc.)
4. conservative, category-aware title inference
5. URL/slug supporting evidence
6. null

Higher-priority sources never get overwritten by lower ones. Identifiers
(GTIN/EAN/UPC/SKU/product_id) are never inferred from arbitrary title numbers.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .product_identity import (
    canonicalize_model_display,
    looks_like_base_model,
    looks_like_opaque_code,
    parse_title_identity,
)

SOURCE_SELECTED_VARIANT = "selected-variant"
SOURCE_SPECIFICATIONS = "product-specifications"
SOURCE_STRUCTURED = "structured-data"
SOURCE_TITLE = "product-title-fallback"
SOURCE_URL_SLUG = "url-slug-evidence"
SOURCE_NOT_FOUND = "not-found"

# Descriptive attributes safe for title fallback (never identifiers).
TITLE_SAFE_ATTRIBUTES = frozenset(
    {
        "brand",
        "model",
        "color",
        "storage",
        "ram",
        "vram",
        "size",
        "capacity",
        "screen_size",
        "voltage",
        "processor",
        "connectivity",
        "memory_type",
        "frequency",
        "cas_latency",
        "module_count",
        "module_capacity",
        "chipset",
        "socket",
        "wifi",
        "cores",
        "threads",
        "gpu_model",
        "wattage",
        "efficiency",
        "modularity",
        "form_factor",
        "interface",
        "pcie_generation",
        "refresh_rate",
        "resolution",
        "panel",
        "cooler_type",
        "radiator_size",
        "overclocked",
        "rpm",
        "edition",
        # Phase 4–7 profile extractors
        "pack_count",
        "port_count",
        "port_speed",
        "poe",
        "managed",
        "bluetooth",
        "fps",
        "channels",
        "power",
        "case_size",
        "mount",
        "kit",
        "focal_length",
        "bay_count",
        "included_storage",
        "va",
        "outlet_count",
        "usb_pd",
        "thunderbolt",
        "usb_generation",
        "connector_a",
        "connector_b",
        "length",
        "standard",
        "ethernet_category",
        "vesa",
        "arm_count",
        "fan_size",
        "fan_count",
        "switch_type",
        "layout",
        "platform",
        "width",
        "depth",
    }
)

DEFAULT_IDENTITY_ATTRIBUTES: tuple[str, ...] = (
    "brand",
    "model",
    "color",
    "storage",
    "ram",
    "vram",
    "size",
    "capacity",
    "screen_size",
    "voltage",
    "processor",
    "connectivity",
    "memory_type",
    "frequency",
    "cas_latency",
    "module_count",
    "module_capacity",
    "chipset",
    "socket",
    "wifi",
    "cores",
    "threads",
    "gpu_model",
    "wattage",
    "efficiency",
    "modularity",
    "form_factor",
    "interface",
    "pcie_generation",
    "refresh_rate",
    "resolution",
    "panel",
    "cooler_type",
    "radiator_size",
    "overclocked",
    "rpm",
    "edition",
    "pack_count",
    "port_count",
    "port_speed",
    "poe",
    "managed",
    "bluetooth",
    "fps",
    "channels",
    "power",
    "case_size",
    "mount",
    "kit",
    "focal_length",
    "bay_count",
    "included_storage",
    "va",
    "outlet_count",
    "usb_pd",
    "thunderbolt",
    "usb_generation",
    "connector_a",
    "connector_b",
    "length",
    "standard",
    "ethernet_category",
    "vesa",
    "arm_count",
    "fan_size",
    "fan_count",
    "switch_type",
    "layout",
    "platform",
    "width",
    "depth",
)

_SPEC_ALIASES: dict[str, frozenset[str]] = {
    "brand": frozenset({"brand", "marca", "manufacturer", "fabricante"}),
    "model": frozenset({"model", "modelo", "model number", "mpn"}),
    "color": frozenset({"color", "colour", "cor", "colore"}),
    "storage": frozenset(
        {
            "storage",
            "armazenamento",
            "almacenamiento",
            "capacidade",
            "capacidad",
            "built-in storage",
            "internal storage",
            "memoria interna",
            "memória interna",
            "internal memory",
            "ssd",
            "hdd",
            "disco",
        }
    ),
    "ram": frozenset(
        {
            "ram",
            "memoria ram",
            "memória ram",
            "memory",
            "memoria",
            "memória",
            "system memory",
        }
    ),
    "vram": frozenset(
        {"vram", "video memory", "memoria de video", "memória de vídeo", "gddr"}
    ),
    "size": frozenset(
        {
            "size",
            "tamanho",
            "tamaño",
            "volumen",
            "volume",
            "contenido",
            "conteudo",
            "conteúdo",
        }
    ),
    "capacity": frozenset(
        {"capacity", "capacidade", "capacidad", "contenido", "conteudo", "conteúdo"}
    ),
    "screen_size": frozenset(
        {
            "screen size",
            "screen",
            "display",
            "tela",
            "pantalla",
            "tamanho da tela",
            "tamaño de pantalla",
        }
    ),
    "voltage": frozenset({"voltage", "voltagem", "voltaje", "tensão", "tensao"}),
    "processor": frozenset(
        {"processor", "processador", "procesador", "cpu", "chip", "chipset cpu"}
    ),
    "connectivity": frozenset({"connectivity", "conectividade", "network", "rede"}),
    "memory_type": frozenset(
        {"memory type", "tipo de memoria", "tipo de memória", "memoria", "ddr"}
    ),
    "frequency": frozenset({"frequency", "frequencia", "frequência", "speed", "clock"}),
    "cas_latency": frozenset({"cas", "cas latency", "latency", "cl", "latencia"}),
    "module_count": frozenset({"modules", "module count", "modulos", "módulos"}),
    "module_capacity": frozenset(
        {"module capacity", "capacidade por modulo", "por modulo"}
    ),
    "chipset": frozenset({"chipset"}),
    "socket": frozenset({"socket", "soquete"}),
    "wifi": frozenset({"wifi", "wi-fi", "wireless"}),
    "cores": frozenset({"cores", "nucleos", "núcleos"}),
    "threads": frozenset({"threads", "hilos", "threads cpu"}),
    "gpu_model": frozenset({"gpu", "graphics", "placa de video", "video card"}),
    "wattage": frozenset({"wattage", "potencia", "potência", "watts", "power"}),
    "efficiency": frozenset({"efficiency", "certificacao", "certificação", "80 plus"}),
    "modularity": frozenset({"modularity", "modular", "modularidade"}),
    "form_factor": frozenset({"form factor", "formato", "factor de forma"}),
    "interface": frozenset(
        {"interface", "protocol", "protocolo", "conexao", "conexão"}
    ),
    "pcie_generation": frozenset({"pcie", "pci express", "pcie generation"}),
    "refresh_rate": frozenset({"refresh rate", "taxa de atualizacao", "refresh"}),
    "resolution": frozenset({"resolution", "resolucao", "resolução"}),
    "panel": frozenset({"panel", "painel", "panel type"}),
    "cooler_type": frozenset({"cooler type", "tipo de cooler", "cooling"}),
    "radiator_size": frozenset({"radiator", "radiador"}),
    "overclocked": frozenset({"overclock", "oc"}),
    "rpm": frozenset({"rpm", "rotacao", "rotação"}),
    "edition": frozenset(
        {
            "edition",
            "edicao",
            "edição",
            "product line",
            "linha do produto",
            "cooler line",
        }
    ),
}

_CATEGORY_PREFIXES = frozenset(
    {
        "celular",
        "telefone",
        "smartphone",
        "notebook",
        "laptop",
        "ultrabook",
        "tablet",
        "perfume",
        "kit",
        "placa",
        "gpu",
        "placa-mae",
        "placa-mãe",
        "motherboard",
        "video",
        "vídeo",
        "fone",
        "headset",
        "console",
        "camera",
        "câmera",
        "camara",
        "tv",
        "monitor",
        "mouse",
        "teclado",
        "liquidificador",
        "air",
        "fryer",
        "depilador",
        "smartwatch",
        "relogio",
        "relógio",
        "cabo",
        "capa",
        "case",
        "fonte",
        "memoria",
        "memória",
        "processador",
        "cooler",
        "water",
        "ssd",
        "hdd",
        "nvme",
    }
)

# Color names that also appear in non-color technical phrases (e.g. 80 Plus Gold).
_AMBIGUOUS_COLOR_TERMS = frozenset(
    {
        "gold",
        "silver",
        "titanium",
        "platinum",
        "bronze",
        "dourado",
        "prateado",
        "dorado",
    }
)

_COLOR_TERMS = frozenset(
    {
        "black",
        "white",
        "blue",
        "red",
        "green",
        "gold",
        "silver",
        "pink",
        "purple",
        "yellow",
        "orange",
        "gray",
        "grey",
        "brown",
        "beige",
        "navy",
        "midnight",
        "starlight",
        "graphite",
        "titanium",
        "natural",
        "cream",
        "lavender",
        "lilac",
        "coral",
        "teal",
        "ivory",
        "champagne",
        "spacegray",
        "spacegrey",
        "preto",
        "branco",
        "azul",
        "vermelho",
        "verde",
        "dourado",
        "prateado",
        "rosa",
        "roxo",
        "amarelo",
        "laranja",
        "cinza",
        "marrom",
        "titânio",
        "titanio",
        "lavanda",
        "lilas",
        "lilás",
        "negro",
        "blanco",
        "rojo",
        "amarillo",
        "gris",
        "plateado",
        "dorado",
    }
)

_STOP_MODEL_TOKENS = (
    frozenset(
        {
            "sim",
            "dual",
            "esim",
            "5g",
            "4g",
            "wifi",
            "wi-fi",
            "unlocked",
            "libre",
            "edition",
            "edicao",
            "edición",
            "digital",
            "com",
            "with",
            "para",
            "de",
            "e",
            "and",
            "y",
            "oc",
            "rgb",
            "argb",
        }
    )
    | _COLOR_TERMS
)

_CAPACITY = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>GB|TB|MB)\b",
    re.IGNORECASE,
)
_KIT = re.compile(
    r"\((?P<count>\d+)\s*[x×]\s*(?P<each>\d+)\s*(?P<unit>GB|TB)\)",
    re.IGNORECASE,
)
_VOLTAGE = re.compile(r"\b(?P<volts>\d{2,3})\s*V(?:olts?)?\b", re.IGNORECASE)
_SCREEN = re.compile(
    r"\b(?P<size>\d{1,2}(?:[.,]\d)?)\s*"
    r"(?:\"|''|″|pol\.?|polegadas?|inch(?:es)?|pulgadas?)",
    re.IGNORECASE,
)
# Bare diagonal only when immediately followed by display tokens (category-gated).
_SCREEN_CONTEXT = re.compile(
    r"\b(?P<size>\d{1,2}(?:[.,]\d)?)\s+"
    r"(?=(?:QHD|UHD|FHD|Full\s*HD|HD|4K|8K|IPS|VA|TN|OLED|QLED|Mini-?LED|"
    r"\d{2,3}\s*Hz))",
    re.IGNORECASE,
)
_RAM_MARKER = re.compile(r"\b(?:RAM|DDR[345]|LPDDR[345]X?)\b", re.IGNORECASE)
_VRAM_MARKER = re.compile(r"\b(?:VRAM|GDDR\d*|HBM\d*)\b", re.IGNORECASE)
_STORAGE_MARKER = re.compile(
    r"\b(?:SSD|HDD|NVMe|eMMC|UFS|storage|armazenamento|almacenamiento)\b",
    re.IGNORECASE,
)
_MEMORY_TYPE = re.compile(
    r"\b(?P<type>LPDDR[345]X?|DDR[345]|GDDR\d+|HBM\d*)\b",
    re.IGNORECASE,
)
_FREQUENCY = re.compile(r"\b(?P<hz>\d{3,5})\s*(?:MHz|MT/?s)\b", re.IGNORECASE)
_CAS = re.compile(r"\bCL\s*(?P<cl>\d{1,2})\b", re.IGNORECASE)
_CONNECTIVITY = re.compile(
    r"\b(?P<net>5G|4G|Wi-?Fi\s*[67]?|Bluetooth)\b", re.IGNORECASE
)
_SOCKET = re.compile(
    r"\b(?P<socket>AM[45]|s?TRX4|TR4|LGA\s?17\d{2}|LGA\s?12\d{2}|LGA\s?1151)\b",
    re.IGNORECASE,
)
_CHIPSET = re.compile(
    r"\b(?P<chipset>[ABZHX](?:650|670|690|790|870|610|510|550|570)M?(?:-Plus)?)\b",
    re.IGNORECASE,
)
_WIFI = re.compile(r"\bWi-?Fi\b", re.IGNORECASE)
_CORES = re.compile(r"\b(?P<cores>\d{1,2})\s*[- ]?Cores?\b", re.IGNORECASE)
_THREADS = re.compile(r"\b(?P<threads>\d{1,2})\s*[- ]?Threads?\b", re.IGNORECASE)
_GPU = re.compile(
    r"\b(?P<gpu>(?:GeForce\s+)?RTX\s*\d{3,4}\s*(?:Ti|SUPER)?"
    r"|(?:Radeon\s+)?RX\s*\d{3,4}\s*(?:XT)?)\b",
    re.IGNORECASE,
)
_WATTAGE = re.compile(r"\b(?P<watts>\d{3,4})\s*W(?:att)?s?\b", re.IGNORECASE)
_EFFICIENCY = re.compile(
    r"\b80\s*Plus\s+(?P<tier>Titanium|Platinum|Gold|Silver|Bronze)\b",
    re.IGNORECASE,
)
_MODULAR = re.compile(
    r"\b(?P<mod>Full(?:y)?\s+Modular|Semi[\s-]?Modular|Non[\s-]?Modular)\b",
    re.IGNORECASE,
)
_FORM_FACTOR = re.compile(
    r"\b(?P<form>M\.2|mATX|ATX|ITX|E-ATX|SODIMM|DIMM|2\.5\"|3\.5\")\b",
    re.IGNORECASE,
)
_INTERFACE = re.compile(
    r"\b(?P<iface>NVMe|SATA(?:\s*III)?|PCIe|USB-?C)\b", re.IGNORECASE
)
_PCIE_GEN = re.compile(
    r"\bPCIe?\s*(?:Gen\s*)?(?P<gen>[45](?:\.\d)?)\b|\bPCIE(?P<gen2>[45])\b",
    re.IGNORECASE,
)
_REFRESH = re.compile(r"\b(?P<hz>\d{2,3})\s*Hz\b", re.IGNORECASE)
_RESOLUTION = re.compile(
    r"\b(?P<res>8K|4K|UHD|QHD|WQHD|FHD|Full\s*HD|HD|1080p|1440p|2160p|7680p)\b",
    re.IGNORECASE,
)
_PANEL = re.compile(
    r"\b(?P<panel>OLED|QLED|IPS|VA|TN|Mini-?LED|Micro-?LED)\b", re.IGNORECASE
)
_RADIATOR = re.compile(r"\b(?P<mm>120|240|280|360|420)\s*mm\b", re.IGNORECASE)
_RPM = re.compile(r"\b(?P<rpm>5400|7200)\s*RPM\b", re.IGNORECASE)
_OC = re.compile(r"\b(?:\bOC\b|Overclock(?:ed)?)\b", re.IGNORECASE)
_LIQUID = re.compile(r"\b(?:Water\s*Cooler|AIO|Liquid\s*Cooler)\b", re.IGNORECASE)
_AIR_COOLER = re.compile(r"\b(?:Air\s*Cooler|Cooler\s*a[eé]reo)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedAttribute:
    value: str | None
    source: str

    @property
    def found(self) -> bool:
        return self.value is not None and str(self.value).strip() != ""


@dataclass(frozen=True)
class ProductAttributeBundle:
    """Resolved identity + variant dimensions with per-field provenance."""

    values: Mapping[str, ResolvedAttribute]
    category: str | None = None

    def get(self, attribute: str) -> ResolvedAttribute:
        return self.values.get(attribute, ResolvedAttribute(None, SOURCE_NOT_FOUND))

    def value(self, attribute: str) -> str | None:
        resolved = self.get(attribute)
        return resolved.value if resolved.found else None

    def sources(self) -> dict[str, str]:
        return {key: item.source for key, item in self.values.items()}

    def filled(self) -> dict[str, str]:
        return {
            key: item.value
            for key, item in self.values.items()
            if item.found and item.value is not None
        }

    def found_sources(self) -> dict[str, str]:
        """Sources for attributes that resolved to a non-empty value."""
        return {
            key: item.source
            for key, item in self.values.items()
            if item.found and item.source != SOURCE_NOT_FOUND
        }


def detect_product_category(title: str | None) -> str | None:
    """Best-effort category hint from CategoryProfile registry (never required)."""
    from scout_api.modules.crawler.utils.category_profiles.registry import (
        detect_category,
        ensure_profiles_loaded,
    )

    ensure_profiles_loaded()
    detected = detect_category(title)
    if detected:
        return detected
    # Legacy motherboard chipset heuristic when needles miss.
    if not title:
        return None
    text = title.casefold()
    if re.search(r"\b[abzhx]\d{3}m?\b", text) and (
        "am4" in text or "am5" in text or "lga" in text or "ddr" in text
    ):
        return "motherboard"
    return None


def resolve_attribute(
    attribute: str,
    *,
    specifications: Mapping[str, Any] | None = None,
    structured: Mapping[str, Any] | None = None,
    selected_variant: Mapping[str, Any] | None = None,
    url_evidence: Mapping[str, Any] | None = None,
    title: str | None = None,
    category: str | None = None,
) -> ResolvedAttribute:
    """Resolve one attribute with selected-variant → … → URL → null priority."""
    return resolve_attributes(
        [attribute],
        specifications=specifications,
        structured=structured,
        selected_variant=selected_variant,
        url_evidence=url_evidence,
        title=title,
        category=category,
    ).get(attribute.strip().casefold())


def resolve_attributes(
    attributes: Sequence[str],
    *,
    specifications: Mapping[str, Any] | None = None,
    structured: Mapping[str, Any] | None = None,
    selected_variant: Mapping[str, Any] | None = None,
    url_evidence: Mapping[str, Any] | None = None,
    title: str | None = None,
    category: str | None = None,
) -> ProductAttributeBundle:
    """Resolve many attributes independently (higher-priority source wins)."""
    detected = category or detect_product_category(title)
    title_map = _title_fallback_map(title, detected) if title else {}
    url_map = (
        {k: v for k, v in url_evidence.items() if v not in (None, "")}
        if url_evidence
        else {}
    )
    values: dict[str, ResolvedAttribute] = {}
    for attribute in attributes:
        key = attribute.strip().casefold()
        from_selected = _from_mapping(key, selected_variant)
        if from_selected is not None:
            values[key] = ResolvedAttribute(from_selected, SOURCE_SELECTED_VARIANT)
            continue
        from_specs = _from_mapping(key, specifications)
        if from_specs is not None:
            values[key] = ResolvedAttribute(from_specs, SOURCE_SPECIFICATIONS)
            continue
        from_structured = _from_mapping(key, structured)
        if from_structured is not None:
            values[key] = ResolvedAttribute(from_structured, SOURCE_STRUCTURED)
            continue
        if key not in TITLE_SAFE_ATTRIBUTES:
            values[key] = ResolvedAttribute(None, SOURCE_NOT_FOUND)
            continue
        from_title = title_map.get(key)
        if from_title:
            values[key] = ResolvedAttribute(from_title, SOURCE_TITLE)
            continue
        from_url = _from_mapping(key, url_map) if url_map else None
        values[key] = (
            ResolvedAttribute(from_url, SOURCE_URL_SLUG)
            if from_url
            else ResolvedAttribute(None, SOURCE_NOT_FOUND)
        )
    _apply_category_identity(values, title, detected)
    return ProductAttributeBundle(values=values, category=detected)


def resolve_product_identity(
    *,
    specifications: Mapping[str, Any] | None = None,
    structured: Mapping[str, Any] | None = None,
    selected_variant: Mapping[str, Any] | None = None,
    url_evidence: Mapping[str, Any] | None = None,
    title: str | None = None,
    attributes: Sequence[str] | None = None,
    category: str | None = None,
) -> ProductAttributeBundle:
    """Resolve the common identity/variant attribute set used by store spiders."""
    return resolve_attributes(
        attributes or DEFAULT_IDENTITY_ATTRIBUTES,
        specifications=specifications,
        structured=structured,
        selected_variant=selected_variant,
        url_evidence=url_evidence,
        title=title,
        category=category,
    )


def format_variant_dimensions(
    values: Mapping[str, str | None] | ProductAttributeBundle,
    *,
    keys: Sequence[str] = (
        "color",
        "storage",
        "ram",
        "size",
        "capacity",
        "screen_size",
    ),
) -> str | None:
    """Build the project ``variant`` string: ``color: Blue; storage: 128 GB``."""
    if isinstance(values, ProductAttributeBundle):
        mapping = {key: values.value(key) for key in keys}
    else:
        mapping = dict(values)
    parts = [
        f"{key}: {mapping[key]}" for key in keys if mapping.get(key) not in (None, "")
    ]
    return "; ".join(parts) or None


def format_identity_variant(
    values: Mapping[str, str | None] | ProductAttributeBundle,
) -> str | None:
    """Public ``variant`` string: commercial edition for GPUs, dimensions otherwise.

    GPU cooler lines (Dual OC Edition, Shadow 3X OC) are the searchable refinement
    of the base chip in ``model``. Other categories keep ``color: …; storage: …``.
    CategoryProfile.variant_from can override the dimension key set.
    """
    category = values.category if isinstance(values, ProductAttributeBundle) else None
    edition = (
        values.value("edition")
        if isinstance(values, ProductAttributeBundle)
        else (values.get("edition") if values else None)
    )
    if category == "gpu":
        return edition or None
    if category == "console" and edition:
        return edition
    from scout_api.modules.crawler.utils.category_profiles.registry import (
        ensure_profiles_loaded,
        get_profile,
    )

    ensure_profiles_loaded()
    profile = get_profile(category)
    if profile is not None and profile.variant_from:
        if profile.variant_from == ("edition",) and edition:
            return edition
        if set(profile.variant_from) <= {
            "color",
            "storage",
            "ram",
            "size",
            "capacity",
            "screen_size",
            "connectivity",
        }:
            return format_variant_dimensions(values, keys=profile.variant_from)
    return format_variant_dimensions(values)


def _apply_category_identity(
    values: dict[str, ResolvedAttribute],
    title: str | None,
    category: str | None,
) -> None:
    """Fill / reclassify brand, model, edition using category-aware title parsers.

    Structured values that already look like a base model are only canonicalized.
    Opaque codes and cooler-line labels are not treated as ``model``.
    """
    if not title:
        return
    parsed = parse_title_identity(title, category=category)
    current_brand = values.get("brand")
    title_brand_is_noise = bool(
        current_brand is not None
        and current_brand.found
        and current_brand.source == SOURCE_TITLE
        and _is_noise_brand(current_brand.value)
    )
    if parsed.brand and (
        current_brand is None
        or not current_brand.found
        or title_brand_is_noise
        or (
            current_brand.source == SOURCE_TITLE
            and parsed.confidence in {"exact_title", "contextual"}
        )
    ):
        values["brand"] = ResolvedAttribute(parsed.brand, SOURCE_TITLE)
    elif title_brand_is_noise and not parsed.brand:
        values.pop("brand", None)

    current_model = values.get("model")
    structured_model = bool(
        current_model is not None
        and current_model.found
        and current_model.source in {SOURCE_SPECIFICATIONS, SOURCE_STRUCTURED}
        and current_model.value is not None
    )
    if structured_model:
        assert current_model is not None
        if looks_like_base_model(category, current_model.value):
            canonical = canonicalize_model_display(category, current_model.value)
            if canonical and canonical != current_model.value:
                values["model"] = ResolvedAttribute(canonical, current_model.source)
            parsed_model_usable = False
        else:
            parsed_model_usable = bool(parsed.model)
    else:
        parsed_model_usable = bool(parsed.model)

    if parsed.model and (not structured_model or parsed_model_usable):
        displaced = current_model.value if structured_model and current_model else None
        if not structured_model or not looks_like_base_model(category, displaced):
            values["model"] = ResolvedAttribute(parsed.model, SOURCE_TITLE)
            if parsed.variant:
                values["edition"] = ResolvedAttribute(parsed.variant, SOURCE_TITLE)
            elif displaced and not looks_like_opaque_code(displaced):
                if not looks_like_base_model(category, displaced):
                    values["edition"] = ResolvedAttribute(
                        displaced,
                        current_model.source if current_model else SOURCE_TITLE,
                    )

    current_edition = values.get("edition")
    if parsed.variant and (current_edition is None or not current_edition.found):
        values["edition"] = ResolvedAttribute(parsed.variant, SOURCE_TITLE)

    if parsed.model and category == "gpu":
        gpu_model = values.get("gpu_model")
        if gpu_model is None or not gpu_model.found:
            values["gpu_model"] = ResolvedAttribute(parsed.model, SOURCE_TITLE)


def _is_noise_brand(value: str | None) -> bool:
    if not value:
        return True
    lower = value.casefold()
    return lower in _CATEGORY_PREFIXES or lower in {
        "gpu",
        "nvidia",
        "geforce",
        "radeon",
    }


def merge_specification_gaps(
    specifications: Mapping[str, Any] | None,
    bundle: ProductAttributeBundle,
    *,
    attributes: Sequence[str] = DEFAULT_IDENTITY_ATTRIBUTES,
) -> dict[str, Any]:
    """Copy resolved values into specs only when that key family is absent.

    When a value already exists under an alias (e.g. ``Memória`` → vram), also
    promote a canonical key without removing the original label.
    """
    result = {
        str(key): value
        for key, value in (specifications or {}).items()
        if value not in (None, "")
    }
    for attribute in attributes:
        if attribute in {"brand", "model"}:
            continue
        if attribute in result:
            continue
        existing = _from_mapping(attribute, result)
        if existing is not None:
            result[attribute] = existing
            continue
        value = bundle.value(attribute)
        if value:
            result[attribute] = value
    return result


def _title_fallback_map(title: str, category: str | None) -> dict[str, str]:
    """Extract all high-confidence title attributes in one pass."""
    text = re.sub(r"\s+", " ", title).strip()
    result: dict[str, str] = {}

    brand = _brand_from_title(text)
    if brand:
        result["brand"] = brand
    model = _model_from_title(text)
    if model:
        result["model"] = model
    color = _color_from_title(text)
    if color:
        result["color"] = color
    voltage = _voltage_from_title(text)
    if voltage:
        result["voltage"] = voltage
    screen = _screen_from_title(text, category)
    if screen:
        result["screen_size"] = screen
    processor = _processor_from_title(text)
    if processor:
        result["processor"] = processor
    size = _size_from_title(text)
    if size:
        result["size"] = size
    volume = _volume_capacity_from_title(text)
    if volume:
        result["capacity"] = volume

    caps = _classify_title_capacities(text, category)
    result.update(caps)

    kit = _KIT.search(text)
    if kit and (category in {None, "ram"} or _MEMORY_TYPE.search(text)):
        result.setdefault("module_count", kit.group("count"))
        result.setdefault(
            "module_capacity",
            _normalize_capacity(kit.group("each"), kit.group("unit").upper()),
        )

    memory_type = _MEMORY_TYPE.search(text)
    if memory_type:
        result["memory_type"] = memory_type.group("type").upper()

    freq = _FREQUENCY.search(text)
    if freq and (category in {None, "ram", "cpu"} or memory_type):
        result["frequency"] = f"{int(freq.group('hz'))} MHz"

    cas = _CAS.search(text)
    if cas:
        result["cas_latency"] = f"CL{int(cas.group('cl'))}"

    conn = _CONNECTIVITY.search(text)
    if conn and conn.group("net").upper() in {"5G", "4G"}:
        result["connectivity"] = conn.group("net").upper()

    socket = _SOCKET.search(text)
    if socket:
        result["socket"] = socket.group("socket").upper().replace(" ", "")

    chipset = _CHIPSET.search(text)
    if chipset and category in {None, "motherboard"}:
        result["chipset"] = chipset.group("chipset").upper()

    if _WIFI.search(text):
        result["wifi"] = "true"

    cores = _CORES.search(text)
    if cores:
        result["cores"] = cores.group("cores")
    threads = _THREADS.search(text)
    if threads:
        result["threads"] = threads.group("threads")

    gpu = _GPU.search(text)
    if gpu:
        result["gpu_model"] = re.sub(r"\s+", " ", gpu.group("gpu")).strip()

    watt = _WATTAGE.search(text)
    if watt and category in {None, "psu"}:
        # Avoid tiny false positives: PSU watts are typically >= 300.
        watts = int(watt.group("watts"))
        if watts >= 300:
            result["wattage"] = f"{watts} W"

    efficiency = _EFFICIENCY.search(text)
    if efficiency:
        tier = efficiency.group("tier").title()
        result["efficiency"] = f"80 Plus {tier}"

    modular = _MODULAR.search(text)
    if modular:
        mod = re.sub(r"\s+", " ", modular.group("mod")).strip().title()
        mod = mod.replace("Fully Modular", "Full Modular")
        result["modularity"] = mod

    form = _FORM_FACTOR.search(text)
    if form:
        token = form.group("form")
        upper = token.upper()
        if upper == "M.2":
            result["form_factor"] = "M.2"
        elif upper == "MATX":
            result["form_factor"] = "mATX"
        else:
            result["form_factor"] = upper

    iface = _INTERFACE.search(text)
    if iface:
        token = iface.group("iface")
        upper = token.upper().replace("SATA III", "SATA")
        if upper == "NVME":
            upper = "NVMe"
        result["interface"] = upper

    pcie = _PCIE_GEN.search(text)
    if pcie:
        gen = pcie.group("gen") or pcie.group("gen2")
        if gen:
            if "." not in gen:
                gen = f"{gen}.0"
            result["pcie_generation"] = f"PCIe {gen}"

    # Refresh rate: prefer explicit pairing with Hz near display context.
    refresh = _REFRESH.search(text)
    if refresh and category in {None, "monitor", "tv", "notebook"}:
        hz = int(refresh.group("hz"))
        if hz >= 60:
            result["refresh_rate"] = f"{hz} Hz"

    resolution = _RESOLUTION.search(text)
    if resolution:
        res = re.sub(r"\s+", " ", resolution.group("res")).upper()
        res = res.replace("FULL HD", "FHD")
        result["resolution"] = res

    panel = _PANEL.search(text)
    if panel:
        result["panel"] = panel.group("panel").upper().replace("MINI-LED", "Mini-LED")

    if _LIQUID.search(text):
        result["cooler_type"] = "liquid"
    elif _AIR_COOLER.search(text):
        result["cooler_type"] = "air"

    radiator = _RADIATOR.search(text)
    if radiator and (
        category in {None, "cooler"} or result.get("cooler_type") == "liquid"
    ):
        result["radiator_size"] = f"{radiator.group('mm')} mm"

    rpm = _RPM.search(text)
    if rpm:
        result["rpm"] = f"{rpm.group('rpm')} RPM"

    if _OC.search(text) and category in {None, "gpu"}:
        result["overclocked"] = "true"

    # RAM category: expose total kit size also as capacity when GB (not ml).
    if category == "ram" and "ram" in result and "capacity" not in result:
        result["capacity"] = result["ram"]
    if category == "ssd" and "storage" in result and "capacity" not in result:
        result["capacity"] = result["storage"]

    # CategoryProfile extractors (Phase 4–7): more precise than generic heuristics.
    from scout_api.modules.crawler.utils.category_profiles.attribute_extractors import (
        extract_profile_attributes,
    )

    profile_attrs = extract_profile_attributes(category, text)
    for key, value in profile_attrs.items():
        if key == "category":
            # Reserved for product CategoryProfile id in persisted attributes.
            continue
        result[key] = value

    return result


def _from_mapping(attribute: str, data: Mapping[str, Any] | None) -> str | None:
    if not data:
        return None
    aliases = _SPEC_ALIASES.get(attribute, frozenset({attribute}))
    for key, raw in data.items():
        folded = _normalize_key(str(key))
        if not _key_matches_alias(folded, aliases):
            continue
        if attribute == "storage" and _looks_like_ram_label(folded):
            continue
        if attribute == "ram" and _looks_like_storage_label(folded):
            continue
        # GPU "Memória: 8GB GDDR7" must not resolve as system RAM.
        if attribute == "ram" and _VRAM_MARKER.search(str(raw)):
            continue
        cleaned = _clean_value(raw, attribute=attribute)
        if cleaned:
            return cleaned
    # Accept generic memory labels as VRAM when the value itself is GDDR/HBM.
    if attribute == "vram":
        for key, raw in data.items():
            folded = _normalize_key(str(key))
            if folded in {"memoria", "memória", "memory", "memoria dedicada"}:
                if _VRAM_MARKER.search(str(raw)):
                    cleaned = _clean_value(raw, attribute="vram")
                    if cleaned:
                        return cleaned
    return None


def _key_matches_alias(folded: str, aliases: frozenset[str]) -> bool:
    for alias in aliases:
        if folded == alias:
            return True
        if len(alias) <= 3:
            if re.search(rf"\b{re.escape(alias)}\b", folded):
                return True
            continue
        if alias in folded or folded in alias:
            return True
    return False


def _classify_title_capacities(
    title: str | None, category: str | None = None
) -> dict[str, str]:
    """Assign GB/TB tokens to ram/vram/storage using markers and category."""
    if not title:
        return {}
    text = re.sub(r"\s+", " ", title)
    hits: list[tuple[str, int, int]] = []
    for match in _CAPACITY.finditer(text):
        unit = match.group("unit").upper()
        if unit == "MB":
            continue
        # Skip capacities inside kit notation; handled separately.
        if _inside_kit_parens(text, match.start()):
            continue
        value = _normalize_capacity(match.group("num"), unit)
        hits.append((value, match.start(), match.end()))
    if not hits:
        return {}

    classified: dict[str, str] = {}
    ambiguous_unknowns: list[str] = []
    for value, start, end in hits:
        kind = _capacity_kind(text, start, end, category)
        if kind is None:
            ambiguous_unknowns.append(value)
            continue
        classified.setdefault(kind, value)

    if (
        len(hits) == 1
        and ambiguous_unknowns
        and not classified
        and not _RAM_MARKER.search(text)
        and not _VRAM_MARKER.search(text)
    ):
        alone = ambiguous_unknowns[0]
        if category == "gpu":
            classified["vram"] = alone
        elif category == "ram":
            classified["ram"] = alone
        elif category in {"smartphone", "ssd", "console", "notebook", None}:
            if category is None and _MEMORY_TYPE.search(text):
                mem = _MEMORY_TYPE.search(text)
                assert mem is not None
                if mem.group("type").upper().startswith("GDDR"):
                    classified["vram"] = alone
                elif mem.group("type").upper().startswith("DDR"):
                    classified["ram"] = alone
                else:
                    classified["storage"] = alone
            else:
                classified["storage"] = alone
    elif (
        len(ambiguous_unknowns) == 1
        and "storage" not in classified
        and ("ram" in classified or "vram" in classified)
        and category in {"smartphone", "notebook", "console", None}
    ):
        # e.g. "256GB 8GB RAM" → storage + ram
        classified["storage"] = ambiguous_unknowns[0]
    elif (
        len(ambiguous_unknowns) == 1
        and "ram" not in classified
        and "storage" in classified
        and category in {"notebook", "smartphone"}
    ):
        # e.g. notebook "16GB 512GB SSD" → ram + storage
        classified["ram"] = ambiguous_unknowns[0]
    return classified


def _inside_kit_parens(text: str, index: int) -> bool:
    open_idx = text.rfind("(", 0, index)
    close_idx = text.find(")", index)
    if open_idx < 0 or close_idx < 0:
        return False
    return bool(_KIT.search(text[open_idx : close_idx + 1]))


def _capacity_kind(text: str, start: int, end: int, category: str | None) -> str | None:
    """Classify using tokens immediately beside the capacity (+ category hints)."""
    after = text[end:].lstrip()
    next_token = after.split(" ", 1)[0] if after else ""
    before = text[:start].rstrip()
    prev_token = before.split(" ")[-1] if before else ""
    side = f"{prev_token} {next_token}".strip()
    if side:
        if _VRAM_MARKER.search(side):
            return "vram"
        if _STORAGE_MARKER.search(side):
            return "storage"
        if _RAM_MARKER.search(side):
            return "ram"
    # Category-aware fallback when adjacent marker is absent but title context is clear.
    if category == "gpu" and _VRAM_MARKER.search(text):
        return "vram"
    if category == "ram" and _MEMORY_TYPE.search(text):
        return "ram"
    return None


def _skip_leading_noise(tokens: list[str]) -> list[str]:
    """Drop category prefixes and Portuguese connectors before brand/model."""
    connectors = {"de", "da", "do", "para", "com", "vídeo", "video"}
    changed = True
    while tokens and changed:
        changed = False
        while tokens and tokens[0].casefold() in _CATEGORY_PREFIXES:
            tokens = tokens[1:]
            changed = True
        while tokens and tokens[0].casefold() in connectors:
            tokens = tokens[1:]
            changed = True
    return tokens


def _brand_from_title(title: str) -> str | None:
    tokens = _skip_leading_noise(_title_tokens(title))
    if not tokens:
        return None
    candidate = tokens[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.&-]{1,30}", candidate):
        return None
    if candidate.casefold() in _STOP_MODEL_TOKENS:
        return None
    if candidate.casefold() in _CATEGORY_PREFIXES:
        return None
    if candidate.casefold() in {"nvidia", "geforce", "radeon", "gpu"}:
        return None
    if _CAPACITY.fullmatch(candidate):
        return None
    return _title_case_token(candidate)


def _model_from_title(title: str) -> str | None:
    tokens = _skip_leading_noise(_title_tokens(title))
    if not tokens:
        return None
    tokens = tokens[1:]  # skip brand
    if not tokens:
        return None
    model_parts: list[str] = []
    for token in tokens:
        lower = token.casefold()
        if _CAPACITY.fullmatch(token) or _VOLTAGE.fullmatch(token):
            break
        if lower in _STOP_MODEL_TOKENS:
            break
        if _STORAGE_MARKER.fullmatch(token) or _RAM_MARKER.fullmatch(token):
            break
        if _CORES.fullmatch(token) or _THREADS.fullmatch(token):
            break
        if re.fullmatch(r"\d+[-]?cores?", lower) or re.fullmatch(
            r"\d+[-]?threads?", lower
        ):
            break
        if _FREQUENCY.fullmatch(token) or _WATTAGE.fullmatch(token):
            break
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+/-]{0,30}", token):
            break
        model_parts.append(token)
        if len(model_parts) >= 5:
            break
    if not model_parts:
        return None
    return " ".join(_title_case_token(part) for part in model_parts)


def _color_from_title(title: str) -> str | None:
    tokens = _title_tokens(title)
    found: list[str] = []
    for index, token in enumerate(tokens):
        lower = token.casefold()
        if lower not in _COLOR_TERMS:
            continue
        if lower in _AMBIGUOUS_COLOR_TERMS and _efficiency_color_context(tokens, index):
            continue
        found.append(_title_case_token(token))
    if len(found) != 1:
        return None
    return found[0]


def _efficiency_color_context(tokens: list[str], index: int) -> bool:
    """True when a metal color is part of 80 Plus / certification wording."""
    window = " ".join(tokens[max(0, index - 3) : index + 1]).casefold()
    return "80" in window or "plus" in window or "bronze" in window


def _voltage_from_title(title: str) -> str | None:
    match = _VOLTAGE.search(title)
    if not match:
        return None
    return f"{int(match.group('volts'))} V"


def _screen_from_title(title: str, category: str | None = None) -> str | None:
    match = _SCREEN.search(title)
    if match:
        size = _format_decimal(match.group("size").replace(",", "."))
        return f'{size}"'
    if category not in {"notebook", "monitor", "tv"}:
        return None
    if not (_RESOLUTION.search(title) or _REFRESH.search(title)):
        return None
    context = _SCREEN_CONTEXT.search(title)
    if not context:
        return None
    size = _format_decimal(context.group("size").replace(",", "."))
    return f'{size}"'


def _processor_from_title(title: str) -> str | None:
    # Explicit CPU/SoC families only — never "Apple iPhone …" as processor.
    match = re.search(
        r"\b((?:Intel\s+)?Core\s+i[3579]-?\d{4,5}[A-Z]{0,3}"
        r"|(?:AMD\s+)?Ryzen\s+[3579]\s+\d{4}[A-Z0-9]{0,4}"
        r"|(?:AMD\s+)?Ryzen\s+[3579]\b"
        r"|Apple\s+M\d(?:\s+(?:Pro|Max|Ultra))?"
        r"|Snapdragon\s+[A-Za-z0-9][A-Za-z0-9 .-]{1,20}"
        r"|Exynos\s+\d+[A-Za-z0-9-]*"
        r"|Dimensity\s+\d+[A-Za-z0-9-]*)\b",
        title,
        re.IGNORECASE,
    )
    if not match:
        return None
    text = _CAPACITY.split(match.group(1))[0].strip(" -")
    text = _CORES.split(text)[0].strip(" -")
    return text or None


def _size_from_title(title: str) -> str | None:
    match = re.search(
        r"\b(?:size|tamanho|tamaño)\s*[:\-]?\s*(XXL|XL|L|M|S|XS|\d{1,2})\b",
        title,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()
    return None


def _volume_capacity_from_title(title: str) -> str | None:
    match = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*(ml|mL|ML|l|L|lt|litro?s?)\b",
        title,
    )
    if not match:
        return None
    num = _format_decimal(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit.startswith("l"):
        return f"{num} L"
    return f"{num} ml"


def _clean_value(raw: Any, *, attribute: str) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("value") or raw.get("label")
    text = str(raw).strip()
    if not text or text.casefold() in {"null", "none", "n/a", "-"}:
        return None
    text = re.sub(r"\s+", " ", text).strip(" .")
    if attribute in {"storage", "ram", "vram", "capacity", "module_capacity"}:
        match = _CAPACITY.search(text)
        if match:
            return _normalize_capacity(match.group("num"), match.group("unit").upper())
    if attribute == "voltage":
        match = _VOLTAGE.search(text)
        if match:
            return f"{int(match.group('volts'))} V"
    if attribute == "screen_size":
        if len(text.split()) > 4:
            return None
        match = _SCREEN.search(text)
        if match:
            size = _format_decimal(match.group("size").replace(",", "."))
            return f'{size}"'
        return None
    if attribute == "color":
        return _title_case_token(text)
    if attribute == "brand" and text.isupper() and len(text) > 1:
        return _title_case_token(text)
    if attribute == "wifi":
        lowered = text.casefold()
        if lowered in {"true", "yes", "sim", "1"}:
            return "true"
        if lowered in {"false", "no", "nao", "não", "0"}:
            return "false"
        if "wi" in lowered:
            return "true"
    if attribute == "overclocked":
        return "true" if text.casefold() in {"true", "yes", "oc", "1"} else text
    return text


def _normalize_capacity(num: str, unit: str) -> str:
    return f"{_format_decimal(num.replace(',', '.'))} {unit.upper()}"


def _format_decimal(num: str) -> str:
    try:
        value = Decimal(num)
    except InvalidOperation:
        return num
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _title_tokens(title: str) -> list[str]:
    # Spaced dashes/pipes are separators; keep hyphenated codes (i7-14700K).
    cleaned = re.sub(r"\s+[-–—|/,_]+\s+", " ", title)
    cleaned = re.sub(r"[|/,_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return [token for token in cleaned.split(" ") if token and token != "-"]


def _title_case_token(token: str) -> str:
    # Preserve product/model codes that mix letters and digits (7800X3D, B650M).
    if re.search(r"\d", token):
        return token
    if token.isupper() and len(token) > 1:
        return token.capitalize()
    return token


def _normalize_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip().casefold()


def _looks_like_ram_label(label: str) -> bool:
    return bool(re.search(r"\bram\b|memoria\s*ram|system memory", label))


def _looks_like_storage_label(label: str) -> bool:
    return bool(
        re.search(
            r"storage|armazen|almacen|ssd|hdd|disco|built-in|memoria\s*interna|"
            r"memória\s*interna|internal\s*memory",
            label,
        )
    )


__all__ = [
    "DEFAULT_IDENTITY_ATTRIBUTES",
    "SOURCE_NOT_FOUND",
    "SOURCE_SELECTED_VARIANT",
    "SOURCE_SPECIFICATIONS",
    "SOURCE_STRUCTURED",
    "SOURCE_TITLE",
    "SOURCE_URL_SLUG",
    "TITLE_SAFE_ATTRIBUTES",
    "ProductAttributeBundle",
    "ResolvedAttribute",
    "detect_product_category",
    "format_identity_variant",
    "format_variant_dimensions",
    "merge_specification_gaps",
    "resolve_attribute",
    "resolve_attributes",
    "resolve_product_identity",
]
