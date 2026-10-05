"""Store-agnostic + store-specific timed-promotion extractors.

Only emit an observation when a reliable absolute expiry is found.
Do not invent timers from relative countdowns without an anchor timestamp.
"""

from __future__ import annotations

import re
from typing import Any

from scout_api.modules.crawler.models.product import ProductOffer
from scout_api.modules.monitoring.promotion import (
    PromotionObservation,
    observation_from_metadata,
    parse_aware_datetime,
)

# Terabyte: jQuery Countdown plugin — absolute local datetime in script.
_TERABYTE_COUNTDOWN_RE = re.compile(
    r"""\$\(\s*['"]#ctd(?P<pid>\d+)['"]\s*\)\.countdown\(\s*['"](?P<when>[^'"]+)['"]""",
    re.IGNORECASE,
)

# Mercado Livre public PDP / deals embed lightning finish dates.
_ML_LIGHTNING_RE = re.compile(
    r'lightning_deal_configuration"\s*:\s*\{\s*"finish_date"\s*:\s*"(?P<when>[^"]+)"',
    re.IGNORECASE,
)
_ML_FINISH_RE = re.compile(
    r"""["'](?:finish_date|expires_at|end_time|stop_time)["']\s*:\s*["'](?P<when>[^"']+)["']""",
    re.IGNORECASE,
)

# Shopee flash sale unix fields near model payloads.
_SHOPEE_FLASH_RE = re.compile(
    r"""["'](?:end_time|end_timestamp|flash_sale_end_time)["']\s*:\s*(?P<when>\d{10,13})""",
    re.IGNORECASE,
)


def extract_promotion(
    *,
    store: str,
    html: str | None = None,
    offer: ProductOffer | None = None,
    metadata: dict[str, Any] | None = None,
) -> PromotionObservation | None:
    meta = metadata
    if meta is None and offer is not None:
        meta = offer.metadata
    from_meta = observation_from_metadata(meta, offer=offer)
    if from_meta is not None and from_meta.expires_at is not None:
        return from_meta

    store_key = (store or "").strip().casefold()
    if html:
        if store_key in {"terabyteshop", "terabyte"}:
            found = extract_terabyte_countdown(
                html,
                product_id=offer.product_id if offer else None,
            )
            if found is not None:
                return found
        if store_key in {"mercadolivre", "mercado_livre", "meli"}:
            found = extract_mercadolivre_finish(html, offer=offer)
            if found is not None:
                return found
        if store_key == "shopee":
            found = extract_shopee_flash_end(html, offer=offer)
            if found is not None:
                return found
    return from_meta


def extract_terabyte_countdown(
    html: str,
    *,
    product_id: str | None = None,
) -> PromotionObservation | None:
    """Parse ``$('#ctd{product_id}').countdown('YYYY/MM/DD HH:MM:SS')``."""
    matches = list(_TERABYTE_COUNTDOWN_RE.finditer(html))
    if not matches:
        return None
    chosen = None
    if product_id:
        for match in matches:
            if match.group("pid") == str(product_id):
                chosen = match
                break
    if chosen is None:
        chosen = matches[0]
    when = parse_aware_datetime(
        chosen.group("when"),
        source_timezone="America/Sao_Paulo",
    )
    if when is None:
        return None
    return PromotionObservation(
        status="active",
        promotion_type="campaign_countdown",
        expires_at=when,
        source="terabyte.jquery.countdown",
        timezone="America/Sao_Paulo",
        product_id=chosen.group("pid"),
        payload={"raw": chosen.group("when")},
    )


def extract_mercadolivre_finish(
    html: str,
    *,
    offer: ProductOffer | None = None,
) -> PromotionObservation | None:
    match = _ML_LIGHTNING_RE.search(html) or _ML_FINISH_RE.search(html)
    if match is None:
        return None
    when = parse_aware_datetime(match.group("when"), source_timezone="UTC")
    if when is None:
        return None
    source = (
        "mercadolivre.lightning_deal_configuration"
        if match.re is _ML_LIGHTNING_RE
        else "mercadolivre.html.finish_date"
    )
    return PromotionObservation(
        status="active",
        promotion_type="lightning_deal",
        expires_at=when,
        source=source,
        timezone="UTC",
        product_id=offer.product_id if offer else None,
        sku=offer.sku if offer else None,
        payload={"raw": match.group("when")},
    )


def extract_shopee_flash_end(
    html: str,
    *,
    offer: ProductOffer | None = None,
) -> PromotionObservation | None:
    match = _SHOPEE_FLASH_RE.search(html)
    if match is None:
        return None
    when = parse_aware_datetime(int(match.group("when")))
    if when is None:
        return None
    return PromotionObservation(
        status="active",
        promotion_type="flash_sale",
        expires_at=when,
        source="shopee.payload.end_time",
        timezone="UTC",
        product_id=offer.product_id if offer else None,
        sku=offer.sku if offer else None,
        payload={"raw_unix": match.group("when")},
    )
