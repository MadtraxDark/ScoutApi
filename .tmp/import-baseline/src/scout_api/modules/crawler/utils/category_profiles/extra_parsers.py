"""Additional category-aware title parsers (motherboard, notebook, console, …)."""

from __future__ import annotations

import re

from scout_api.modules.crawler.utils.product_identity import (
    ParsedIdentity,
    _display_token,
    _title_tokens,
    canonical_variant_key,
    fold_identity,
)

_CHIPSET_RE = re.compile(
    r"\b(?P<chip>[ABZXH]\d{3}(?:E|M)?|[BbZzXx]\d{3}(?:[Ee]|[Mm])?-?(?:Plus|Pro|I|F)?)\b"
)
_SOCKET_RE = re.compile(r"\b(?P<sock>AM[45]|LGA\s?\d{3,4}|sTR[Xx5]\d*)\b", re.I)
_WIFI_RE = re.compile(r"\bwi-?fi\b|\bwifi\b", re.I)
_FORM_RE = re.compile(r"\b(e-?atx|matx|mini-?itx|atx)\b", re.I)

_IPAD_RE = re.compile(
    r"\bipad\s*(?P<line>air|pro|mini)?\s*(?P<size>\d{1,2}(?:\.\d)?)?",
    re.I,
)
_CONSOLE_RE = re.compile(
    r"\b(?:playstation\s*(?P<ps>\d)|ps(?P<ps2>\d)|xbox\s*series\s*(?P<xb>[xs])|"
    r"nintendo\s*switch(?:\s*(?P<nsw>oled|lite))?)\b",
    re.I,
)
_PSU_WATT = re.compile(r"\b(?P<w>\d{3,4})\s*w\b", re.I)
_MONITOR_MODEL = re.compile(r"\b(?P<code>[A-Z0-9]+(?:-[A-Z0-9]+)*)\b", re.I)
_MONITOR_MODEL_NOISE = frozenset({"displayport", "hdmi", "usb"})
_RADIATOR = re.compile(r"\b(?P<mm>120|240|280|360|420)\s*mm\b", re.I)


def parse_motherboard(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    lead_noise = {
        "placa",
        "mae",
        "mãe",
        "placa-mae",
        "placa-mãe",
        "motherboard",
        "board",
    }
    while tokens and fold_identity(tokens[0]).replace(" ", "") in {
        fold_identity(x).replace(" ", "") for x in lead_noise
    }:
        tokens = tokens[1:]
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    chip = _CHIPSET_RE.search(title)
    if chip is None and not _SOCKET_RE.search(title):
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0]) if tokens else None
    # Structural / marketing tokens that belong in attributes, not model.
    stop_noise = {
        "placa",
        "mae",
        "mãe",
        "motherboard",
        "board",
        "wifi",
        "wi-fi",
        "ddr4",
        "ddr5",
        "am4",
        "am5",
        "socket",
        "soquete",
        "chipset",
        "amd",
        "intel",
        "matx",
        "m-atx",
        "micro-atx",
        "microatx",
        "atx",
        "e-atx",
        "mini-itx",
        "itx",
        "form",
        "factor",
        "formato",
    }
    model_parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        folded = fold_identity(token).replace(" ", "")
        if lower in stop_noise or folded in stop_noise:
            # Once the commercial board code is collected, stop at structural specs.
            if model_parts:
                break
            continue
        if _SOCKET_RE.fullmatch(token):
            if model_parts:
                break
            continue
        if re.fullmatch(r"ddr[45]", lower):
            if model_parts:
                break
            continue
        if _FORM_RE.fullmatch(token):
            if model_parts:
                break
            continue
        if _WIFI_RE.fullmatch(token) or lower in {"wifi", "wi-fi"}:
            continue
        model_parts.append(_display_token(token))
        if len(model_parts) >= 6:
            break
    # Prefer chipset-centered model when we have one.
    if chip:
        chip_disp = chip.group("chip").upper().replace("M", "M")
        # Find slice containing chipset
        display = " ".join(model_parts) if model_parts else chip_disp
        # Ensure chipset string appears
        if fold_identity(chip_disp) not in fold_identity(display):
            display = f"{display} {chip_disp}".strip() if display else chip_disp
    else:
        display = " ".join(model_parts) if model_parts else None
    if not display:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    # Drop trailing Wi-Fi tokens from model (kept as variant).
    display = re.sub(r"\s+wi-?fi\b", "", display, flags=re.I).strip()
    # Drop accidental structural tails that slipped past tokenization.
    display = re.sub(
        r"\s+(socket|chipset|ddr[45]|m-?atx|atx|amd|intel)\b.*$",
        "",
        display,
        flags=re.I,
    ).strip()
    # Product line (TUF / ROG / MAG) when leading marketing token.
    product_line = None
    for token in model_parts[:2]:
        if token.upper() in {"TUF", "ROG", "MAG", "MPG", "PRO", "AORUS", "STRIX"}:
            product_line = token.upper() if len(token) <= 4 else token
            break
    key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
    variant = None
    variant_key = None
    if _WIFI_RE.search(title):
        variant = "WiFi"
        variant_key = "wifi"
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=variant,
        variant_key=variant_key,
        confidence="contextual",
        product_line=product_line,
    )


def parse_notebook(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    skip = {
        "notebook",
        "laptop",
        "ultrabook",
        "com",
        "de",
        "e",
        "the",
        "pc",
    }
    model_parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        if lower in skip:
            continue
        # Stop before CPU/GPU/capacity blocks.
        if re.match(r"^(i[3579]|ryzen|rtx|gtx|core)", lower):
            break
        if re.fullmatch(r"\d+(gb|tb)", lower):
            break
        if re.fullmatch(r"\d+(?:\.\d+)?(\"|\')?", token):
            break
        model_parts.append(_display_token(token))
        if len(model_parts) >= 5:
            break
    if not model_parts:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    display = " ".join(model_parts)
    key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=None,
        variant_key=None,
        confidence="contextual",
    )


def parse_tablet(title: str, category: str) -> ParsedIdentity:
    folded = fold_identity(title)
    if "ipad" in folded:
        match = _IPAD_RE.search(title)
        if not match:
            return ParsedIdentity(
                category, "Apple", None, None, None, None, "ambiguous"
            )
        line = (match.group("line") or "").title()
        size = match.group("size")
        display = "iPad"
        if line:
            display += f" {line}"
        if size:
            display += f" {size}"
        key = re.sub(r"[^a-z0-9]+", "", fold_identity(display))
        variant = None
        if "cellular" in folded or "lte" in folded:
            variant = "Wi-Fi + Cellular"
        elif "wi-fi" in folded or "wifi" in folded:
            variant = "Wi-Fi"
        return ParsedIdentity(
            category=category,
            brand="Apple",
            model=display,
            model_key=key,
            variant=variant,
            variant_key=canonical_variant_key(variant) if variant else None,
            confidence="exact_title",
        )
    # Generic Android tablet: brand + first model-ish tokens until storage.
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    parts: list[str] = []
    for token in tokens[1:]:
        if re.fullmatch(r"\d+(gb|tb)", token.casefold()):
            break
        if token.casefold() in {"tablet", "tab"}:
            continue
        parts.append(_display_token(token))
        if len(parts) >= 4:
            break
    if not parts:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    display = " ".join(parts)
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
        variant=None,
        variant_key=None,
        confidence="contextual",
    )


def parse_console(title: str, category: str) -> ParsedIdentity:
    match = _CONSOLE_RE.search(title)
    if not match:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    folded = fold_identity(title)
    brand = None
    display = None
    if match.group("ps") or match.group("ps2"):
        gen = match.group("ps") or match.group("ps2")
        brand = "Sony"
        display = f"PlayStation {gen}"
        if "pro" in folded and f"ps{gen}" in folded.replace(" ", ""):
            display = f"PlayStation {gen} Pro"
        elif "slim" in folded:
            display = f"PlayStation {gen} Slim"
    elif match.group("xb"):
        brand = "Microsoft"
        series = match.group("xb").upper()
        display = f"Xbox Series {series}"
    else:
        brand = "Nintendo"
        display = "Nintendo Switch"
        if match.group("nsw"):
            display += f" {match.group('nsw').upper()}"
    key = re.sub(r"[^a-z0-9]+", "", fold_identity(display or ""))
    variant = None
    if re.search(r"\bdigital\b", folded):
        variant = "Digital"
    elif re.search(r"\bdisc\b|\bedicao\s+fisica\b|\bblu-?ray\b", folded):
        variant = "Disc"
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=key,
        variant=variant,
        variant_key=canonical_variant_key(variant) if variant else None,
        confidence="exact_title",
    )


def parse_psu(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    watt = _PSU_WATT.search(title)
    folded = fold_identity(title)
    if watt is None and "fonte" not in folded and "psu" not in folded:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    # Model: tokens until wattage / 80 plus
    parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        if re.fullmatch(r"\d{3,4}w?", lower):
            break
        if "80" in lower or lower in {
            "plus",
            "gold",
            "platinum",
            "bronze",
            "full",
            "modular",
        }:
            break
        if lower in {"fonte", "psu", "atx"}:
            continue
        parts.append(_display_token(token))
        if len(parts) >= 4:
            break
    display = " ".join(parts) if parts else None
    if display and watt:
        # Keep model without forcing watt into model (watt is attribute).
        pass
    if not display:
        display = f"{watt.group('w')}W" if watt else None
    if not display:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
        variant=None,
        variant_key=None,
        confidence="contextual",
    )


def parse_cooler(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        if lower in {"water", "cooler", "aio", "liquid", "air", "cpu"}:
            continue
        if _RADIATOR.fullmatch(token) or re.fullmatch(r"\d{3}mm", lower):
            break
        parts.append(_display_token(token))
        if len(parts) >= 5:
            break
    if not parts:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    display = " ".join(parts)
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
        variant=None,
        variant_key=None,
        confidence="contextual",
    )


def parse_monitor(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    while tokens and tokens[0].casefold() in {"monitor", "gamer", "gaming"}:
        tokens = tokens[1:]
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    # Prefer alphanumeric model codes (27GS95QE) over inventing size from prefix.
    codes: list[str] = []
    for match in _MONITOR_MODEL.finditer(title):
        code = match.group("code").strip("-")
        compact = re.sub(r"[^A-Z0-9]", "", code.upper())
        if (
            len(compact) < 5
            or not re.search(r"[A-Z]", compact)
            or not re.search(r"\d", compact)
        ):
            continue
        folded = compact.casefold()
        if (
            folded in _MONITOR_MODEL_NOISE
            or folded.startswith(("hdr", "hdmi", "displayport", "usb", "bt"))
            or folded.endswith(
                ("hz", "khz", "mhz", "ghz", "ms", "mm", "cm", "in", "bit", "bpc")
            )
            or re.fullmatch(r"\d{3,4}x\d{3,4}", folded)
            or re.fullmatch(r"dci-?p3", code, re.I)
        ):
            continue
        codes.append(code)
    code_keys = {re.sub(r"[^a-z0-9]", "", fold_identity(code)) for code in codes}
    parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        compact = re.sub(r"[^a-z0-9]", "", fold_identity(token))
        if compact in code_keys:
            break
        if lower in {"monitor", "ultragear", "gaming"}:
            if lower == "ultragear":
                parts.append("UltraGear")
            continue
        if re.search(r"\d+\s*(hz|\"|polegada)", lower) or re.fullmatch(
            r"\d+(?:\.\d+)?\"?", token
        ):
            break
        parts.append(_display_token(token))
        if len(parts) >= 4:
            break
    product_line = " ".join(parts) or None
    if codes:
        # Pick the longest distinctive code after brand.
        display = max(codes, key=len)
        return ParsedIdentity(
            category=category,
            brand=brand,
            model=display.upper(),
            model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
            variant=None,
            variant_key=None,
            confidence="exact_title",
            product_line=product_line,
        )
    if not parts:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    display = " ".join(parts)
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
        variant=None,
        variant_key=None,
        confidence="contextual",
        product_line=display,
    )


def parse_peripheral_generic(title: str, category: str) -> ParsedIdentity:
    """Brand + short model line for keyboard/mouse/headset/gamepad/case fans."""
    tokens = _title_tokens(title)
    if len(tokens) < 2:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    lead_noise = {
        "carregador",
        "charger",
        "cabo",
        "cable",
        "teclado",
        "mouse",
        "headset",
        "headphone",
        "fone",
        "controle",
        "gamepad",
        "gabinete",
        "case",
        "fan",
        "ventoinha",
        "suporte",
        "mesa",
        "cadeira",
        "hub",
        "dock",
        "adaptador",
        "roteador",
        "router",
    }
    while tokens and tokens[0].casefold() in lead_noise:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    if brand.casefold() in lead_noise or brand.casefold() in {
        "gan",
        "usb",
        "usb-c",
        "type-c",
    }:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    stop = {
        "rgb",
        "argb",
        "wireless",
        "wired",
        "bluetooth",
        "usb",
        "abnt2",
        "switch",
        "red",
        "brown",
        "blue",
        "black",
        "white",
        "teclado",
        "mouse",
        "headset",
        "headphone",
        "controle",
        "gamepad",
        "fan",
        "cooler",
        "gabinete",
        "case",
        "pwm",
        "gan",
        "pd",
    }
    parts: list[str] = []
    for token in tokens[1:]:
        lower = token.casefold()
        if lower in stop and parts:
            break
        if lower in stop and not parts:
            continue
        if re.fullmatch(r"\d+mm", lower) or re.fullmatch(r"\d+w", lower):
            break
        parts.append(_display_token(token))
        if len(parts) >= 5:
            break
    if not parts:
        return ParsedIdentity(category, brand, None, None, None, None, "ambiguous")
    display = " ".join(parts)
    return ParsedIdentity(
        category=category,
        brand=brand,
        model=display,
        model_key=re.sub(r"[^a-z0-9]+", "", fold_identity(display)),
        variant=None,
        variant_key=None,
        confidence="contextual",
    )


def parse_network_generic(title: str, category: str) -> ParsedIdentity:
    return parse_peripheral_generic(title, category)


def parse_tv(title: str, category: str) -> ParsedIdentity:
    tokens = _title_tokens(title)
    if not tokens:
        return ParsedIdentity(category, None, None, None, None, None, "ambiguous")
    brand = _display_token(tokens[0])
    # Series codes like S90D / QN90
    series = re.search(r"\b([A-Z]{1,3}\d{2}[A-Z0-9]{0,4})\b", title)
    if series and len(series.group(1)) >= 3:
        display = series.group(1).upper()
        return ParsedIdentity(
            category=category,
            brand=brand,
            model=display,
            model_key=fold_identity(display).replace(" ", ""),
            variant=None,
            variant_key=None,
            confidence="exact_title",
        )
    return parse_peripheral_generic(title, category)
