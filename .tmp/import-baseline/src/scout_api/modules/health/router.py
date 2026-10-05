from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from scout_api.core.database import check_database

router = APIRouter(tags=["Saúde da API"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable", "not_configured"]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Consultar saúde da API",
    description=(
        "Indica se a API está respondendo e se o banco está acessível. "
        "Sempre retorna HTTP 200; use o campo database para readiness de persistência."
    ),
)
def health_check() -> HealthResponse:
    database = check_database()
    status: Literal["ok", "degraded"] = (
        "degraded" if database == "unavailable" else "ok"
    )
    return HealthResponse(status=status, database=database)
