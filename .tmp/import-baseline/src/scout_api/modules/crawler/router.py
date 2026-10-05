from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from scout_api.modules.auth.deps import enforce_rate_limit, require_permission

from .core.exceptions import ParseError, RequestError
from .models.product import ProductOffer, ProductPriceItem
from .schemas import CrawlErrorResponse, CrawlRequest
from .services.offer_scrape_service import OfferScrapeService
from .services.product_scrape_service import ProductScrapeService

router = APIRouter(
    prefix="/crawl",
    tags=["Crawler"],
    dependencies=[
        Depends(require_permission("crawl")),
        Depends(enforce_rate_limit("crawler")),
    ],
)


def get_product_scrape_service() -> ProductScrapeService:
    return ProductScrapeService()


def get_offer_scrape_service() -> OfferScrapeService:
    return OfferScrapeService()


@router.post(
    "",
    response_model=ProductPriceItem,
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de crawl."},
        422: {
            "model": CrawlErrorResponse,
            "description": "URL inválida ou falha ao interpretar a página.",
        },
        429: {
            "model": CrawlErrorResponse,
            "description": "Limite de requisições excedido.",
        },
        502: {
            "model": CrawlErrorResponse,
            "description": "Bloqueio ou falha ao acessar a loja de origem.",
        },
    },
    status_code=status.HTTP_200_OK,
    summary="Executar scraping completo",
    description=(
        "Coleta título, atributos, preço e demais dados do produto na URL informada. "
        "Com include_images=true, também extrai a galeria de imagens quando disponível."
    ),
)
def crawl_product(
    payload: CrawlRequest,
    service: Annotated[ProductScrapeService, Depends(get_product_scrape_service)],
) -> ProductPriceItem:
    try:
        return service.scrape(str(payload.url), include_images=payload.include_images)
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "url": exc.url,
                "upstream_status": exc.upstream_status,
                "retryable": exc.retryable,
                "retry_after": exc.retry_after,
            },
        ) from exc
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PARSE_ERROR",
                "message": str(exc),
                "url": str(payload.url),
                "retryable": False,
            },
        ) from exc


@router.post(
    "/offer",
    response_model=ProductOffer,
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de crawl."},
        422: {
            "model": CrawlErrorResponse,
            "description": "URL inválida ou falha ao interpretar a oferta.",
        },
        429: {
            "model": CrawlErrorResponse,
            "description": "Limite de requisições excedido.",
        },
        502: {
            "model": CrawlErrorResponse,
            "description": "Bloqueio ou falha ao acessar a loja de origem.",
        },
    },
    status_code=status.HTTP_200_OK,
    summary="Buscar oferta de produto",
    description=(
        "Obtém preço, disponibilidade, vendedor "
        "e demais dados comerciais da URL informada."
    ),
)
def crawl_offer(
    payload: CrawlRequest,
    service: Annotated[OfferScrapeService, Depends(get_offer_scrape_service)],
) -> ProductOffer:
    try:
        return service.scrape_offer(str(payload.url))
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "url": exc.url,
                "upstream_status": exc.upstream_status,
                "retryable": exc.retryable,
                "retry_after": exc.retry_after,
            },
        ) from exc
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PARSE_ERROR",
                "message": str(exc),
                "url": str(payload.url),
                "retryable": False,
            },
        ) from exc


def _status_for_request_error(exc: RequestError) -> int:
    if exc.code in {"DUPLICATE_REQUEST", "RATE_LIMITED"}:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if exc.code == "AUTH_REQUIRED":
        return status.HTTP_401_UNAUTHORIZED
    return status.HTTP_502_BAD_GATEWAY
