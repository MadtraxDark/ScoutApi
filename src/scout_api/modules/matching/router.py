"""HTTP routes for product matching and offer refresh."""

from __future__ import annotations

import logging
import uuid as uuid_module
from collections.abc import Generator
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import get_db_session
from scout_api.core.db_errors import classify_database_error
from scout_api.modules.auth.deps import (
    DEV_BYPASS_USER_ID,
    enforce_rate_limit,
    require_authenticated_user,
    require_media_permission,
    require_permission,
)
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.schemas import CrawlErrorResponse
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.images.drive_client import (
    DriveClientError,
    DriveNotConfiguredError,
    get_drive_storage,
)
from scout_api.modules.matching.match_run_serializers import (
    match_run_to_detail,
    match_run_to_status,
    notification_to_view,
)
from scout_api.modules.matching.match_run_service import (
    MatchRunService,
    NotificationService,
)
from scout_api.modules.matching.models import StoreMetadata
from scout_api.modules.matching.offer_refresh_service import OfferRefreshService
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.schemas import (
    MatchRequest,
    MatchResponse,
    MatchRunDetailView,
    MatchRunListResponse,
    MatchRunStatusView,
    NotificationListResponse,
    NotificationView,
    OfferRefreshRequest,
    OfferRefreshResponse,
    ProductListResponse,
    ProductRegisterRequest,
    ProductRegisterResponse,
    ProductSearchResponse,
    ProductUpdateRequest,
    ProductView,
    StoreInfo,
    StoreListResponse,
    StoreMetadataUpdateRequest,
    StoreMetadataUpdateResponse,
    UnreadCountResponse,
)
from scout_api.modules.matching.search_adapters.registry import (
    registered_search_store_keys,
)
from scout_api.modules.matching.store_metadata_repository import StoreMetadataRepository
from scout_api.modules.matching.store_metadata_service import (
    InvalidStoreLogoError,
    StoreMetadataService,
    StoreNotFoundError,
    validate_store_logo_upload,
)

# Rate scopes are per-route (not router-wide) so:
# - crawler POSTs do not also burn the default bucket;
# - SPA polling uses the dedicated `poll` bucket (ADR 0036).
_RL_DEFAULT = Depends(enforce_rate_limit("default"))
_RL_POLL = Depends(enforce_rate_limit("poll"))
_RL_CRAWLER = Depends(enforce_rate_limit("crawler"))

router = APIRouter()
logger = logging.getLogger(__name__)


def require_store_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipal:
    if principal.role == UserRole.ADMIN:
        return principal
    if (
        not settings.auth_required
        and settings.environment.lower() != "production"
        and principal.id == DEV_BYPASS_USER_ID
    ):
        return principal
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "FORBIDDEN",
            "message": "Acesso administrativo necessário",
            "retryable": False,
        },
    )


def _optional_db_session() -> Generator[Session | None, None, None]:
    settings = get_settings()
    if not settings.database_url:
        yield None
        return
    yield from get_db_session()


def get_match_service(
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> ProductMatchService:
    return ProductMatchService(session=session)


def get_refresh_service(
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> OfferRefreshService:
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    return OfferRefreshService(session=session)


def get_registration_service(
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> ProductRegistrationService:
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    return ProductRegistrationService(session=session)


def get_match_run_service(
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> MatchRunService:
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    return MatchRunService(session)


def get_notification_service(
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> NotificationService:
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    return NotificationService(session)


@router.post(
    "/products",
    response_model=ProductRegisterResponse,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de escrita."},
        409: {
            "model": CrawlErrorResponse,
            "description": "Conflito de integridade ao persistir o produto.",
        },
        422: {
            "model": CrawlErrorResponse,
            "description": "Dados de cadastro inválidos.",
        },
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:write")), _RL_DEFAULT],
    summary="Cadastrar produto",
    description=(
        "Cadastra um produto canônico ou reutiliza um já existente "
        "sem sobrescrita silenciosa. "
        "Pode anexar um listing de loja quando store "
        "e identificadores forem informados."
    ),
)
def register_product(
    payload: ProductRegisterRequest,
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> ProductRegisterResponse:
    try:
        return service.register_saved(payload, owner=principal)
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.get(
    "/products",
    response_model=ProductListResponse,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de leitura."},
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:read")), _RL_DEFAULT],
    summary="Listar produtos",
    description=(
        "Lista produtos canônicos visíveis ao usuário autenticado, "
        "com paginação por limit/offset e ordenação por updated_at."
    ),
)
def list_products(
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:read"))
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListResponse:
    try:
        return service.list_products(viewer=principal, limit=limit, offset=offset)
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.get(
    "/stores",
    response_model=StoreListResponse,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:read")), _RL_DEFAULT],
    summary="Listar lojas do crawler",
    description=(
        "Retorna o registry estático de lojas do crawler "
        "(fonte de verdade cadastral; não é cadastro dinâmico no banco)."
    ),
)
def list_stores(
    _principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:read"))
    ],
    session: Annotated[Session | None, Depends(_optional_db_session)],
) -> StoreListResponse:
    return _stores_response(session)


def _store_logo_url(key: str, metadata: StoreMetadata | None) -> str | None:
    if metadata is None or not (metadata.logo_mime_type or metadata.logo_svg):
        return None
    revision = metadata.logo_version or str(
        int(metadata.updated_at.timestamp() * 1_000_000)
    )
    variant = "avif" if metadata.logo_optimized_file_id else "original"
    return f"/stores/{key}/logo?v={revision}-{variant}"


def _stores_response(session: Session | None) -> StoreListResponse:
    metadata = (
        StoreMetadataService(StoreMetadataRepository(session)).list_metadata()
        if session
        else {}
    )
    search_keys = set(registered_search_store_keys())
    stores = [
        StoreInfo(
            key=key,
            display_name=(
                metadata[key].display_name
                if key in metadata and metadata[key].display_name
                else config.label
            ),
            country=config.country,
            currency=config.currency,
            supported_country_currency_pairs=list(config.market_pairs),
            domains=list(config.domains),
            implemented=config.implemented,
            supports_search=key in search_keys,
            supports_images=config.supports_images,
            match_enabled=config.match_enabled,
            match_disabled_reason=config.match_disabled_reason,
            image_fetch_cost=config.image_fetch_cost,
            default_include_images=config.default_include_images,
            logo_svg=metadata[key].logo_svg if key in metadata else None,
            logo_url=_store_logo_url(key, metadata.get(key)),
            logo_mime_type=metadata[key].logo_mime_type if key in metadata else None,
            logo_processing_status=metadata[key].logo_processing_status
            if key in metadata
            else "ready",
        )
        for key, config in STORE_CONFIGS.items()
    ]
    return StoreListResponse(stores=stores)


@router.patch(
    "/admin/stores/{store_key}",
    response_model=StoreMetadataUpdateResponse,
    dependencies=[Depends(require_store_admin), _RL_DEFAULT],
    summary="Atualizar metadados de uma loja registrada",
)
def update_store_metadata(
    store_key: str,
    payload: StoreMetadataUpdateRequest,
    session: Annotated[Session | None, Depends(_optional_db_session)],
    _principal: Annotated[AuthenticatedPrincipal, Depends(require_store_admin)],
) -> StoreMetadataUpdateResponse:
    if session is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Banco de dados indisponível",
                "retryable": False,
            },
        )
    service = StoreMetadataService(StoreMetadataRepository(session))
    try:
        service.update(store_key, payload.display_name, payload.logo_svg)
    except StoreNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STORE_NOT_FOUND",
                "message": "Loja não encontrada",
                "retryable": False,
            },
        ) from exc
    except (InvalidStoreLogoError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STORE_METADATA",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    stores = _stores_response(session).stores
    return StoreMetadataUpdateResponse(
        store=next(store for store in stores if store.key == store_key)
    )


@router.post(
    "/admin/stores/{store_key}/logo",
    response_model=StoreMetadataUpdateResponse,
    dependencies=[Depends(require_store_admin), _RL_DEFAULT],
    summary="Enviar logo para uma loja registrada",
)
async def upload_store_logo(
    store_key: str,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session | None, Depends(_optional_db_session)],
    _principal: Annotated[AuthenticatedPrincipal, Depends(require_store_admin)],
) -> StoreMetadataUpdateResponse:
    if session is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Banco de dados indisponível",
                "retryable": False,
            },
        )
    if store_key not in STORE_CONFIGS:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STORE_NOT_FOUND",
                "message": "Loja não encontrada",
                "retryable": False,
            },
        )
    data = await file.read(2 * 1024 * 1024 + 1)
    try:
        upload = validate_store_logo_upload(
            data, file.filename or "", file.content_type or ""
        )
        version = str(uuid_module.uuid4())
        file_id = ""
        if not upload.is_svg:
            settings = get_settings()
            drive = get_drive_storage(settings)
            folder = drive.ensure_folder("store-logos", parent_id=drive.root_folder_id)
            file_id = drive.upload_bytes(
                name=f"{store_key}-{version}",
                parent_id=folder,
                data=data,
                mime_type=upload.mime_type,
            )
        service = StoreMetadataService(StoreMetadataRepository(session))
        current = service.repository.get(store_key)
        obsolete_ids = (
            [current.logo_original_file_id, current.logo_optimized_file_id]
            if current is not None
            else []
        )
        if current is None:
            service.update(store_key, STORE_CONFIGS[store_key].label, None)
        service.repository.save_logo_upload(
            store_key,
            mime_type=upload.mime_type,
            original_file_id=file_id,
            version=version,
            status="pending"
            if upload.mime_type not in {"image/svg+xml", "image/avif"}
            else "ready",
            svg_text=data.decode("utf-8") if upload.is_svg else None,
        )
        if obsolete_ids:
            cleanup = get_drive_storage(get_settings())
            for obsolete_id in obsolete_ids:
                if obsolete_id and obsolete_id != file_id:
                    try:
                        cleanup.delete_file(obsolete_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("store_logo_obsolete_file_cleanup_failed")
    except InvalidStoreLogoError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STORE_LOGO",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    return StoreMetadataUpdateResponse(
        store=next(
            store
            for store in _stores_response(session).stores
            if store.key == store_key
        )
    )


@router.get(
    "/stores/{store_key}/logo",
    include_in_schema=False,
    dependencies=[_RL_DEFAULT],
)
def get_store_logo(
    store_key: str,
    session: Annotated[Session | None, Depends(_optional_db_session)],
    _principal: Annotated[
        AuthenticatedPrincipal, Depends(require_media_permission("products:read"))
    ],
) -> Response:
    row = session.get(StoreMetadata, store_key) if session is not None else None
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "IMAGE_NOT_CONFIGURED",
                "message": "Esta loja não possui uma logo cadastrada.",
                "image_status": "missing",
                "retryable": False,
            },
        )
    if row.logo_svg:
        data = row.logo_svg.encode("utf-8")
        content_type = "image/svg+xml"
        version = row.logo_version or str(row.updated_at.timestamp())
    else:
        file_id = row.logo_optimized_file_id or row.logo_original_file_id
        if not file_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "IMAGE_NOT_CONFIGURED",
                    "message": "Esta loja não possui uma logo cadastrada.",
                    "image_status": "missing",
                    "retryable": False,
                },
            )
        try:
            data = get_drive_storage(get_settings()).download_bytes(file_id)
        except (DriveClientError, DriveNotConfiguredError) as exc:
            if isinstance(exc, DriveClientError):
                image_status = exc.availability
                retryable = exc.retryable
            else:
                image_status = "storage_error"
                retryable = False
            status_code, code, message = {
                "not_found": (
                    status.HTTP_404_NOT_FOUND,
                    "IMAGE_STORAGE_NOT_FOUND",
                    "A logo cadastrada não foi encontrada no armazenamento.",
                ),
                "permission_denied": (
                    status.HTTP_403_FORBIDDEN,
                    "IMAGE_STORAGE_PERMISSION_DENIED",
                    "O sistema não tem acesso à logo cadastrada.",
                ),
                "temporarily_unavailable": (
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "IMAGE_STORAGE_UNAVAILABLE",
                    "O armazenamento de imagens está temporariamente indisponível.",
                ),
            }.get(
                image_status,
                (
                    status.HTTP_502_BAD_GATEWAY,
                    "IMAGE_STORAGE_ERROR",
                    "Não foi possível acessar a logo cadastrada.",
                ),
            )
            logger.warning(
                "store_logo_delivery_failed",
                extra={
                    "store_key": store_key,
                    "entity_type": "store",
                    "entity_id": store_key,
                    "storage_provider": "drive",
                    "error_code": code,
                    "image_status": image_status,
                    "retryable": retryable,
                },
            )
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": code,
                    "message": message,
                    "image_status": image_status,
                    "retryable": retryable,
                },
            ) from exc
        content_type = (
            "image/avif"
            if row.logo_optimized_file_id
            else (row.logo_mime_type or "application/octet-stream")
        )
        version = row.logo_version or str(row.updated_at.timestamp())
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "ETag": f'"{version}-{content_type.split("/")[-1]}"',
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/products/search",
    response_model=ProductSearchResponse,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de leitura."},
        422: {
            "model": CrawlErrorResponse,
            "description": "Informe ao menos brand, model ou variant.",
        },
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:read")), _RL_DEFAULT],
    summary="Buscar produtos",
    description=(
        "Filtra o catálogo canônico por brand, model, variant e atributos "
        "opcionais (category-aware). Model identifica a família base; "
        "variant só restringe quando informado."
    ),
)
def search_products(
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:read"))
    ],
    brand: Annotated[
        str | None, Query(description="Marca (opcional, case-insensitive).")
    ] = None,
    model: Annotated[
        str | None,
        Query(
            description=(
                "Modelo base pesquisável (ex.: GeForce RTX 5070). Não exige variant."
            )
        ),
    ] = None,
    variant: Annotated[
        str | None,
        Query(
            description=(
                "Refinamento comercial opcional (ex.: Dual OC Edition). "
                "Quando omitido, retorna todas as implementações do modelo."
            )
        ),
    ] = None,
    category: Annotated[
        str | None,
        Query(description="Categoria do perfil (gpu, ram, smartphone, …)."),
    ] = None,
    vram: Annotated[str | None, Query(description="Filtro de VRAM (GPU).")] = None,
    memory_type: Annotated[str | None, Query(description="DDR5 / GDDR7 / …")] = None,
    capacity: Annotated[
        str | None, Query(description="Capacidade (RAM/SSD/storage).")
    ] = None,
    frequency: Annotated[str | None, Query(description="Frequência (RAM).")] = None,
    chipset: Annotated[str | None, Query(description="Chipset (placa-mãe).")] = None,
    socket: Annotated[str | None, Query(description="Socket (CPU/MB).")] = None,
    storage: Annotated[str | None, Query(description="Armazenamento.")] = None,
    refresh_rate: Annotated[
        str | None, Query(description="Taxa de atualização (monitor/TV).")
    ] = None,
    wattage: Annotated[str | None, Query(description="Potência (PSU/charger).")] = None,
    panel: Annotated[str | None, Query(description="Painel (OLED/IPS/…).")] = None,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Máximo de produtos retornados.")
    ] = 50,
) -> ProductSearchResponse:
    attribute_filters = {
        key: value
        for key, value in {
            "vram": vram,
            "memory_type": memory_type,
            "capacity": capacity,
            "frequency": frequency,
            "chipset": chipset,
            "socket": socket,
            "storage": storage,
            "refresh_rate": refresh_rate,
            "wattage": wattage,
            "panel": panel,
        }.items()
        if value
    }
    try:
        return service.search_products(
            viewer=principal,
            brand=brand,
            model=model,
            variant=variant,
            category=category,
            attribute_filters=attribute_filters or None,
            limit=limit,
        )
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.get(
    "/products/{product_id}",
    response_model=ProductView,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de leitura."},
        404: {"model": CrawlErrorResponse, "description": "Produto não encontrado."},
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:read")), _RL_DEFAULT],
    summary="Consultar produto",
    description=(
        "Retorna o produto canônico e seus listings vinculados, "
        "respeitando a visibilidade do usuário autenticado."
    ),
)
def get_product(
    product_id: Annotated[UUID, Path(description="Identificador do produto canônico.")],
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:read"))
    ],
) -> ProductView:
    product = service.get_product(product_id, viewer=principal)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRODUCT_NOT_FOUND",
                "message": "Produto canônico não encontrado",
                "retryable": False,
            },
        )
    return product


@router.patch(
    "/products/{product_id}",
    response_model=ProductView,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de escrita."},
        404: {"model": CrawlErrorResponse, "description": "Produto não encontrado."},
        422: {"model": CrawlErrorResponse, "description": "Dados inválidos."},
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("products:write")), _RL_DEFAULT],
    summary="Atualizar produto",
    description=(
        "Atualiza campos editáveis do produto canônico "
        "(title, brand, model, variant, attributes)."
    ),
)
def update_product(
    product_id: Annotated[UUID, Path(description="Identificador do produto canônico.")],
    payload: ProductUpdateRequest,
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> ProductView:
    try:
        return service.update_product(product_id, payload, owner=principal)
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Produtos"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de escrita."},
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    dependencies=[Depends(require_permission("products:write")), _RL_DEFAULT],
    summary="Excluir produto",
    description=(
        "Remove o produto canônico e listings associados (cascade). "
        "Idempotente quando o produto já não existe."
    ),
    response_class=Response,
)
def delete_product(
    product_id: Annotated[UUID, Path(description="Identificador do produto canônico.")],
    service: Annotated[ProductRegistrationService, Depends(get_registration_service)],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("products:write"))
    ],
) -> Response:
    try:
        service.delete_product(product_id, owner=principal)
    except RequestError as exc:
        raise HTTPException(
            status_code=_status_for_request_error(exc),
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/match",
    response_model=MatchResponse,
    tags=["Correspondência"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de matching."},
        422: {
            "model": CrawlErrorResponse,
            "description": "URL inválida, loja sem suporte ou falha de parsing.",
        },
        429: {
            "model": CrawlErrorResponse,
            "description": "Limite de requisições excedido.",
        },
        502: {
            "model": CrawlErrorResponse,
            "description": "Bloqueio ou falha ao acessar loja de origem.",
        },
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco indisponível quando persist=true.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(require_permission("match")),
        _RL_CRAWLER,
    ],
    summary="Corresponder produto entre lojas",
    description=(
        "A partir da URL de referência, busca candidatos nas lojas informadas "
        "(ou no conjunto padrão) e classifica cada hit "
        "como auto_match, review ou reject. "
        "Com persist=true, grava o produto canônico e os listings no banco."
    ),
)
def match_product(
    payload: MatchRequest,
    service: Annotated[ProductMatchService, Depends(get_match_service)],
) -> MatchResponse:
    if payload.persist and get_settings().database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": (
                    "DATABASE_URL não configurada; "
                    "use persist=false ou configure o Postgres"
                ),
                "retryable": False,
            },
        )
    try:
        return service.match(payload)
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
                "url": str(payload.reference_url),
                "retryable": False,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.post(
    "/products/{product_id}/match-runs",
    response_model=MatchRunStatusView,
    tags=["Correspondência"],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de matching."},
        404: {"model": CrawlErrorResponse, "description": "Produto não encontrado."},
        422: {
            "model": CrawlErrorResponse,
            "description": "Produto sem URL de referência.",
        },
        429: {
            "model": CrawlErrorResponse,
            "description": "Limite de requisições excedido.",
        },
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco indisponível.",
        },
    },
    dependencies=[
        Depends(require_permission("match")),
        _RL_CRAWLER,
    ],
    summary="Iniciar busca em outras lojas",
    description=(
        "Cria uma Match Run persistente e responde imediatamente (202). "
        "O Product Match continua em background; consulte o status via polling. "
        "Se já existir Run ativa, devolve a existente com already_active=true."
    ),
)
def start_match_run(
    product_id: Annotated[UUID, Path(description="ID do produto canônico.")],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[MatchRunService, Depends(get_match_run_service)],
) -> MatchRunStatusView:
    try:
        run, created = service.start(product_id, principal=principal)
        return match_run_to_status(run, already_active=not created)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRODUCT_NOT_FOUND",
                "message": "Produto não encontrado.",
                "retryable": False,
            },
        ) from None
    except ValueError as exc:
        if str(exc) == "REFERENCE_URL_MISSING":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "REFERENCE_URL_MISSING",
                    "message": "O produto não possui URL de referência para busca.",
                    "retryable": False,
                },
            ) from exc
        raise
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


@router.get(
    "/products/{product_id}/match-runs/active",
    response_model=MatchRunStatusView,
    tags=["Correspondência"],
    responses={
        204: {"description": "Nenhuma busca ativa."},
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão."},
        404: {"model": CrawlErrorResponse, "description": "Produto não encontrado."},
    },
    dependencies=[Depends(require_permission("match")), _RL_POLL],
    summary="Consultar busca ativa do produto",
    description="Retorna a Match Run ativa (pending/running) ou 204 se não houver.",
)
def get_active_match_run(
    product_id: Annotated[UUID, Path(description="ID do produto canônico.")],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[MatchRunService, Depends(get_match_run_service)],
    response: Response,
) -> MatchRunStatusView | Response:
    try:
        run = service.get_active(product_id, principal=principal)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRODUCT_NOT_FOUND",
                "message": "Produto não encontrado.",
                "retryable": False,
            },
        ) from None
    if run is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    return match_run_to_status(run)


@router.get(
    "/products/{product_id}/match-runs",
    response_model=MatchRunListResponse,
    tags=["Correspondência"],
    dependencies=[Depends(require_permission("match")), _RL_DEFAULT],
    summary="Histórico de buscas do produto",
    description="Lista Match Runs do produto (mais recentes primeiro).",
)
def list_match_runs(
    product_id: Annotated[UUID, Path(description="ID do produto canônico.")],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[MatchRunService, Depends(get_match_run_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchRunListResponse:
    try:
        rows = service.list_history(
            product_id, principal=principal, limit=limit, offset=offset
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRODUCT_NOT_FOUND",
                "message": "Produto não encontrado.",
                "retryable": False,
            },
        ) from None
    return MatchRunListResponse(items=[match_run_to_status(row) for row in rows])


@router.get(
    "/match-runs/{run_id}",
    response_model=MatchRunStatusView,
    tags=["Correspondência"],
    dependencies=[Depends(require_permission("match")), _RL_POLL],
    summary="Status da Match Run",
    description="Payload compacto para polling (sem candidates/logs).",
)
def get_match_run_status(
    run_id: Annotated[UUID, Path(description="ID da Match Run.")],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[MatchRunService, Depends(get_match_run_service)],
) -> MatchRunStatusView:
    try:
        run = service.get_status(run_id, principal=principal)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RUN_NOT_FOUND",
                "message": "Match Run não encontrada.",
                "retryable": False,
            },
        ) from None
    return match_run_to_status(run)


@router.get(
    "/match-runs/{run_id}/details",
    response_model=MatchRunDetailView,
    tags=["Correspondência"],
    dependencies=[Depends(require_permission("match")), _RL_DEFAULT],
    summary="Relatório detalhado da Match Run",
    description="Summary + resultados por loja + candidates relevantes.",
)
def get_match_run_details(
    run_id: Annotated[UUID, Path(description="ID da Match Run.")],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[MatchRunService, Depends(get_match_run_service)],
) -> MatchRunDetailView:
    try:
        run = service.get_details(run_id, principal=principal)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RUN_NOT_FOUND",
                "message": "Match Run não encontrada.",
                "retryable": False,
            },
        ) from None
    return match_run_to_detail(run)


@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    tags=["Notificações"],
    dependencies=[Depends(require_authenticated_user), _RL_POLL],
    summary="Listar notificações",
    description="Central persistente de notificações do usuário autenticado.",
)
def list_notifications(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
    unread_only: Annotated[bool, Query()] = False,
) -> NotificationListResponse:
    items = service.list_for_user(
        principal.id, limit=limit, offset=offset, unread_only=unread_only
    )
    unread = service.unread_count(principal.id)
    return NotificationListResponse(
        items=[notification_to_view(row) for row in items],
        unread_count=unread,
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    tags=["Notificações"],
    dependencies=[Depends(require_authenticated_user), _RL_POLL],
    summary="Contagem de não lidas",
)
def notifications_unread_count(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> UnreadCountResponse:
    return UnreadCountResponse(unread_count=service.unread_count(principal.id))


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationView,
    tags=["Notificações"],
    dependencies=[Depends(require_authenticated_user), _RL_DEFAULT],
    summary="Marcar notificação como lida",
)
def mark_notification_read(
    notification_id: Annotated[UUID, Path()],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> NotificationView:
    try:
        row = service.mark_read(notification_id, user_id=principal.id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOTIFICATION_NOT_FOUND",
                "message": "Notificação não encontrada.",
                "retryable": False,
            },
        ) from None
    return notification_to_view(row)


@router.post(
    "/notifications/read-all",
    response_model=UnreadCountResponse,
    tags=["Notificações"],
    dependencies=[Depends(require_authenticated_user), _RL_DEFAULT],
    summary="Marcar todas as notificações como lidas",
)
def mark_all_notifications_read(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> UnreadCountResponse:
    service.mark_all_read(principal.id)
    return UnreadCountResponse(unread_count=0)


@router.post(
    "/offers/refresh",
    response_model=OfferRefreshResponse,
    tags=["Ofertas"],
    responses={
        401: {"model": CrawlErrorResponse, "description": "Não autenticado."},
        403: {"model": CrawlErrorResponse, "description": "Sem permissão de refresh."},
        422: {
            "model": CrawlErrorResponse,
            "description": "Pedido inválido ou falha ao interpretar a página.",
        },
        429: {
            "model": CrawlErrorResponse,
            "description": "Limite de requisições excedido.",
        },
        502: {
            "model": CrawlErrorResponse,
            "description": "Bloqueio ou falha ao acessar loja de origem.",
        },
        503: {
            "model": CrawlErrorResponse,
            "description": "Banco de dados indisponível ou não configurado.",
        },
    },
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(require_permission("offers:refresh")),
        _RL_CRAWLER,
    ],
    summary="Atualizar ofertas",
    description=(
        "Reexecuta o scraping das ofertas indicadas por produto canônico, "
        "listing_ids ou urls e registra mudanças de preço, vendedor ou disponibilidade."
    ),
)
def refresh_offers(
    payload: OfferRefreshRequest,
    service: Annotated[OfferRefreshService, Depends(get_refresh_service)],
    registration: Annotated[
        ProductRegistrationService, Depends(get_registration_service)
    ],
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("offers:refresh"))
    ],
) -> OfferRefreshResponse:
    if get_settings().database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "DATABASE_URL não configurada",
                "retryable": False,
            },
        )
    if (
        payload.canonical_product_id is None
        and not payload.listing_ids
        and not payload.urls
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Informe canonical_product_id, listing_ids ou urls",
                "retryable": False,
            },
        )
    if payload.canonical_product_id is not None:
        owned = registration.get_product(payload.canonical_product_id, viewer=principal)
        if owned is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "PRODUCT_NOT_FOUND",
                    "message": "Produto canônico não encontrado",
                    "retryable": False,
                },
            )
    try:
        return service.refresh(payload)
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
                "retryable": False,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise _http_for_database_error(exc) from exc


def _http_for_database_error(exc: SQLAlchemyError) -> HTTPException:
    failure = classify_database_error(exc)
    http_status = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if failure.code == "DATABASE_UNAVAILABLE"
        else status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    if failure.code == "DATABASE_INTEGRITY_ERROR":
        http_status = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=http_status,
        detail={
            "code": failure.code,
            "message": failure.message,
            "retryable": failure.retryable,
        },
    )


def _status_for_request_error(exc: RequestError) -> int:
    if exc.code in {"DUPLICATE_REQUEST", "RATE_LIMITED"}:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if exc.code in {"DATABASE_UNAVAILABLE"}:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if exc.code in {"INVALID_REQUEST", "UNSUPPORTED_STORE", "SEARCH_UNSUPPORTED"}:
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if exc.code == "PRODUCT_NOT_FOUND":
        return status.HTTP_404_NOT_FOUND
    if exc.code == "FORBIDDEN":
        return status.HTTP_403_FORBIDDEN
    if exc.code == "AUTH_REQUIRED":
        return status.HTTP_401_UNAUTHORIZED
    return status.HTTP_502_BAD_GATEWAY
