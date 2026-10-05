"""Exchange-rate API router.

Endpoints:
  GET  /exchange-rates               — list rates (requires products:read)
  GET  /exchange-rates/diagnostics   — admin diagnostics
  POST /exchange-rates/refresh       — trigger manual refresh (admin)

Security: DENY BY DEFAULT — all endpoints require auth.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from scout_api.core.database import get_db_session
from scout_api.modules.auth.deps import require_admin, require_permission
from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.exchange.models import ExchangeRateLatest
from scout_api.modules.exchange.repository import ExchangeRateRepository
from scout_api.modules.exchange.schemas import (
    ExchangeRateDiagnosticsResponse,
    ExchangeRateListResponse,
    ExchangeRateRefreshResponse,
    ExchangeRateView,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exchange-rates", tags=["Câmbio"])

_require_read = require_permission("products:read")


def _row_to_view(row: ExchangeRateLatest) -> ExchangeRateView:
    return ExchangeRateView(
        base_currency=row.base_currency,
        quote_currency=row.quote_currency,
        rate_type=row.rate_type,
        rate=row.rate,  # type: ignore[arg-type]
        source=row.source,
        source_timestamp=row.source_timestamp,
        fetched_at=row.fetched_at,
        status=row.status,
        consecutive_failures=row.consecutive_failures,
        updated_at=row.updated_at,
    )


@router.get(
    "",
    response_model=ExchangeRateListResponse,
    summary="Listar taxas de câmbio",
    description="Retorna todas as taxas de câmbio persistidas (last-known-good).",
)
def list_exchange_rates(
    _: Annotated[AuthenticatedPrincipal, Depends(_require_read)],
    session: Annotated[Session, Depends(get_db_session)],
    base_currency: str | None = None,
    quote_currency: str | None = None,
) -> ExchangeRateListResponse:
    repo = ExchangeRateRepository(session)
    rows = repo.list_latest(base_currency=base_currency, quote_currency=quote_currency)
    items = [_row_to_view(r) for r in rows]
    return ExchangeRateListResponse(items=items, count=len(items))


@router.get(
    "/diagnostics",
    response_model=ExchangeRateDiagnosticsResponse,
    summary="Diagnóstico do subsistema de câmbio",
    description="Retorna estado detalhado do scheduler e das taxas. Acesso restrito a admins.",
)
def exchange_diagnostics(
    _: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_db_session)],
) -> ExchangeRateDiagnosticsResponse:
    repo = ExchangeRateRepository(session)
    rows = repo.list_latest()
    items = [_row_to_view(r) for r in rows]
    state = repo.get_scheduler_state()
    scheduler_info = {}
    if state:
        scheduler_info = {
            "next_refresh_at": state.next_refresh_at.isoformat() if state.next_refresh_at else None,
            "last_refresh_at": state.last_refresh_at.isoformat() if state.last_refresh_at else None,
            "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
            "last_error": state.last_error,
            "consecutive_failures": state.consecutive_failures,
        }

    stale_count = sum(1 for r in rows if r.status == "stale")
    unavailable = [
        f"{r.base_currency}/{r.quote_currency}/{r.rate_type}"
        for r in rows
        if r.status == "unavailable"
    ]

    return ExchangeRateDiagnosticsResponse(
        rates=items,
        scheduler=scheduler_info,
        stale_count=stale_count,
        unavailable_pairs=unavailable,
    )


@router.post(
    "/refresh",
    response_model=ExchangeRateRefreshResponse,
    summary="Forçar atualização das taxas de câmbio",
    description="Dispara um refresh imediato de todos os provedores. Acesso restrito a admins.",
)
def trigger_refresh(
    _: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_db_session)],
) -> ExchangeRateRefreshResponse:
    from scout_api.modules.exchange.refresh_service import run_refresh  # noqa: PLC0415

    try:
        summary = run_refresh(session)
        session.commit()
        return ExchangeRateRefreshResponse(
            fetched=summary.get("fetched", 0),
            valid=summary.get("valid", 0),
            rejected=summary.get("rejected", 0),
            persisted=summary.get("persisted", 0),
            errors=summary.get("errors", []),
            fallback_used=summary.get("fallback_used", False),
            duration_ms=summary.get("duration_ms", 0),
            next_refresh_at=summary.get("next_refresh_at"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("exchange_rate_manual_refresh_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "EXCHANGE_REFRESH_FAILED", "message": str(exc)},
        ) from exc
