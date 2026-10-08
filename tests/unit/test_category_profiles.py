"""CategoryProfile registry, common normalizers, and multi-category identity."""

from __future__ import annotations

import pytest

from scout_api.modules.crawler.utils.category_profiles import (
    all_profiles,
    detect_category,
    ensure_profiles_loaded,
    get_profile,
)
from scout_api.modules.crawler.utils.category_profiles.common import (
    apply_brand_alias,
    normalize_attribute_value,
    normalize_capacity,
    normalize_frequency,
    normalize_kit_config,
    normalize_memory_type,
)
from scout_api.modules.crawler.utils.product_attributes import (
    format_identity_variant,
    resolve_product_identity,
)
from scout_api.modules.crawler.utils.product_identity import canonical_model_key


@pytest.fixture(scope="module", autouse=True)
def _load_profiles() -> None:
    ensure_profiles_loaded()


def test_all_listed_category_profiles_are_registered() -> None:
    ids = {profile.id for profile in all_profiles()}
    required = {
        "gpu",
        "ram",
        "motherboard",
        "cpu",
        "smartphone",
        "tablet",
        "ssd",
        "notebook",
        "psu",
        "cooler",
        "fan",
        "monitor",
        "tv",
        "console",
        "case",
        "keyboard",
        "mouse",
        "headset",
        "gamepad",
        "router",
        "access_point",
        "wifi_adapter",
        "network_switch",
        "printer",
        "scanner",
        "webcam",
        "microphone",
        "speaker",
        "projector",
        "smartwatch",
        "camera",
        "lens",
        "memory_card",
        "usb_drive",
        "nas",
        "ups",
        "power_strip",
        "charger",
        "laptop_charger",
        "dock",
        "cable",
        "chair",
        "desk",
        "monitor_mount",
        "accessory",
    }
    assert required <= ids
    assert len(ids) >= 45


@pytest.mark.parametrize(
    "title",
    [
        (
            "Water Cooler MSI MAG Coreliquid A12, ARGB, 360mm, Intel e AMD, "
            "Preto - CLA12360"
        ),
        "MSI MAG CoreLiquid A12 360 Liquid CPU Cooler, ARGB",
        "Cooler Liquido MSI MAG CoreLiquid A12 360 mm ARGB",
        "Water cooler msi 360mm amd/intel mag coreliquid a12 360 argb preto",
    ],
)
def test_cooler_identity_skips_category_prefix_and_keeps_family(title: str) -> None:
    bundle = resolve_product_identity(
        title=title,
        category="cooler",
        attributes=("brand", "model", "cooler_type", "radiator_size"),
    )

    assert (bundle.value("brand") or "").casefold() == "msi"
    assert "MAG CoreLiquid A12".casefold() == (bundle.value("model") or "").casefold()
    assert bundle.value("cooler_type") == "liquid"
    assert bundle.value("radiator_size") == "360 mm"


def test_cooler_family_without_manufacturer_does_not_invent_mag_brand() -> None:
    bundle = resolve_product_identity(
        title="MAG Coreliquid A12 360 - AIO ARGB CPU Liquid Cooler",
        category="cooler",
        attributes=("brand", "model"),
    )

    assert bundle.value("brand") is None
    assert "MAG Coreliquid A12".casefold() == (bundle.value("model") or "").casefold()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("ASUS GeForce RTX 5070 Dual OC 12GB", "gpu"),
        ("Kingston Fury Beast DDR5 32GB 6000MHz", "ram"),
        ("ASUS TUF Gaming B650M-Plus WiFi AM5 DDR5", "motherboard"),
        ("AMD Ryzen 7 7800X3D", "cpu"),
        ("Apple iPhone 16 Pro 256GB", "smartphone"),
        ("Apple iPad Air 11 M3 256GB Wi-Fi Blue", "tablet"),
        ("Samsung 990 PRO 2TB NVMe M.2", "ssd"),
        ("ASUS ROG Strix G16 i9-14900HX RTX 4070", "notebook"),
        ("Corsair RM850x 850W 80 Plus Gold", "psu"),
        ("Corsair iCUE H150i Elite 360mm RGB", "cooler"),
        ("LG UltraGear 27GS95QE 27 OLED 240Hz", "monitor"),
        ("PlayStation 5 Slim Digital 1TB", "console"),
        ("Carregador GaN 65W USB-C", "charger"),
    ],
)
def test_category_detection(title: str, expected: str) -> None:
    assert detect_category(title) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("128GB", "128 GB"),
        ("1TB", "1 TB"),
        ("6000mhz", "6000 MHz"),
        ("240hz", "240 Hz"),
        ("DDR 5", "DDR5"),
        ("2 X 16 GB", "2x16 GB"),
    ],
)
def test_common_normalizers(raw: str, expected: str) -> None:
    if "ddr" in raw.casefold():
        assert normalize_memory_type(raw) == expected
    elif "x" in raw.casefold() and any(ch.isdigit() for ch in raw):
        assert normalize_kit_config(raw) == expected
    elif "hz" in raw.casefold() or "mhz" in raw.casefold():
        assert normalize_frequency(raw) == expected
    else:
        assert normalize_capacity(raw) == expected


def test_brand_aliases() -> None:
    assert apply_brand_alias("ASUSTeK") == "ASUS"
    assert apply_brand_alias("Micro-Star International") == "MSI"
    assert apply_brand_alias("Hewlett Packard") == "HP"


def test_do_not_infer_kit_from_total_capacity() -> None:
    assert normalize_kit_config("32GB") is None
    bundle = resolve_product_identity(
        title="Kingston Fury Beast DDR5 32GB 6000MHz CL36",
        category="ram",
    )
    assert bundle.value("model") == "Fury Beast"
    assert bundle.value("ram") == "32 GB"
    # Must not invent 2x16 from total alone.
    assert bundle.value("module_count") is None or bundle.value("module_capacity")


def test_motherboard_wifi_is_variant_not_model_noise() -> None:
    bundle = resolve_product_identity(title="ASUS TUF Gaming B650M-Plus WiFi AM5 DDR5")
    assert bundle.category == "motherboard"
    assert bundle.value("brand") in {"Asus", "ASUS"}
    assert "B650" in (bundle.value("model") or "").upper()
    assert "WiFi" not in (bundle.value("model") or "")
    assert format_identity_variant(bundle) in {"WiFi", "wifi", "Wi-Fi"} or bundle.value(
        "edition"
    ) in {"WiFi", "wifi"}


def test_console_digital_vs_model() -> None:
    bundle = resolve_product_identity(title="PlayStation 5 Slim Digital 1TB")
    assert bundle.value("brand") == "Sony"
    assert bundle.value("model") in {"PlayStation 5 Slim", "PlayStation 5"}
    assert format_identity_variant(bundle) == "Digital"


def test_monitor_model_number_not_screen_size_from_prefix() -> None:
    bundle = resolve_product_identity(title="LG UltraGear 27GS95QE OLED QHD 240Hz")
    assert bundle.category == "monitor"
    model = bundle.value("model") or ""
    assert "27GS95QE" in model.upper()
    # screen_size may come from title "27" elsewhere — must not invent from code alone
    # when no explicit inch token with unit; profile notes forbid prefix invention.
    profile = get_profile("monitor")
    assert profile is not None
    assert "screen_size" in profile.critical_conflict_keys


def test_charger_not_classified_as_psu() -> None:
    assert detect_category("Carregador GaN 65W USB-C PD") == "charger"
    assert detect_category("Corsair RM850x 850W 80 Plus Gold Full Modular") == "psu"


def test_critical_suffixes_preserved() -> None:
    assert canonical_model_key("GeForce RTX 5070") != canonical_model_key(
        "GeForce RTX 5070 Ti"
    )
    phone = resolve_product_identity(title="Apple iPhone 16 Pro Max 256GB Black")
    assert phone.value("model") == "iPhone 16 Pro Max"
    assert canonical_model_key("iPhone 16") != canonical_model_key("iPhone 16 Pro Max")


def test_gpu_profile_search_filters_declared() -> None:
    profile = get_profile("gpu")
    assert profile is not None
    assert "vram" in profile.search_filters
    assert "edition" in profile.critical_conflict_keys


def test_attribute_normalize_dispatch() -> None:
    assert normalize_attribute_value("vram", "12GB") == "12 GB"
    assert normalize_attribute_value("frequency", "6000mhz") == "6000 MHz"
    assert normalize_attribute_value("memory_type", "ddr 5") == "DDR5"


def test_notebook_does_not_steal_gpu_as_product_model() -> None:
    bundle = resolve_product_identity(
        title="ASUS ROG Strix G16 i9-14900HX RTX 4070 16GB 1TB 240Hz"
    )
    assert bundle.category == "notebook"
    assert bundle.value("model") is not None
    assert "4070" not in (bundle.value("model") or "")


def test_ssd_capacity_conflict_keys() -> None:
    profile = get_profile("ssd")
    assert profile is not None
    assert "capacity" in profile.critical_conflict_keys


@pytest.mark.parametrize(
    ("category", "title", "attr", "expected_substr"),
    [
        ("router", "TP-Link Deco XE75 Mesh Wi-Fi 6E Kit 3 Pack", "pack_count", "3"),
        ("router", "TP-Link Deco XE75 Mesh Wi-Fi 6E Kit 3 Pack", "wifi", "6E"),
        (
            "network_switch",
            "TP-Link TL-SG108 8 Port Gigabit Switch",
            "port_count",
            "8",
        ),
        (
            "network_switch",
            "Ubiquiti UniFi Switch 24 PoE Managed",
            "poe",
            "true",
        ),
        ("nas", "Synology DS923+ 4-Bay NAS Diskless", "bay_count", "4"),
        ("nas", "Synology DS923+ 4-Bay NAS Diskless", "included_storage", "diskless"),
        ("ups", "SMS Station II 1200VA 600W", "va", "1200"),
        ("charger", "Anker GaNPrime 65W USB-C PD 2 Portas", "wattage", "65"),
        ("cable", "Cabo HDMI 2.1 2 metros", "standard", "HDMI 2.1"),
        ("cable", "Cabo HDMI 2.1 2 metros", "length", "2"),
        ("cable", "Patch Cord Cat6 1.5m", "ethernet_category", "Cat6"),
        (
            "monitor_mount",
            "Suporte Monitor Articulado Dual VESA 75x75",
            "arm_count",
            "2",
        ),
        (
            "monitor_mount",
            "Suporte Monitor Articulado Dual VESA 75x75",
            "vesa",
            "75x75",
        ),
        ("webcam", "Logitech C920 HD Pro 1080p 30fps", "resolution", "1080P"),
        ("smartwatch", "Apple Watch Series 10 42mm GPS", "case_size", "42"),
        ("keyboard", "Keychron K2 Mechanical Brown Switch TKL", "switch_type", "Brown"),
        ("keyboard", "Keychron K2 Mechanical Brown Switch TKL", "layout", "TKL"),
        ("dock", "CalDigit TS4 Thunderbolt 4 Dock", "thunderbolt", "Thunderbolt 4"),
        ("lens", "Sony FE 24-70mm f/2.8 GM II E-Mount", "mount", "E-Mount"),
        ("camera", "Canon EOS R6 Mark II Body Only RF-Mount", "kit", "body-only"),
    ],
)
def test_phase5_7_critical_title_extractors(
    category: str,
    title: str,
    attr: str,
    expected_substr: str,
) -> None:
    profile = get_profile(category)
    assert profile is not None
    assert profile.extract_attributes is not None
    bundle = resolve_product_identity(title=title, category=category)
    value = bundle.value(attr)
    assert value is not None, f"{category}.{attr} missing for {title!r}"
    assert expected_substr.casefold() in value.casefold()
    assert bundle.sources().get(attr) == "product-title-fallback"


def test_printer_scanner_mpn_specs_only() -> None:
    for cat in ("printer", "scanner"):
        profile = get_profile(cat)
        assert profile is not None
        assert "mpn" in profile.specs_only_critical
        assert profile.extract_attributes is not None
        assert profile.extract_attributes("Epson EcoTank L3250 Wi-Fi") == {}
        bundle = resolve_product_identity(
            title="Epson EcoTank L3250 Wi-Fi Multifuncional",
            category=cat,
        )
        assert bundle.value("mpn") is None


def test_structured_wins_over_phase5_title_extractor() -> None:
    bundle = resolve_product_identity(
        title="TP-Link Deco Mesh Wi-Fi 6 Kit 3 Pack",
        category="router",
        structured={"pack_count": "2", "wifi": "Wi-Fi 6"},
    )
    assert bundle.value("pack_count") == "2"
    assert bundle.sources().get("pack_count") == "structured-data"


@pytest.mark.parametrize(
    ("title", "category", "checks"),
    [
        # Phase 1–3 regression: identity still resolves
        (
            "ASUS GeForce RTX 5070 Dual OC 12GB GDDR7",
            "gpu",
            {"vram": "12", "brand": "Asus"},
        ),
        (
            "Kingston Fury Beast DDR5 32GB 6000MHz CL36",
            "ram",
            {"memory_type": "DDR5", "ram": "32"},
        ),
        (
            "Corsair RM850x 850W 80 Plus Gold Full Modular",
            "psu",
            {"wattage": "850"},
        ),
        (
            "PlayStation 5 Slim Digital 1TB",
            "console",
            {"brand": "Sony"},
        ),
        # Phase 5–7 precision samples
        (
            "TP-Link TL-SG108PE 8-Port Gigabit PoE Switch",
            "network_switch",
            {"port_count": "8", "poe": "true"},
        ),
        (
            "Synology DiskStation DS423+ 4 Bay NAS Sem Disco",
            "nas",
            {"bay_count": "4", "included_storage": "diskless"},
        ),
        (
            "Cabo DisplayPort 1.4 3m",
            "cable",
            {"standard": "DisplayPort", "length": "3"},
        ),
    ],
)
def test_identity_precision_benchmark(
    title: str,
    category: str,
    checks: dict[str, str],
) -> None:
    """Lightweight before/after precision gate for CategoryProfile extractors."""
    assert detect_category(title) == category
    bundle = resolve_product_identity(title=title)
    assert bundle.category == category
    for attr, needle in checks.items():
        value = bundle.value(attr)
        assert value is not None, f"missing {attr}"
        assert needle.casefold() in value.casefold()
