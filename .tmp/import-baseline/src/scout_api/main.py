import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from scout_api.api.router import api_router
from scout_api.core.config import get_settings
from scout_api.core.database import dispose_database_engine
from scout_api.core.http_errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from scout_api.core.log_redaction import install_log_redaction

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.scraper_log_level.upper(), logging.INFO),
    format="%(levelname)s:%(name)s:%(message)s",
)
install_log_redaction()

_is_production = settings.environment.lower() == "production"
_docs_url: str | None = None if _is_production else "/docs"
_redoc_url: str | None = None if _is_production else "/redoc"
_openapi_url: str | None = None if _is_production else "/openapi.json"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Engine stays lazy: do not connect to PostgreSQL on startup.
    # Image AVIF poller connects only on first sweep (ADR 0031).
    from scout_api.modules.exchange.worker import (
        start_scheduler as start_exchange_scheduler,
    )
    from scout_api.modules.exchange.worker import (
        stop_scheduler as stop_exchange_scheduler,
    )
    from scout_api.modules.images.worker import start_scheduler, stop_scheduler
    from scout_api.modules.matching.match_run_worker import (
        start_scheduler as start_match_run_scheduler,
    )
    from scout_api.modules.matching.match_run_worker import (
        stop_scheduler as stop_match_run_scheduler,
    )

    start_scheduler()
    start_exchange_scheduler()
    start_match_run_scheduler()
    try:
        yield
    finally:
        stop_match_run_scheduler()
        stop_exchange_scheduler()
        stop_scheduler()
        dispose_database_engine()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug and not _is_production,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Saúde da API",
            "description": "Verificação de disponibilidade da API.",
        },
        {
            "name": "Autenticação",
            "description": "Login Google, sessão e usuário autenticado.",
        },
        {
            "name": "Crawler",
            "description": "Scraping de produto e oferta a partir de URL.",
        },
        {
            "name": "Produtos",
            "description": "Cadastro, listagem e consulta de produtos canônicos.",
        },
        {
            "name": "Imagens",
            "description": "Galeria de produto (Drive + AVIF) após aprovação.",
        },
        {
            "name": "Correspondência",
            "description": "Matching de produto entre lojas suportadas.",
        },
        {
            "name": "Notificações",
            "description": "Central persistente de notificações do usuário.",
        },
        {
            "name": "Ofertas",
            "description": "Atualização de preço e disponibilidade de ofertas.",
        },
        {
            "name": "Câmbio",
            "description": (
                "Cotações FX e valor convertido em BRL "
                "(preço × taxa; sem IOF/impostos/frete)."
            ),
        },
    ],
)

origins = [
    part.strip()
    for part in (settings.cors_allowed_origins or "").split(",")
    if part.strip() and part.strip() != "*"
]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        expose_headers=[
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Scope",
        ],
    )

ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]
app.add_exception_handler(
    StarletteHTTPException,
    cast(ExceptionHandler, http_exception_handler),
)
app.add_exception_handler(
    RequestValidationError,
    cast(ExceptionHandler, validation_exception_handler),
)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(api_router)
