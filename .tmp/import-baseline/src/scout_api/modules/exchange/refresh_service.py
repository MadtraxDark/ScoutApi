"""Exchange-rate refresh service.

Orchestrates provider fetches, validation, persistence and scheduler state.

Failure policy (ADR 0034):
- If ALL providers for a pair fail: keep last-known-good, mark status=stale.
- Never block crawler/match if this service fails.
- SML is a fallback for PYG/BRL when BCP fails.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.modules.exchange.domain import FetchedRate, RateStatus, RateType
from scout_api.modules.exchange.providers.base import ExchangeRateProvider
from scout_api.modules.exchange.providers.bcb_ptax import BcbPtaxProvider
from scout_api.modules.exchange.providers.bcb_sml import BcbSmlProvider
from scout_api.modules.exchange.providers.bcp_referential import BcpReferentialProvider
from scout_api.modules.exchange.providers.valor_data import ValorDataProvider
from scout_api.modules.exchange.repository import ExchangeRateRepository
from scout_api.modules.exchange.schedule import compute_next_refresh_at, is_refresh_due
from scout_api.modules.exchange.validation import validate_batch

logger = logging.getLogger(__name__)


def _fetch_with_fallback(
    primary: ExchangeRateProvider,
    fallback: ExchangeRateProvider | None = None,
    *,
    context: str = "",
) -> tuple[list[FetchedRate], str | None]:
    """Attempt primary; if it fails and fallback is provided, try fallback.

    Returns (rates, error_summary). Rates may be empty on total failure.
    """
    try:
        rates = primary.fetch()
        logger.info(
            "exchange_fetch_ok",
            extra={"provider": primary.source_id, "count": len(rates), "context": context},
        )
        return rates, None
    except Exception as exc:  # noqa: BLE001
        err = f"{primary.source_id}: {exc}"
        logger.warning(
            "exchange_fetch_failed",
            extra={"provider": primary.source_id, "error": str(exc), "context": context},
        )
        if fallback is None:
            return [], err
        try:
            rates = fallback.fetch()
            logger.info(
                "exchange_fetch_fallback_ok",
                extra={
                    "provider": fallback.source_id,
                    "count": len(rates),
                    "context": context,
                    "fallback_used": True,
                },
            )
            return rates, None
        except Exception as exc2:  # noqa: BLE001
            err2 = f"{fallback.source_id}: {exc2}"
            logger.warning(
                "exchange_fetch_fallback_failed",
                extra={"provider": fallback.source_id, "error": str(exc2), "context": context},
            )
            return [], f"{err}; {err2}"


def run_refresh(
    session: Session,
    *,
    settings: Settings | None = None,
    valor_provider: ExchangeRateProvider | None = None,
    ptax_provider: ExchangeRateProvider | None = None,
    bcp_provider: ExchangeRateProvider | None = None,
    sml_provider: ExchangeRateProvider | None = None,
) -> dict[str, Any]:
    """Execute one full refresh cycle.

    Fetches from all providers, validates, persists valid rates, updates
    scheduler state, and appends observations. Returns a summary dict.

    Never raises — all errors are caught and logged. Caller gets the summary.
    """
    cfg = settings or get_settings()
    repo = ExchangeRateRepository(session)
    started = time.perf_counter()

    # Build providers (allow injection for tests)
    valor = valor_provider or ValorDataProvider()
    ptax = ptax_provider or BcbPtaxProvider()
    bcp = bcp_provider or BcpReferentialProvider()
    sml = sml_provider or BcbSmlProvider()

    # ----- override for emergencies / tests ----
    override_tourism_sell: Decimal | None = None
    if cfg.exchange_rate_manual_usd_brl_tourism_sell:
        try:
            override_tourism_sell = Decimal(cfg.exchange_rate_manual_usd_brl_tourism_sell)
            logger.warning(
                "exchange_rate_manual_override_active",
                extra={"override_tourism_sell": str(override_tourism_sell)},
            )
        except Exception:  # noqa: BLE001
            logger.error("exchange_rate_manual_override_invalid")

    all_fetched: list[FetchedRate] = []
    errors: list[str] = []
    fallback_used = False

    # 1. USD/BRL: Valor (tourism + ptax cross-check)
    valor_rates, valor_err = _fetch_with_fallback(valor, context="usd_brl")
    if valor_err:
        errors.append(valor_err)
    else:
        all_fetched.extend(valor_rates)

    # 2. USD/BRL: BCB PTAX (ptax_buy/sell)
    ptax_rates, ptax_err = _fetch_with_fallback(ptax, context="ptax")
    if ptax_err:
        errors.append(ptax_err)
    else:
        all_fetched.extend(ptax_rates)

    # 3. PYG rates: BCP (primary) with SML fallback for PYG/BRL
    bcp_rates, bcp_err = _fetch_with_fallback(bcp, fallback=sml, context="pyg")
    if bcp_err:
        errors.append(bcp_err)
        fallback_used = True
    elif any(r.source == sml.source_id for r in bcp_rates):
        fallback_used = True
    all_fetched.extend(bcp_rates)

    # Apply manual override if configured
    if override_tourism_sell is not None:
        all_fetched = [
            r
            for r in all_fetched
            if not (r.base_currency == "USD" and r.quote_currency == "BRL" and r.rate_type == RateType.TOURISM_SELL)
        ]
        from scout_api.modules.exchange.domain import FetchedRate as FR  # noqa: PLC0415
        all_fetched.append(
            FR(
                base_currency="USD",
                quote_currency="BRL",
                rate_type=RateType.TOURISM_SELL,
                rate=override_tourism_sell,
                source="manual_override",
                source_timestamp=None,
                fetched_at=datetime.now(UTC),
            )
        )

    # Validate
    lkg_map = repo.get_last_known_map()
    valid_rates, rejected = validate_batch(
        all_fetched,
        last_known_map=lkg_map,
        max_change_pct=cfg.exchange_rate_outlier_max_change_pct,
        tourism_max_premium_pct=cfg.exchange_rate_tourism_max_premium_pct,
        ptax_agreement_pct=cfg.exchange_rate_ptax_agreement_pct,
    )

    for rate, errs in rejected:
        errors.append(
            f"rejected {rate.base_currency}/{rate.quote_currency}/{rate.rate_type}: {errs}"
        )

    # Prefer BCB as SoT for PTAX in latest table; Valor PTAX stays in observations
    # only when BCB is present (unique key is pair+rate_type).
    has_bcb_ptax = any(
        r.source == "bcb_ptax" and r.rate_type == RateType.PTAX_SELL for r in valid_rates
    )
    to_persist = [
        r
        for r in valid_rates
        if not (
            has_bcb_ptax
            and r.source == "valor_data"
            and r.rate_type in {RateType.PTAX_BUY, RateType.PTAX_SELL}
        )
    ]

    # Persist valid rates
    persisted = 0
    for rate in to_persist:
        try:
            repo.upsert_latest(rate, status=RateStatus.FRESH)
            repo.append_observation(rate)
            persisted += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("exchange_rate_persist_failed", extra={"error": str(exc)})
            errors.append(f"persist: {exc}")

    # Still record Valor PTAX as observation for audit when BCB won the latest slot
    if has_bcb_ptax:
        for rate in valid_rates:
            if rate.source == "valor_data" and rate.rate_type in {
                RateType.PTAX_BUY,
                RateType.PTAX_SELL,
            }:
                try:
                    repo.append_observation(rate)
                except Exception:  # noqa: BLE001
                    pass

    # Mark stale
    repo.mark_stale(stale_after_seconds=cfg.exchange_rate_stale_after_seconds)

    # Update scheduler state
    now = datetime.now(UTC)
    next_refresh = compute_next_refresh_at(
        now=now,
        refresh_interval_seconds=cfg.exchange_rate_refresh_interval_seconds,
    )
    success = len(valid_rates) > 0
    repo.update_scheduler_state(
        next_refresh_at=next_refresh,
        last_refresh_at=now,
        last_success_at=now if success else None,
        last_error="; ".join(errors[:3]) if errors else None,
        consecutive_failures=0 if success else None,
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary = {
        "fetched": len(all_fetched),
        "valid": len(valid_rates),
        "rejected": len(rejected),
        "persisted": persisted,
        "errors": errors,
        "fallback_used": fallback_used,
        "duration_ms": duration_ms,
        "next_refresh_at": next_refresh.isoformat(),
    }
    logger.info("exchange_rate_refresh_done", extra=summary)
    return summary


def refresh_if_due(
    session: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    **provider_kwargs: Any,
) -> dict[str, Any] | None:
    """Run refresh only if scheduled time has passed.

    Returns the summary dict if refresh was run, None if not due.
    """
    cfg = settings or get_settings()
    repo = ExchangeRateRepository(session)
    state = repo.get_scheduler_state()
    if not is_refresh_due(
        state.next_refresh_at if state else None,
        now=now,
    ):
        return None
    return run_refresh(session, settings=cfg, **provider_kwargs)
