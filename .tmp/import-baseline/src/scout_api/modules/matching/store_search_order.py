"""Order live-search targets so GTIN-rich stores run first.

When the reference listing has no trusted barcode, discovering a check-digit-valid
GTIN early (via ``auto_match``) lets remaining SERPs switch to GTIN-first queries.

Stores are grouped by Camoufox locale affinity. Playwright Sync keeps **one**
warm context: mixing pt-BR ↔ es-PY ↔ en-US mid-flight forces relaunch. Waves:

1. GTIN stores in the dominant locale (serial) — barcode learning
2. Remaining stores in that locale (parallel) — HTTP overlap
3. Other locales (serial, locale-sorted) — after the dominant cluster
"""

from __future__ import annotations

from collections import Counter

# Lower rank → search earlier. Based on how often PDPs expose GTIN/EAN/UPC
# in our spiders (structured data / specs), not on marketplace popularity.
GTIN_EXPOSURE_RANK: dict[str, int] = {
    "kabum": 10,  # EAN / código de barras in technicalInformation
    "bestbuy": 20,  # UPC / gtin* in product JSON frequently
    "nissei": 30,  # UPC attribute on PDP
    "shoppingchina": 40,  # flix EAN hooks when present
    "amazon_br": 50,  # detail tables / JSON-LD sometimes
    "amazon_us": 55,
    "magazineluiza": 70,  # extractor exists; often absent on PDP
    "mercadolivre": 75,  # catalog ids strong; GTIN often absent in JSON-LD
    "shopee": 90,  # marketplace; barcode often missing
    "aliexpress": 92,  # marketplace; GTIN rare in MTop props
    "visaovip": 95,
}

# Camoufox ``locale_for_url`` clusters — lower runs earlier to cut fingerprint thrash.
LOCALE_AFFINITY_RANK: dict[str, int] = {
    # pt-BR — majority of BR catalog stores
    "kabum": 10,
    "amazon_br": 10,
    "magazineluiza": 10,
    "mercadolivre": 10,
    "pichau": 10,
    "terabyteshop": 10,
    "shopee": 10,
    "aliexpress": 10,
    "visaovip": 10,
    "shoppingchina": 10,
    # es-PY
    "nissei": 20,
    # en-US
    "amazon_us": 40,
    "bestbuy": 40,
}

_DEFAULT_RANK = 100
_DEFAULT_LOCALE_RANK = 50
_PT_BR_LOCALE_RANK = 10

# Stores at or below this rank run in wave-1 (GTIN discovery) before the
# parallel wave — preserves mid-flight barcode learning for later SERPs.
GTIN_WAVE_RANK_MAX = 55


def gtin_exposure_rank(store_key: str) -> int:
    return GTIN_EXPOSURE_RANK.get(store_key.strip().lower(), _DEFAULT_RANK)


def locale_affinity_rank(store_key: str) -> int:
    return LOCALE_AFFINITY_RANK.get(store_key.strip().lower(), _DEFAULT_LOCALE_RANK)


def _store_sort_key(store: str) -> tuple[int, int, str]:
    return (locale_affinity_rank(store), gtin_exposure_rank(store), store)


def _dominant_locale_rank(stores: list[str]) -> int:
    """Prefer pt-BR when present; otherwise the most common affinity rank."""
    if not stores:
        return _PT_BR_LOCALE_RANK
    ranks = [locale_affinity_rank(s) for s in stores]
    if _PT_BR_LOCALE_RANK in ranks:
        return _PT_BR_LOCALE_RANK
    return Counter(ranks).most_common(1)[0][0]


def split_stores_for_match_waves(
    stores: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Split into GTIN/locale serial → same-locale parallel → other locales.

    Returns ``(wave1, wave2, wave3)``:

    - wave1: GTIN-rich stores in the dominant locale (serial)
    - wave2: remaining dominant-locale stores (parallel-safe)
    - wave3: every other locale (serial; locale-sorted)
    """
    gtin: list[str] = []
    rest: list[str] = []
    for store in stores:
        if gtin_exposure_rank(store) <= GTIN_WAVE_RANK_MAX:
            gtin.append(store)
        else:
            rest.append(store)

    dominant = _dominant_locale_rank(stores)
    wave1 = sorted(
        [s for s in gtin if locale_affinity_rank(s) == dominant],
        key=_store_sort_key,
    )
    wave2 = sorted(
        [s for s in rest if locale_affinity_rank(s) == dominant],
        key=_store_sort_key,
    )
    other = [s for s in gtin + rest if locale_affinity_rank(s) != dominant]
    # Preserve GTIN-first within non-dominant locales, then locale groups.
    wave3 = sorted(
        other,
        key=lambda s: (
            locale_affinity_rank(s),
            0 if gtin_exposure_rank(s) <= GTIN_WAVE_RANK_MAX else 1,
            gtin_exposure_rank(s),
            s,
        ),
    )
    return wave1, wave2, wave3


def order_stores_for_match(
    stores: list[str],
    *,
    reference_store: str | None = None,
    reference_has_gtin: bool = False,
) -> list[str]:
    """Return stores ordered for precision-first discovery.

    - Prefer stores that typically publish GTIN/EAN/UPC.
    - Group by Camoufox locale affinity to reduce warm relaunch thrash.
    - If the reference still lacks a barcode, push the reference store later
      (its PDP was already scraped without GTIN).
    """
    seen: set[str] = set()
    unique: list[str] = []
    for raw in stores:
        key = raw.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)

    ref = (reference_store or "").strip().lower() or None

    def sort_key(store: str) -> tuple[int, int, int, str]:
        deprioritize_ref = (
            1 if (not reference_has_gtin and ref is not None and store == ref) else 0
        )
        return (
            deprioritize_ref,
            locale_affinity_rank(store),
            gtin_exposure_rank(store),
            store,
        )

    return sorted(unique, key=sort_key)
