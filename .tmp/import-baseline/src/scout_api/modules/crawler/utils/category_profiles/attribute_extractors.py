"""Category-specific title attribute extractors (Phase 4–7 critical keys).

Only extract when tokens are unambiguous. Prefer ``null`` over guessing.
MPN / manufacturer part numbers are never invented from free text here.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from scout_api.modules.crawler.utils.category_profiles.common import (
    normalize_attribute_value,
    normalize_capacity,
    normalize_length,
    normalize_wattage,
)
from scout_api.modules.crawler.utils.product_identity import fold_identity

_WIFI_GEN = re.compile(
    r"\bwi-?fi\s*(?P<gen>6e|7|6|5|4)\b|\b802\.11\s*(?P<ax>ax|ac|be|n)\b",
    re.I,
)
_PACK = re.compile(
    r"\b(?P<n>[1-3])\s*[- ]?(?:pack|unidade|unidades|node|nodes|kit)\b|"
    r"\bkit\s*(?P<n2>[1-3])\b",
    re.I,
)
_PORTS = re.compile(
    r"\b(?P<n>\d{1,2})\s*[- ]?(?:portas?|ports?)\b",
    re.I,
)
_PORT_SPEED = re.compile(
    r"\b(?P<speed>10g|2\.5g|1g|gigabit|1000|100)\b",
    re.I,
)
_POE = re.compile(r"\bpoe\+?\b|\bpower over ethernet\b", re.I)
_VESA = re.compile(r"\bvesa\s*(?P<v>\d{2,3}\s*[x×]\s*\d{2,3}|\d{2,3})\b", re.I)
_ARMS = re.compile(r"\b(?P<n>[1-4])\s*[- ]?(?:braco|braço|arm|arms|monitor)\b", re.I)
_BAYS = re.compile(r"\b(?P<n>[1-8])\s*[- ]?(?:bay|baia|baias)\b", re.I)
_DISKLESS = re.compile(r"\bdiskless\b|\bsem\s+disco\b|\bsem\s+hd\b", re.I)
_VA = re.compile(r"\b(?P<va>\d{3,4})\s*va\b", re.I)
_WATT = re.compile(r"\b(?P<w>\d{2,4})\s*w\b", re.I)
_VOLTAGE = re.compile(r"\b(?P<v>\d{1,2}(?:[.,]\d+)?)\s*v\b", re.I)
_LENGTH = re.compile(
    r"\b(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>m|cm|mm|metros?)\b",
    re.I,
)
_HDMI = re.compile(r"\bhdmi\s*(?P<ver>2\.1|2\.0|1\.4)?\b", re.I)
_DP = re.compile(
    r"\bdisplayport\s*(?P<ver>2\.1|2\.0|1\.4)?\b|\bdp\s*(?P<ver2>2\.1|1\.4)\b",
    re.I,
)
_USB = re.compile(r"\busb\s*(?P<ver>4|3\.2|3\.1|3\.0|2\.0|c)\b|\busb-c\b", re.I)
_CAT = re.compile(r"\bcat\.?\s*(?P<c>5e|6a|6|7|8)\b", re.I)
_TB = re.compile(r"\bthunderbolt\s*(?P<ver>5|4|3)\b", re.I)
_FAN_SIZE = re.compile(r"\b(?P<mm>120|140|200)\s*mm\b", re.I)
_FAN_COUNT = re.compile(
    r"\b(?:kit|pack)\s*(?P<n>[2-6])\b|\b(?P<n2>[2-6])\s*(?:fans?|ventoinhas?)\b",
    re.I,
)
_RES = re.compile(
    r"\b(?P<res>8k|4k|1440p|1080p|720p|qhd|uhd|fhd|full\s*hd)\b|"
    r"\b(?P<wh>\d{3,4})\s*[x×]\s*(?P<hh>\d{3,4})\b",
    re.I,
)
_CASE_SIZE = re.compile(r"\b(?P<mm>40|41|42|44|45|46|49)\s*mm\b", re.I)
_FOCAL = re.compile(
    r"\b(?P<a>\d{1,3}(?:[.,]\d+)?)\s*(?:-|–|a|to)?\s*(?P<b>\d{1,3}(?:[.,]\d+)?)?\s*mm\b",
    re.I,
)
_MOUNT = re.compile(
    r"\b(?P<m>sony\s*e|fuji\s*x|canon\s*rf|canon\s*ef|nikon\s*z|mft|micro\s*4/3|"
    r"l-mount|e-mount|rf-mount|z-mount)\b",
    re.I,
)
_KIT_LENS = re.compile(r"\bkit\b|\b\+\s*lens\b|\bcom\s+lente\b", re.I)
_BODY = re.compile(r"\bbody\s*only\b|\bsomente\s+corpo\b|\bcorpo\b", re.I)
_SWITCH = re.compile(
    r"\b(?P<sw>red|brown|blue|black|silent|linear|tactile)\s*switch\b|"
    r"\bswitch\s*(?P<sw2>red|brown|blue|black)\b",
    re.I,
)
_LAYOUT = re.compile(r"\b(?P<lay>abnt2|ansi|iso|tkl|60%|65%|75%|full\s*size)\b", re.I)
_CONN = re.compile(
    r"\b(?P<c>wireless|sem\s*fio|bluetooth|wired|com\s*fio|usb|p2|3\.5\s*mm|xlr)\b",
    re.I,
)
_CHANNELS = re.compile(r"\b(?P<ch>2\.1|5\.1|7\.1|mono|stereo)\b", re.I)
_WIDTH = re.compile(r"\b(?P<w>\d{2,3})\s*(?:cm|mm)?\s*(?:largura|width|w)\b", re.I)
_COLOR = re.compile(
    r"\b(?P<color>preto|black|branco|white|cinza|gray|grey|azul|blue|vermelho|red)\b",
    re.I,
)
_PLATFORM = re.compile(
    r"\b(?P<p>ps5|ps4|xbox|pc|switch|android|ios)\b",
    re.I,
)
_EDITION = re.compile(
    r"\b(?P<e>edge|elite|pro|standard|core)\b",
    re.I,
)


def _norm(key: str, value: str | None) -> str | None:
    if not value:
        return None
    return normalize_attribute_value(key, value) or value.strip()


def extract_router_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    wifi = _wifi_generation(title)
    if wifi:
        out["wifi"] = wifi
    pack = _PACK.search(title)
    if pack:
        n = pack.group("n") or pack.group("n2")
        if n:
            out["pack_count"] = str(int(n))
    return out


def extract_access_point_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    wifi = _wifi_generation(title)
    if wifi:
        out["wifi"] = wifi
    if _POE.search(title):
        out["poe"] = "true"
    return out


def extract_wifi_adapter_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    wifi = _wifi_generation(title)
    if wifi:
        out["wifi"] = wifi
    folded = fold_identity(title)
    if "pcie" in folded or "pci-e" in folded:
        out["interface"] = "PCIe"
    elif "usb" in folded:
        out["interface"] = "USB"
    if "bluetooth" in folded or re.search(r"\bbt\b", folded):
        out["bluetooth"] = "true"
    return out


def extract_switch_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ports = _PORTS.search(title)
    if ports:
        out["port_count"] = str(int(ports.group("n")))
    speed = _PORT_SPEED.search(title)
    if speed:
        raw = speed.group("speed").casefold()
        mapping = {
            "10g": "10 GbE",
            "2.5g": "2.5 GbE",
            "1g": "1 GbE",
            "gigabit": "1 GbE",
            "1000": "1 GbE",
            "100": "100 Mbps",
        }
        out["port_speed"] = mapping.get(raw, speed.group("speed"))
    if _POE.search(title):
        out["poe"] = "true"
    folded = fold_identity(title)
    if "gerenciavel" in folded or "managed" in folded:
        out["managed"] = "true"
    elif "unmanaged" in folded or "nao gerenciavel" in folded:
        out["managed"] = "false"
    return out


def extract_webcam_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    res = _resolution(title)
    if res:
        out["resolution"] = res
    fps = re.search(r"\b(?P<fps>\d{2,3})\s*fps\b", title, re.I)
    if fps:
        out["fps"] = f"{int(fps.group('fps'))} FPS"
    return out


def extract_microphone_attrs(title: str) -> dict[str, str]:
    return _connectivity_only(title)


def extract_speaker_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ch = _CHANNELS.search(title)
    if ch:
        out["channels"] = ch.group("ch").replace(" ", "")
    return out


def extract_projector_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    res = _resolution(title)
    if res:
        out["resolution"] = res
    return out


def extract_smartwatch_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    size = _CASE_SIZE.search(title)
    if size:
        out["case_size"] = f"{size.group('mm')} mm"
    folded = fold_identity(title)
    if "cellular" in folded or "lte" in folded:
        out["connectivity"] = "GPS + Cellular"
    elif "gps" in folded:
        out["connectivity"] = "GPS"
    return out


def extract_camera_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    mount = _MOUNT.search(title)
    if mount:
        out["mount"] = re.sub(r"\s+", " ", mount.group("m")).strip().title()
    if _BODY.search(title):
        out["kit"] = "body-only"
    elif _KIT_LENS.search(title):
        out["kit"] = "kit"
    return out


def extract_lens_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    mount = _MOUNT.search(title)
    if mount:
        out["mount"] = re.sub(r"\s+", " ", mount.group("m")).strip().title()
    focal = _FOCAL.search(title)
    if focal:
        a = focal.group("a").replace(",", ".")
        b = focal.group("b")
        if b:
            out["focal_length"] = f"{a}-{b.replace(',', '.')} mm"
        else:
            out["focal_length"] = f"{a} mm"
    return out


def extract_memory_card_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cap = normalize_capacity(title)
    if cap:
        out["capacity"] = cap
    return out


def extract_usb_drive_attrs(title: str) -> dict[str, str]:
    return extract_memory_card_attrs(title)


def extract_nas_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    bay = _BAYS.search(title)
    if bay:
        out["bay_count"] = str(int(bay.group("n")))
    if _DISKLESS.search(title):
        out["included_storage"] = "diskless"
    else:
        cap = normalize_capacity(title)
        # Only treat large TB as included disks when "com" / populated hints exist.
        folded = fold_identity(title)
        if cap and (
            "com " in folded
            or "populated" in folded
            or "hdd" in folded
            or "incluido" in folded
        ):
            out["included_storage"] = cap
    return out


def extract_ups_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    va = _VA.search(title)
    if va:
        out["va"] = f"{int(va.group('va'))} VA"
    watt = normalize_wattage(title)
    if watt:
        out["wattage"] = watt
    return out


def extract_power_strip_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    watt = normalize_wattage(title)
    if watt:
        out["power"] = watt
    outlets = re.search(r"\b(?P<n>\d{1,2})\s*(?:tomadas?|outlets?)\b", title, re.I)
    if outlets:
        out["outlet_count"] = str(int(outlets.group("n")))
    return out


def extract_charger_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    watt = normalize_wattage(title)
    if watt:
        out["wattage"] = watt
    ports = re.search(r"\b(?P<n>[1-4])\s*(?:portas?|ports?)\b", title, re.I)
    if ports:
        out["port_count"] = str(int(ports.group("n")))
    folded = fold_identity(title)
    if "pd" in folded or "power delivery" in folded:
        out["usb_pd"] = "true"
    return out


def extract_laptop_charger_attrs(title: str) -> dict[str, str]:
    out = extract_charger_attrs(title)
    volt = _VOLTAGE.search(title)
    if volt:
        out["voltage"] = f"{volt.group('v').replace(',', '.')} V"
    return out


def extract_dock_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    tb = _TB.search(title)
    if tb:
        out["thunderbolt"] = f"Thunderbolt {tb.group('ver')}"
    usb = _USB.search(title)
    if usb:
        ver = usb.group("ver") or "C"
        out["usb_generation"] = f"USB {ver.upper()}" if ver.upper() != "C" else "USB-C"
    return out


def extract_cable_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    length = _LENGTH.search(title)
    if length:
        unit = length.group("u").lower()
        if unit.startswith("metro"):
            unit = "m"
        out["length"] = normalize_length(f"{length.group('n')} {unit}") or (
            f"{length.group('n')} {unit}"
        )
    hdmi = _HDMI.search(title)
    if hdmi:
        ver = hdmi.group("ver") or ""
        out["standard"] = f"HDMI {ver}".strip()
        out["connector_a"] = "HDMI"
        out["connector_b"] = "HDMI"
    dp = _DP.search(title)
    if dp and "standard" not in out:
        ver = dp.group("ver") or dp.group("ver2") or ""
        out["standard"] = f"DisplayPort {ver}".strip()
        out["connector_a"] = "DisplayPort"
        out["connector_b"] = "DisplayPort"
    cat = _CAT.search(title)
    if cat:
        # Never use key ``category`` — that stores product CategoryProfile id.
        out["ethernet_category"] = f"Cat{cat.group('c').upper()}"
        out["standard"] = out["ethernet_category"]
    usb = _USB.search(title)
    if usb and "standard" not in out:
        ver = usb.group("ver") or "C"
        out["standard"] = f"USB-{ver.upper()}" if ver.upper() == "C" else f"USB {ver}"
    return out


def extract_chair_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    color = _COLOR.search(title)
    if color:
        out["color"] = color.group("color").title()
    return out


def extract_desk_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    width = _WIDTH.search(title)
    if width:
        out["width"] = f"{width.group('w')} cm"
    # Explicit size like 140x60
    size = re.search(r"\b(?P<w>\d{2,3})\s*[x×]\s*(?P<d>\d{2,3})\b", title)
    if size:
        out["width"] = f"{size.group('w')} cm"
        out["depth"] = f"{size.group('d')} cm"
    return out


def extract_monitor_mount_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    vesa = _VESA.search(title)
    if vesa:
        out["vesa"] = re.sub(r"\s+", "", vesa.group("v").upper().replace("×", "x"))
    arms = _ARMS.search(title)
    if arms:
        out["arm_count"] = str(int(arms.group("n")))
    elif re.search(r"\bdual\b|\bduplo\b", title, re.I):
        out["arm_count"] = "2"
    elif re.search(r"\btriple\b|\btriplo\b", title, re.I):
        out["arm_count"] = "3"
    return out


def extract_fan_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    size = _FAN_SIZE.search(title)
    if size:
        out["fan_size"] = f"{size.group('mm')} mm"
    count = _FAN_COUNT.search(title)
    if count:
        n = count.group("n") or count.group("n2")
        if n:
            out["fan_count"] = str(int(n))
    return out


def extract_keyboard_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    sw = _SWITCH.search(title)
    if sw:
        token = (sw.group("sw") or sw.group("sw2") or "").title()
        out["switch_type"] = f"{token} Switch"
    lay = _LAYOUT.search(title)
    if lay:
        out["layout"] = lay.group("lay").upper().replace("FULL SIZE", "Full Size")
    return out


def extract_mouse_attrs(title: str) -> dict[str, str]:
    out = _connectivity_only(title)
    color = _COLOR.search(title)
    if color:
        out["color"] = color.group("color").title()
    return out


def extract_headset_attrs(title: str) -> dict[str, str]:
    return _connectivity_only(title)


def extract_gamepad_attrs(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    plat = _PLATFORM.search(title)
    if plat:
        out["platform"] = plat.group("p").upper()
    ed = _EDITION.search(title)
    if ed and ed.group("e").casefold() in {"edge", "elite", "pro"}:
        out["edition"] = ed.group("e").title()
    conn = _connectivity_only(title)
    out.update(conn)
    return out


def extract_case_attrs(title: str) -> dict[str, str]:
    return extract_chair_attrs(title)


def extract_empty(_title: str) -> dict[str, str]:
    """Specs-only categories (MPN critical): never invent identifiers from title."""
    return {}


def _wifi_generation(title: str) -> str | None:
    match = _WIFI_GEN.search(title)
    if not match:
        if re.search(r"\bwi-?fi\b", title, re.I):
            return "Wi-Fi"
        return None
    gen = match.group("gen")
    if gen:
        return f"Wi-Fi {gen.upper()}"
    ax = (match.group("ax") or "").lower()
    mapping = {"be": "Wi-Fi 7", "ax": "Wi-Fi 6", "ac": "Wi-Fi 5", "n": "Wi-Fi 4"}
    return mapping.get(ax, "Wi-Fi")


def _resolution(title: str) -> str | None:
    match = _RES.search(title)
    if not match:
        return None
    if match.group("res"):
        token = re.sub(r"\s+", " ", match.group("res")).upper()
        token = token.replace("FULL HD", "FHD")
        return token
    return f"{match.group('wh')}x{match.group('hh')}"


def _connectivity_only(title: str) -> dict[str, str]:
    out: dict[str, str] = {}
    conn = _CONN.search(title)
    if not conn:
        return out
    raw = fold_identity(conn.group("c"))
    mapping = {
        "wireless": "Wireless",
        "sem fio": "Wireless",
        "bluetooth": "Bluetooth",
        "wired": "Wired",
        "com fio": "Wired",
        "usb": "USB",
        "p2": "P2",
        "3.5 mm": "P2",
        "xlr": "XLR",
    }
    out["connectivity"] = mapping.get(raw, conn.group("c").title())
    return out


# Registry used by CategoryProfile.extract_attributes
EXTRACTORS: dict[str, Callable[[str], dict[str, str]]] = {
    "router": extract_router_attrs,
    "access_point": extract_access_point_attrs,
    "wifi_adapter": extract_wifi_adapter_attrs,
    "network_switch": extract_switch_attrs,
    "printer": extract_empty,
    "scanner": extract_empty,
    "webcam": extract_webcam_attrs,
    "microphone": extract_microphone_attrs,
    "speaker": extract_speaker_attrs,
    "projector": extract_projector_attrs,
    "smartwatch": extract_smartwatch_attrs,
    "camera": extract_camera_attrs,
    "lens": extract_lens_attrs,
    "memory_card": extract_memory_card_attrs,
    "usb_drive": extract_usb_drive_attrs,
    "nas": extract_nas_attrs,
    "ups": extract_ups_attrs,
    "power_strip": extract_power_strip_attrs,
    "charger": extract_charger_attrs,
    "laptop_charger": extract_laptop_charger_attrs,
    "dock": extract_dock_attrs,
    "cable": extract_cable_attrs,
    "chair": extract_chair_attrs,
    "desk": extract_desk_attrs,
    "monitor_mount": extract_monitor_mount_attrs,
    "fan": extract_fan_attrs,
    "keyboard": extract_keyboard_attrs,
    "mouse": extract_mouse_attrs,
    "headset": extract_headset_attrs,
    "gamepad": extract_gamepad_attrs,
    "case": extract_case_attrs,
}


def extract_profile_attributes(
    category: str | None,
    title: str | None,
) -> dict[str, str]:
    if not category or not title:
        return {}
    fn = EXTRACTORS.get(category)
    if fn is None:
        return {}
    raw = fn(title)
    return {key: value for key, value in raw.items() if value}
