"""Shared helpers to attach timed ``metadata.promotion`` from store payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scout_api.modules.monitoring.promotion import parse_aware_datetime


def promotion_metadata(
    *,
    promotion_type: str,
    expires_at: datetime | None,
    starts_at: datetime | None = None,
    source: str,
    timezone: str | None = None,
    sku: str | None = None,
    product_id: str | None = None,
    conditions: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build monitor-compatible ``metadata.promotion`` or None without expiry."""
    if expires_at is None:
        return None
    aware = (
        expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    )
    payload: dict[str, Any] = {
        "type": promotion_type,
        "expires_at": aware.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "source": source,
    }
    if starts_at is not None:
        start = (
            starts_at if starts_at.tzinfo is not None else starts_at.replace(tzinfo=UTC)
        )
        payload["starts_at"] = start.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if timezone:
        payload["timezone"] = timezone
    if sku:
        payload["sku"] = sku
    if product_id:
        payload["product_id"] = product_id
    if conditions:
        payload["conditions"] = conditions
    if extra:
        payload.update(extra)
    return payload


def shopee_flash_promotion(
    payload: dict[str, Any],
    *,
    model: dict[str, Any] | None = None,
    model_id: str | None = None,
    product_id: str | None = None,
) -> dict[str, Any] | None:
    """Extract flash_sale / deep_discount end_time from Shopee PDP payload."""
    candidates: list[dict[str, Any]] = []
    for block in (
        payload.get("flash_sale"),
        payload.get("flash_sale_preview"),
        payload.get("deep_discount"),
        (model or {}).get("flash_sale") if isinstance(model, dict) else None,
    ):
        if isinstance(block, dict) and block:
            candidates.append(block)

    # Nested under product_price / item sometimes.
    for key in ("product_price", "item", "promo_exclusive_prices"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for sub in ("flash_sale", "flash_sale_preview", "deep_discount"):
                block = nested.get(sub)
                if isinstance(block, dict) and block:
                    candidates.append(block)

    for block in candidates:
        end_raw = (
            block.get("end_time")
            or block.get("end_timestamp")
            or block.get("flash_sale_end_time")
            or block.get("end")
        )
        start_raw = block.get("start_time") or block.get("start_timestamp")
        expires = parse_aware_datetime(end_raw)
        if expires is None:
            continue
        starts = parse_aware_datetime(start_raw)
        return promotion_metadata(
            promotion_type="flash_sale",
            expires_at=expires,
            starts_at=starts,
            source="shopee.payload.flash_sale",
            timezone="UTC",
            sku=model_id,
            product_id=product_id,
            conditions={},
            extra={
                "raw_end": end_raw,
                "flash_sale_type": block.get("flash_sale_type") or block.get("type"),
            },
        )
    return None


def mercadolivre_lightning_promotion(
    html: str,
    *,
    item_id: str | None = None,
    product_id: str | None = None,
) -> dict[str, Any] | None:
    """Parse ``lightning_deal_configuration.finish_date`` from embedded JSON."""
    import re

    # Prefer block keyed by the selected item id when known.
    keys: list[str] = []
    if item_id:
        compact = item_id.upper().replace("-", "")
        keys.append(compact)
        if compact.startswith("MLB") and not compact.startswith("MLBU"):
            # Deals tracking sometimes prefixes MLBU…
            keys.append("MLBU" + compact[3:])
    patterns: list[str] = []
    for key in keys:
        patterns.append(
            rf'"{re.escape(key)}"\s*:\s*\{{[^{{}}]*?'
            rf'lightning_deal_configuration"\s*:\s*\{{\s*"finish_date"\s*:\s*"([^"]+)"'
        )
    patterns.append(
        r'lightning_deal_configuration"\s*:\s*\{\s*"finish_date"\s*:\s*"([^"]+)"'
    )
    when: str | None = None
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I | re.S)
        if match:
            when = match.group(1)
            break
    if when is None:
        # JSON-LD priceValidUntil (date-only) — only as last resort with noon UTC.
        match = re.search(
            r'"priceValidUntil"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
            html,
            flags=re.I,
        )
        if match:
            when = f"{match.group(1)}T23:59:59Z"
            source = "mercadolivre.jsonld.priceValidUntil"
        else:
            return None
    else:
        source = "mercadolivre.lightning_deal_configuration"
    expires = parse_aware_datetime(when, source_timezone="UTC")
    return promotion_metadata(
        promotion_type="lightning_deal",
        expires_at=expires,
        source=source,
        timezone="UTC",
        product_id=product_id,
        extra={"item_id": item_id, "raw": when},
    )


def terabyte_promotion_from_html(
    html: str,
    *,
    product_id: str | None = None,
) -> dict[str, Any] | None:
    from scout_api.modules.monitoring.extractors import extract_terabyte_countdown

    obs = extract_terabyte_countdown(html, product_id=product_id)
    if obs is None or obs.expires_at is None:
        # JSON-LD priceValidUntil (date only → end of day America/Sao_Paulo).
        import re

        match = re.search(
            r'"priceValidUntil"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
            html,
            flags=re.I,
        )
        if not match:
            return None
        expires = parse_aware_datetime(
            f"{match.group(1)} 23:59:59",
            source_timezone="America/Sao_Paulo",
        )
        return promotion_metadata(
            promotion_type="campaign_countdown",
            expires_at=expires,
            source="terabyte.jsonld.priceValidUntil",
            timezone="America/Sao_Paulo",
            product_id=product_id,
            extra={"raw": match.group(1)},
        )
    return promotion_metadata(
        promotion_type=obs.promotion_type or "campaign_countdown",
        expires_at=obs.expires_at,
        starts_at=obs.starts_at,
        source=obs.source or "terabyte.jquery.countdown",
        timezone=obs.timezone,
        product_id=obs.product_id or product_id,
        extra=dict(obs.payload or {}),
    )
