"""HTTP routes for product image gallery and media delivery."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from fastapi.responses import Response as PlainResponse
from sqlalchemy.orm import Session

from scout_api.core.config import get_settings
from scout_api.core.database import get_db_session
from scout_api.modules.auth.deps import (
    enforce_rate_limit,
    require_media_permission,
    require_permission,
)
from scout_api.modules.auth.schemas import AuthenticatedPrincipal
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.schemas import CrawlErrorResponse
from scout_api.modules.images.schemas import (
    AddProductImageRequest,
    GalleryPatchRequest,
    ProductImageListResponse,
    ProductImageView,
)
from scout_api.modules.images.service import ProductImageService

router = APIRouter(
    dependencies=[Depends(enforce_rate_limit("default"))],
)


def _require_db() -> Generator[Session, None, None]:
    settings = get_settings()
    if not settings.database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    yield from get_db_session()


def get_image_service(
    session: Annotated[Session, Depends(_require_db)],
) -> ProductImageService:
    return ProductImageService(session)


def _http_error(exc: RequestError) -> HTTPException:
    code_map = {
        "PRODUCT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "FORBIDDEN": status.HTTP_403_FORBIDDEN,
        "INVALID_REQUEST": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "IMAGE_PROCESSING": status.HTTP_425_TOO_EARLY,
        "IMAGE_INVALID_REFERENCE": status.HTTP_424_FAILED_DEPENDENCY,
        "IMAGE_STORAGE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "IMAGE_STORAGE_PERMISSION_DENIED": status.HTTP_403_FORBIDDEN,
        "IMAGE_STORAGE_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
        "IMAGE_STORAGE_ERROR": status.HTTP_502_BAD_GATEWAY,
        "STORAGE_ERROR": status.HTTP_502_BAD_GATEWAY,
        "UPSTREAM_ERROR": status.HTTP_502_BAD_GATEWAY,
    }
    image_status_map = {
        "IMAGE_PROCESSING": "processing",
        "IMAGE_INVALID_REFERENCE": "invalid_reference",
        "IMAGE_STORAGE_NOT_FOUND": "not_found",
        "IMAGE_STORAGE_PERMISSION_DENIED": "permission_denied",
        "IMAGE_STORAGE_UNAVAILABLE": "temporarily_unavailable",
        "IMAGE_STORAGE_ERROR": "storage_error",
    }
    return HTTPException(
        status_code=code_map.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={
            "code": exc.code,
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
            **(
                {"image_status": image_status_map[exc.code]}
                if exc.code in image_status_map
                else {}
            ),
        },
    )


@router.get(
    "/products/{product_id}/images",
    response_model=ProductImageListResponse,
    tags=["Imagens"],
    dependencies=[Depends(require_permission("products:read"))],
    summary="Listar galeria",
    description="Lista imagens do produto ordenadas por position.",
    responses={404: {"model": CrawlErrorResponse}},
)
def list_images(
    product_id: Annotated[UUID, Path()],
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:read"))
    ],
) -> ProductImageListResponse:
    try:
        return service.list_images(product_id, viewer=principal)
    except RequestError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/products/{product_id}/images",
    response_model=ProductImageView,
    tags=["Imagens"],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("products:write"))],
    summary="Adicionar imagem",
    description="Baixa URL externa aprovada e persiste original; AVIF em background.",
)
def add_image(
    product_id: Annotated[UUID, Path()],
    payload: AddProductImageRequest,
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> ProductImageView:
    try:
        return service.add_image(product_id, payload, owner=principal)
    except RequestError as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/products/{product_id}/images",
    response_model=ProductImageListResponse,
    tags=["Imagens"],
    dependencies=[Depends(require_permission("products:write"))],
    summary="Reordenar galeria",
    description="Atualiza position e is_main sem reupload.",
)
def patch_gallery(
    product_id: Annotated[UUID, Path()],
    payload: GalleryPatchRequest,
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> ProductImageListResponse:
    try:
        return service.patch_gallery(product_id, payload, owner=principal)
    except RequestError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/products/{product_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["Imagens"],
    dependencies=[Depends(require_permission("products:write"))],
    summary="Excluir imagem",
    description="Remove arquivos conhecidos no Drive e a metadata no Postgres.",
)
def delete_image(
    product_id: Annotated[UUID, Path()],
    image_id: Annotated[UUID, Path()],
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> Response:
    try:
        service.delete_image(product_id, image_id, owner=principal)
    except RequestError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/products/{product_id}/images/{image_id}/retry-optimization",
    response_model=ProductImageView,
    tags=["Imagens"],
    dependencies=[Depends(require_permission("products:write"))],
    summary="Retentar AVIF",
    description="Reenfileira conversão AVIF para imagem com original ready.",
)
def retry_optimization(
    product_id: Annotated[UUID, Path()],
    image_id: Annotated[UUID, Path()],
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> ProductImageView:
    try:
        return service.retry_optimization(product_id, image_id, owner=principal)
    except RequestError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/products/{product_id}/images/{image_id}/content",
    tags=["Imagens"],
    dependencies=[Depends(require_media_permission("products:read"))],
    summary="Conteúdo da imagem",
    description=(
        "Proxy autenticado (Bearer ou cookie HttpOnly de mídia). "
        "Use variant=original|optimized (URLs imutáveis); "
        "sem variant, auto prefere AVIF quando ready."
    ),
    responses={
        200: {"content": {"image/*": {}}},
        304: {"description": "Not Modified"},
        404: {"model": CrawlErrorResponse},
    },
)
def get_image_content(
    product_id: Annotated[UUID, Path()],
    image_id: Annotated[UUID, Path()],
    service: Annotated[ProductImageService, Depends(get_image_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_media_permission("products:read"))
    ],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    variant: Annotated[
        Literal["auto", "original", "optimized"],
        Query(description="original|optimized|auto (padrão)"),
    ] = "auto",
) -> PlainResponse:
    try:
        data, content_type, etag = service.get_content(
            product_id, image_id, viewer=principal, variant=variant
        )
    except RequestError as exc:
        raise _http_error(exc) from exc
    if if_none_match and if_none_match.strip() == etag:
        return PlainResponse(status_code=status.HTTP_304_NOT_MODIFIED)
    settings = get_settings()
    return PlainResponse(
        content=data,
        media_type=content_type,
        headers={
            "ETag": etag,
            "Content-Disposition": "inline",
            "Cache-Control": (
                f"private, max-age={settings.image_media_cache_max_age_seconds}"
            ),
        },
    )
