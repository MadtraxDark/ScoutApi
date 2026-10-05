"""Admin diagnostics for persistent offer monitoring."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from scout_api.core.database import get_db_session
from scout_api.modules.auth import AuthenticatedPrincipal, require_admin
from scout_api.modules.monitoring.service import OfferMonitorService

router = APIRouter(prefix="/monitor", tags=["Monitoramento"])


class MonitorDiagnosticsResponse(BaseModel):
    monitored_active: int
    due_count: int
    claimed_count: int
    failed_count: int
    oldest_due_check_at: str | None = None
    max_delay_seconds: int | None = None
    next_check_at: str | None = None
    promotions_expiring_now: int = 0
    promotions_expired_awaiting_refresh: int = 0
    scheduler_last_heartbeat_at: str | None = None
    scheduler_worker_id: str | None = None
    scheduler_last_claimed_count: int = 0


class MonitorSweepResponse(BaseModel):
    worker_id: str
    claimed: int
    processed: int
    duration_ms: int
    due_remaining: int
    results: list[dict[str, Any]] = Field(default_factory=list)


@router.get(
    "/diagnostics",
    response_model=MonitorDiagnosticsResponse,
    summary="Diagnosticar monitoramento de ofertas",
    description=(
        "Resumo admin de listings monitorados, checks vencidos, leases e "
        "heartbeat do worker."
    ),
)
def monitor_diagnostics(
    _: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_db_session)],
) -> MonitorDiagnosticsResponse:
    payload = OfferMonitorService(session=session).diagnostics()
    return MonitorDiagnosticsResponse.model_validate(payload)


@router.post(
    "/sweep",
    response_model=MonitorSweepResponse,
    summary="Executar sweep de monitoramento",
    description=(
        "Processa um lote de listings com next_check_at vencido. "
        "Uso admin/operacional; o worker dedicado é o caminho normal."
    ),
)
def monitor_sweep(
    _: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    session: Annotated[Session, Depends(get_db_session)],
) -> MonitorSweepResponse:
    summary = OfferMonitorService(session=session).sweep_once()
    return MonitorSweepResponse.model_validate(summary)
