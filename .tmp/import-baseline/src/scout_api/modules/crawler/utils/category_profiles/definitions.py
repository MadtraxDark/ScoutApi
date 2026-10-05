"""Register all CategoryProfiles (side-effect import).

Each profile declares attributes, conflicts, search filters, and optionally a
title parser. Deep attribute extractors grow per category without editing the
resolver core.
"""

from __future__ import annotations

from scout_api.modules.crawler.utils.category_profiles.attribute_extractors import (
    EXTRACTORS,
)
from scout_api.modules.crawler.utils.category_profiles.base import (
    AttributeExtractor,
    CategoryProfile,
    TitleParser,
)
from scout_api.modules.crawler.utils.category_profiles.extra_parsers import (
    parse_console,
    parse_cooler,
    parse_monitor,
    parse_motherboard,
    parse_network_generic,
    parse_notebook,
    parse_peripheral_generic,
    parse_psu,
    parse_tablet,
    parse_tv,
)
from scout_api.modules.crawler.utils.category_profiles.registry import register_profile
from scout_api.modules.crawler.utils.product_identity import ParsedIdentity


def _lazy(name: str) -> TitleParser:
    """Defer product_identity parser imports to avoid circular load."""

    def _call(title: str, category: str) -> ParsedIdentity:
        from scout_api.modules.crawler.utils import product_identity as pi

        parser = getattr(pi, name)
        result: ParsedIdentity = parser(title, category)
        return result

    return _call


_ASUS_ALIASES = {"asustek": "ASUS", "asustek computer": "ASUS"}
_MSI_ALIASES = {"micro-star international": "MSI", "micro star international": "MSI"}


def _p(
    id: str,
    label: str,
    *,
    needles: tuple[str, ...] = (),
    priority: int = 100,
    attrs: tuple[str, ...] = (),
    critical: tuple[str, ...] = (),
    strong: tuple[str, ...] = (),
    filters: tuple[str, ...] = (),
    variant_from: tuple[str, ...] = ("edition",),
    aliases: dict[str, str] | None = None,
    parse_title: TitleParser | None = None,
    extract_attributes: AttributeExtractor | None = None,
    specs_only_critical: tuple[str, ...] = (),
    notes: str = "",
) -> CategoryProfile:
    extractor = extract_attributes
    if extractor is None:
        extractor = EXTRACTORS.get(id)
    return register_profile(
        CategoryProfile(
            id=id,
            label=label,
            detection_needles=needles,
            detection_priority=priority,
            supported_attributes=attrs,
            critical_conflict_keys=critical,
            strong_match_keys=strong,
            search_filters=filters,
            variant_from=variant_from,
            brand_aliases=aliases or {},
            parse_title=parse_title,
            extract_attributes=extractor,
            specs_only_critical=specs_only_critical,
            notes=notes,
        )
    )


# --- Phase 1: GPU / RAM / Motherboard / CPU ---
_p(
    "gpu",
    "GPU / Placa de vídeo",
    needles=("rtx", "gtx", "radeon", "geforce", "placa de vídeo", "placa de video"),
    priority=40,
    attrs=(
        "gpu_model",
        "vram",
        "memory_type",
        "bus_width",
        "overclocked",
        "edition",
        "mpn",
    ),
    critical=("gpu_model", "vram", "edition"),
    strong=("mpn", "gpu_model", "vram"),
    filters=("brand", "model", "variant", "vram", "memory_type"),
    aliases={**_ASUS_ALIASES, **_MSI_ALIASES},
    parse_title=_lazy("_parse_gpu"),
)

_p(
    "ram",
    "Memória RAM",
    needles=(
        "ddr4",
        "ddr5",
        "dimm",
        "sodimm",
        "fury",
        "vengeance",
        "memoria ram",
        "memória ram",
    ),
    priority=50,
    attrs=(
        "capacity",
        "ram",
        "module_count",
        "module_capacity",
        "memory_type",
        "frequency",
        "cas_latency",
        "form_factor",
        "ecc",
        "rgb",
    ),
    critical=("memory_type", "capacity", "frequency"),
    strong=("mpn", "memory_type", "capacity", "frequency"),
    filters=(
        "brand",
        "model",
        "memory_type",
        "capacity",
        "frequency",
        "cas_latency",
    ),
    parse_title=_lazy("_parse_ram"),
)

_p(
    "motherboard",
    "Placa-mãe",
    needles=(
        "placa-mãe",
        "placa-mae",
        "placa mae",
        "motherboard",
        "b650",
        "z790",
        "x870",
        "a620",
        "b550",
        "x670",
    ),
    priority=20,
    attrs=(
        "chipset",
        "socket",
        "memory_type",
        "form_factor",
        "wifi",
        "bluetooth",
        "pcie_generation",
        "mpn",
    ),
    critical=("chipset", "socket", "memory_type", "wifi"),
    strong=("mpn", "chipset", "socket"),
    filters=("brand", "model", "chipset", "socket", "memory_type", "wifi"),
    aliases=_ASUS_ALIASES,
    parse_title=parse_motherboard,
)

_p(
    "cpu",
    "CPU / Processador",
    needles=(
        "ryzen",
        "core i3",
        "core i5",
        "core i7",
        "core i9",
        "processador",
    ),
    priority=30,
    attrs=("processor", "socket", "cores", "threads", "mpn"),
    critical=("processor",),
    strong=("mpn", "processor"),
    filters=("brand", "model", "socket"),
    parse_title=_lazy("_parse_cpu"),
)

# --- Phase 2: Smartphone / Tablet / SSD / Notebook ---
_p(
    "smartphone",
    "Smartphone",
    needles=(
        "iphone",
        "galaxy",
        "celular",
        "smartphone",
        "pixel",
        "xiaomi",
        "redmi",
    ),
    priority=15,
    attrs=(
        "storage",
        "ram",
        "color",
        "connectivity",
        "condition",
        "carrier",
        "mpn",
    ),
    critical=("storage", "color", "condition"),
    strong=("mpn", "storage", "gtin"),
    filters=("brand", "model", "storage", "color", "variant"),
    variant_from=("color", "storage"),
    parse_title=_lazy("_parse_phone"),
)

_p(
    "tablet",
    "Tablet / iPad",
    needles=("ipad", "tablet", "galaxy tab"),
    priority=18,
    attrs=("storage", "color", "screen_size", "connectivity", "wifi", "mpn"),
    critical=("storage", "connectivity"),
    strong=("mpn", "storage"),
    filters=("brand", "model", "storage", "connectivity"),
    variant_from=("color", "storage", "connectivity"),
    parse_title=parse_tablet,
)

_p(
    "ssd",
    "SSD / NVMe / HDD",
    needles=("nvme", " m.2", "ssd", "hdd", "barracuda", "980 pro", "990 pro"),
    priority=45,
    attrs=(
        "capacity",
        "storage",
        "interface",
        "form_factor",
        "pcie_generation",
        "mpn",
    ),
    critical=("capacity", "interface"),
    strong=("mpn", "capacity", "interface"),
    filters=("brand", "model", "capacity", "interface"),
    parse_title=_lazy("_parse_ssd"),
)

_p(
    "notebook",
    "Notebook / Laptop",
    needles=(
        "notebook",
        "laptop",
        "ultrabook",
        "macbook",
        "rog strix g",
        "strix g16",
        "strix g15",
        "zephyrus",
        "ideapad",
        "thinkpad",
        "vivobook",
        "legion ",
        "nitro v",
        "predator helios",
        "tuf gaming a",
        "tuf gaming f",
    ),
    priority=10,
    attrs=(
        "processor",
        "gpu_model",
        "vram",
        "ram",
        "storage",
        "screen_size",
        "resolution",
        "refresh_rate",
        "panel",
        "color",
        "mpn",
    ),
    critical=("processor", "gpu_model", "ram", "storage"),
    strong=("mpn", "processor", "gpu_model"),
    filters=("brand", "model", "gpu_model", "ram", "storage", "processor"),
    parse_title=parse_notebook,
    notes="model_number/MPN is strongest identity; commercial family alone is weak.",
)

# --- Phase 3: PSU / Cooling / Monitor / TV / Console / Case ---
_p(
    "psu",
    "Fonte / PSU",
    needles=("fonte ", " fonte", "psu", "80 plus", "full modular", "semi modular"),
    priority=55,
    attrs=("wattage", "efficiency", "modularity", "form_factor", "mpn"),
    critical=("wattage",),
    strong=("mpn", "wattage"),
    filters=("brand", "model", "wattage", "efficiency"),
    parse_title=parse_psu,
)

_p(
    "cooler",
    "Cooler / AIO / Air cooler",
    needles=(
        "water cooler",
        "aio",
        "liquid cooler",
        "air cooler",
        "cpu cooler",
        "h150i",
        "h100i",
        "h170i",
        "icue",
        "ak620",
        "peerless assassin",
        "nh-d15",
        "kraken",
    ),
    priority=25,
    attrs=("cooler_type", "radiator_size", "fan_count", "rgb", "mpn"),
    critical=("radiator_size", "cooler_type"),
    strong=("mpn", "radiator_size"),
    filters=("brand", "model", "radiator_size", "cooler_type"),
    parse_title=parse_cooler,
)

_p(
    "fan",
    "Fan / Case fan",
    needles=("case fan", "ventoinha", "sickleflow", "fan pack", "kit 3 fans"),
    priority=70,
    attrs=("fan_size", "fan_count", "rgb", "pwm", "rpm", "mpn"),
    critical=("fan_size", "fan_count"),
    strong=("mpn",),
    filters=("brand", "model", "fan_size", "fan_count"),
    parse_title=parse_peripheral_generic,
)

_p(
    "monitor",
    "Monitor",
    needles=("monitor", "ultragear"),
    priority=35,
    attrs=(
        "screen_size",
        "resolution",
        "panel",
        "refresh_rate",
        "mpn",
    ),
    critical=("screen_size", "resolution", "refresh_rate"),
    strong=("mpn", "model"),
    filters=("brand", "model", "screen_size", "panel", "refresh_rate"),
    parse_title=parse_monitor,
    notes="Do not invent screen_size from model-number prefix digits alone.",
)

_p(
    "tv",
    "TV / Smart TV",
    needles=("smart tv", " oled ", " qled ", "miniled", "mini-led"),
    priority=38,
    attrs=("screen_size", "resolution", "panel", "refresh_rate", "mpn"),
    critical=("screen_size", "resolution"),
    strong=("mpn",),
    filters=("brand", "model", "screen_size", "panel"),
    parse_title=parse_tv,
)

_p(
    "console",
    "Console",
    needles=("playstation", "xbox series", "nintendo switch", "ps5", "ps4"),
    priority=22,
    attrs=("storage", "color", "edition", "bundle"),
    critical=("edition", "storage"),
    strong=("edition", "storage", "gtin"),
    filters=("brand", "model", "variant", "storage", "edition"),
    variant_from=("edition",),
    parse_title=parse_console,
)

_p(
    "case",
    "Gabinete",
    needles=("gabinete", "pc case", "mid tower", "full tower", "airflow"),
    priority=75,
    attrs=("color", "form_factor", "mpn"),
    critical=("color",),
    strong=("mpn",),
    filters=("brand", "model", "color"),
    parse_title=parse_peripheral_generic,
)

# --- Phase 4: Peripherals ---
_p(
    "keyboard",
    "Teclado",
    needles=("teclado", "keyboard", "mechanical keyboard", "kumara", "keychron"),
    priority=60,
    attrs=("switch_type", "layout", "rgb", "connectivity", "color"),
    critical=("switch_type", "layout"),
    strong=("mpn",),
    filters=("brand", "model", "switch_type", "layout"),
    parse_title=parse_peripheral_generic,
)

_p(
    "mouse",
    "Mouse",
    needles=("mouse", "g pro", "superlight", "deathadder"),
    priority=65,
    attrs=("connectivity", "color", "sensor", "dpi", "mpn"),
    critical=("connectivity", "color"),
    strong=("mpn",),
    filters=("brand", "model", "connectivity", "color"),
    parse_title=parse_peripheral_generic,
)

_p(
    "headset",
    "Headset / Headphone / Earbuds",
    needles=("headset", "headphone", "earbuds", "fone de ouvido", "airpods"),
    priority=62,
    attrs=("connectivity", "color", "anc", "mpn"),
    critical=("connectivity",),
    strong=("mpn",),
    filters=("brand", "model", "connectivity"),
    parse_title=parse_peripheral_generic,
)

_p(
    "gamepad",
    "Controle / Gamepad",
    needles=("dualsense", "dualshock", "gamepad", "controle ", "xbox controller"),
    priority=63,
    attrs=("connectivity", "color", "edition", "platform"),
    critical=("edition", "platform"),
    strong=("mpn", "edition"),
    filters=("brand", "model", "edition"),
    parse_title=parse_peripheral_generic,
)

# --- Phase 5: Network / Printer / Scanner / Webcam ---
_p(
    "router",
    "Roteador / Mesh",
    needles=("roteador", "router", "mesh wifi", "mesh wi-fi", "orbi", "deco"),
    priority=58,
    attrs=("wifi", "pack_count", "ethernet_speed", "mpn"),
    critical=("pack_count", "wifi"),
    strong=("mpn", "pack_count"),
    filters=("brand", "model", "wifi", "pack_count"),
    parse_title=parse_network_generic,
)

_p(
    "access_point",
    "Access Point",
    needles=("access point", "unifi ap", "uap-", "outdoor ap"),
    priority=59,
    attrs=("wifi", "poe", "mpn"),
    critical=("wifi",),
    strong=("mpn",),
    filters=("brand", "model", "wifi"),
    parse_title=parse_network_generic,
)

_p(
    "wifi_adapter",
    "Adaptador Wi-Fi / NIC",
    needles=("adaptador wifi", "wifi adapter", "placa de rede", "pcie wifi"),
    priority=68,
    attrs=("wifi", "bluetooth", "interface", "mpn"),
    critical=("wifi", "interface"),
    strong=("mpn",),
    filters=("brand", "model", "wifi", "interface"),
    parse_title=parse_network_generic,
)

_p(
    "network_switch",
    "Switch de rede",
    needles=("switch gerenciavel", "network switch", "poe switch", "gigabit switch"),
    priority=69,
    attrs=("port_count", "port_speed", "poe", "managed", "mpn"),
    critical=("port_count", "port_speed", "poe"),
    strong=("mpn",),
    filters=("brand", "model", "port_count", "poe"),
    parse_title=parse_network_generic,
)

_p(
    "printer",
    "Impressora / Multifuncional",
    needles=("impressora", "printer", "multifuncional", "laserjet", "ecotank"),
    priority=72,
    attrs=("print_technology", "color", "wifi", "duplex", "adf", "mpn"),
    critical=("mpn",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
    specs_only_critical=("mpn",),
    notes=(
        "critical mpn is specs/structured only — never inferred from free-text titles."
    ),
)

_p(
    "scanner",
    "Scanner",
    needles=("scanner", "scanjet"),
    priority=73,
    attrs=("adf", "duplex", "resolution", "mpn"),
    critical=("mpn",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
    specs_only_critical=("mpn",),
    notes=(
        "critical mpn is specs/structured only — never inferred from free-text titles."
    ),
)

_p(
    "webcam",
    "Webcam",
    needles=("webcam", "c920", "brio"),
    priority=74,
    attrs=("resolution", "fps", "microphone", "mpn"),
    critical=("resolution",),
    strong=("mpn",),
    filters=("brand", "model", "resolution"),
    parse_title=parse_peripheral_generic,
)

# --- Phase 6: Audio / Projector / Watch / Camera ---
_p(
    "microphone",
    "Microfone",
    needles=("microfone", "microphone", "blue yeti", "sm7b"),
    priority=76,
    attrs=("connectivity", "polar_pattern", "color", "mpn"),
    critical=("connectivity",),
    strong=("mpn",),
    filters=("brand", "model", "connectivity"),
    parse_title=parse_peripheral_generic,
)

_p(
    "speaker",
    "Caixa de som / Soundbar",
    needles=("soundbar", "caixa de som", "speaker", "sound bar"),
    priority=77,
    attrs=("channels", "power", "bluetooth", "mpn"),
    critical=("channels",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
)

_p(
    "projector",
    "Projetor",
    needles=("projetor", "projector", "epson home cinema"),
    priority=78,
    attrs=("resolution", "brightness", "mpn"),
    critical=("resolution",),
    strong=("mpn",),
    filters=("brand", "model", "resolution"),
    parse_title=parse_peripheral_generic,
)

_p(
    "smartwatch",
    "Smartwatch",
    needles=("apple watch", "smartwatch", "galaxy watch", "amazfit"),
    priority=28,
    attrs=("case_size", "connectivity", "color", "gps", "mpn"),
    critical=("case_size", "connectivity"),
    strong=("mpn", "case_size"),
    filters=("brand", "model", "case_size", "connectivity"),
    parse_title=parse_peripheral_generic,
)

_p(
    "camera",
    "Câmera",
    needles=("camera ", "câmera", "mirrorless", "dslr", "gopro"),
    priority=80,
    attrs=("sensor", "mount", "kit", "color", "mpn"),
    critical=("mount", "kit"),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
)

_p(
    "lens",
    "Lente",
    needles=("lente ", "lens ", "f/1.", "f/2.", "mm f/"),
    priority=81,
    attrs=("mount", "focal_length", "aperture", "mpn"),
    critical=("mount", "focal_length"),
    strong=("mpn", "mount"),
    filters=("brand", "model", "mount"),
    parse_title=parse_peripheral_generic,
)

# --- Phase 7: Storage accessories / Power / Dock / Cable / Furniture ---
_p(
    "memory_card",
    "Cartão de memória",
    needles=("microsd", "sd card", "cfexpress", "cartao de memoria", "cartão sd"),
    priority=82,
    attrs=("capacity", "speed_class", "mpn"),
    critical=("capacity",),
    strong=("capacity",),
    filters=("brand", "model", "capacity"),
    parse_title=parse_peripheral_generic,
)

_p(
    "usb_drive",
    "Pen drive / USB storage",
    needles=("pen drive", "pendrive", "flash drive", "usb drive"),
    priority=83,
    attrs=("capacity", "usb_generation", "mpn"),
    critical=("capacity",),
    strong=("capacity",),
    filters=("brand", "model", "capacity"),
    parse_title=parse_peripheral_generic,
)

_p(
    "nas",
    "NAS",
    needles=(" nas ", "synology", "qnap", "diskstation"),
    priority=84,
    attrs=("bay_count", "included_storage", "mpn"),
    critical=("bay_count", "included_storage"),
    strong=("mpn",),
    filters=("brand", "model", "bay_count"),
    parse_title=parse_peripheral_generic,
    notes="Diskless ≠ populated kit.",
)

_p(
    "ups",
    "No-break / UPS",
    needles=("nobreak", "no-break", " ups ", "sms station"),
    priority=85,
    attrs=("va", "wattage", "topology", "mpn"),
    critical=("va", "wattage"),
    strong=("mpn",),
    filters=("brand", "model", "va"),
    parse_title=parse_peripheral_generic,
)

_p(
    "power_strip",
    "Estabilizador / filtro de linha",
    needles=("estabilizador", "filtro de linha", "surge protector"),
    priority=86,
    attrs=("power", "outlet_count", "mpn"),
    critical=("power",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
)

_p(
    "charger",
    "Carregador",
    needles=("carregador", "charger", "gan charger", "usb-c charger"),
    priority=87,
    attrs=("wattage", "usb_pd", "port_count", "mpn"),
    critical=("wattage",),
    strong=("wattage",),
    filters=("brand", "model", "wattage"),
    parse_title=parse_peripheral_generic,
    notes="65W charger must not be classified as PSU.",
)

_p(
    "laptop_charger",
    "Fonte de notebook",
    needles=("fonte notebook", "laptop charger", "notebook adapter"),
    priority=88,
    attrs=("wattage", "voltage", "connector", "mpn"),
    critical=("wattage", "voltage"),
    strong=("mpn",),
    filters=("brand", "model", "wattage"),
    parse_title=parse_peripheral_generic,
)

_p(
    "dock",
    "Dock / Hub USB-C / Thunderbolt",
    needles=("dock ", "hub usb", "thunderbolt dock", "usb-c hub"),
    priority=89,
    attrs=("usb_generation", "thunderbolt", "port_count", "mpn"),
    critical=("thunderbolt",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
)

_p(
    "cable",
    "Cabos",
    needles=(
        "cabo hdmi",
        "cabo displayport",
        "cabo usb",
        "thunderbolt cable",
        "cabo de rede",
        "patch cord",
    ),
    priority=90,
    attrs=(
        "connector_a",
        "connector_b",
        "length",
        "standard",
        "ethernet_category",
    ),
    critical=("standard", "length", "ethernet_category"),
    strong=("standard", "length"),
    filters=("brand", "model", "standard", "length"),
    parse_title=parse_peripheral_generic,
    notes="Attributes often matter more than variant for cables.",
)

_p(
    "chair",
    "Cadeira gamer / escritório",
    needles=("cadeira gamer", "cadeira escritorio", "office chair", "gaming chair"),
    priority=91,
    attrs=("color", "material", "weight_capacity", "mpn"),
    critical=("color",),
    strong=("mpn",),
    filters=("brand", "model", "color"),
    parse_title=parse_peripheral_generic,
)

_p(
    "desk",
    "Mesa gamer / escritório",
    needles=("mesa gamer", "mesa escritorio", "standing desk", "escritório mesa"),
    priority=92,
    attrs=("width", "depth", "color", "adjustable_height", "mpn"),
    critical=("width",),
    strong=("mpn",),
    filters=("brand", "model"),
    parse_title=parse_peripheral_generic,
)

_p(
    "monitor_mount",
    "Suporte de monitor",
    needles=("suporte monitor", "monitor arm", "monitor mount", "vesa mount"),
    priority=93,
    attrs=("vesa", "arm_count", "max_screen_size", "mpn"),
    critical=("arm_count", "vesa"),
    strong=("mpn",),
    filters=("brand", "model", "arm_count"),
    parse_title=parse_peripheral_generic,
)

# Accessory catch-all (lowest priority — only if nothing else matched via needles
# that are very specific; keep empty needles so detector never prefers this).
_p(
    "accessory",
    "Acessório genérico",
    needles=(),
    priority=999,
    attrs=("color", "mpn"),
    critical=(),
    strong=("mpn", "gtin"),
    filters=("brand", "model"),
    notes="Never auto-detected; used when category is forced explicitly.",
)

__all__ = ["CategoryProfile"]
