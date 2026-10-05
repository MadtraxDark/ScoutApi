"""FastAPI dependencies: authentication, authorization, rate limiting."""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from scout_api.core.config import Settings, get_settings
from scout_api.core.rate_limit import RateLimitResult, get_rate_limiter
from scout_api.modules.auth.jwt_service import AuthError, verify_access_token
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole

_bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)

REFRESH_COOKIE = "scout_refresh_token"
PKCE_COOKIE = "scout_pkce_verifier"
# HttpOnly access JWT for browser subresources (<img>) that cannot send Bearer.
# Accepted ONLY by media content routes — never by mutating JSON APIs.
ACCESS_COOKIE = "scout_access_token"
DEFAULT_ACCESS_COOKIE_MAX_AGE = 60 * 60

# Fixed local-dev principal when AUTH_REQUIRED=false (never production).
# USER — not ADMIN — so ownership stays scoped to this UUID only.
DEV_BYPASS_USER_ID = UUID("00000000-0000-4000-8000-000000000001")


def client_ip(request: Request, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    peer = request.client.host if request.client else "unknown"
    trusted = {
        part.strip()
        for part in (cfg.trusted_proxy_ips or "").split(",")
        if part.strip()
    }
    if peer in trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or peer
    return peer


def _auth_http_error(exc: AuthError) -> HTTPException:
    code = status.HTTP_401_UNAUTHORIZED
    if exc.code == "AUTH_MISCONFIGURED":
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _development_bypass_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        id=DEV_BYPASS_USER_ID,
        role=UserRole.USER,
        display_name="dev-bypass",
    )


def _resolve_principal_from_bearer(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
    *,
    allow_dev_bypass: bool,
) -> AuthenticatedPrincipal | None:
    if credentials is not None and credentials.credentials:
        try:
            return verify_access_token(credentials.credentials, settings=settings)
        except AuthError as exc:
            raise _auth_http_error(exc) from exc
    if allow_dev_bypass and not settings.auth_required:
        if settings.environment.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTH_MISCONFIGURED",
                    "message": "AUTH_REQUIRED=false não permitido em production",
                    "retryable": False,
                },
            )
        return _development_bypass_principal()
    return None


def require_authenticated_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipal:
    """Resolve the caller principal for protected routes.

    When ``AUTH_REQUIRED=true`` (default): Bearer JWT is mandatory.
    When ``AUTH_REQUIRED=false`` (non-production only): missing Bearer uses the
    fixed local-dev principal; a present Bearer is still validated.

    Does **not** accept the media access cookie — JSON APIs stay Bearer-only
    to avoid CSRF on mutating methods.
    """
    principal = getattr(request.state, "bearer_principal", None)
    if principal is None:
        principal = _resolve_principal_from_bearer(
            credentials, settings, allow_dev_bypass=True
        )
    if principal is not None:
        request.state.bearer_principal = principal
        return principal
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "Credencial Bearer obrigatória",
            "retryable": False,
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_media_authenticated_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipal:
    """Auth for image bytes: Bearer, then HttpOnly access cookie, then dev bypass.

    ``<img src>`` cannot send ``Authorization``. The access cookie is set on
    login/refresh (path ``/``) so same-site media requests authenticate without
    putting tokens in the URL.
    """
    principal = _resolve_principal_from_bearer(
        credentials, settings, allow_dev_bypass=False
    )
    if principal is not None:
        return principal

    cookie_token = request.cookies.get(ACCESS_COOKIE)
    if cookie_token:
        try:
            return verify_access_token(cookie_token, settings=settings)
        except AuthError as exc:
            raise _auth_http_error(exc) from exc

    if not settings.auth_required:
        if settings.environment.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTH_MISCONFIGURED",
                    "message": "AUTH_REQUIRED=false não permitido em production",
                    "retryable": False,
                },
            )
        return _development_bypass_principal()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "Credencial Bearer ou cookie de mídia obrigatória",
            "retryable": False,
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(
    permission: str,
) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    def _dep(
        principal: Annotated[
            AuthenticatedPrincipal, Depends(require_authenticated_user)
        ],
    ) -> AuthenticatedPrincipal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Permissão insuficiente",
                    "retryable": False,
                },
            )
        return principal

    return _dep


def require_media_permission(
    permission: str,
) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    """Like ``require_permission``, but resolves identity via media auth rules."""

    def _dep(
        principal: Annotated[
            AuthenticatedPrincipal, Depends(require_media_authenticated_user)
        ],
    ) -> AuthenticatedPrincipal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": "Permissão insuficiente",
                    "retryable": False,
                },
            )
        return principal

    return _dep


def require_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
) -> AuthenticatedPrincipal:
    if principal.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": "Acesso administrativo necessário",
                "retryable": False,
            },
        )
    return principal


def enforce_rate_limit(
    scope: str,
    *,
    use_user: bool = True,
) -> Callable[..., RateLimitResult]:
    def _dep(
        request: Request,
        response: Response,
        settings: Annotated[Settings, Depends(get_settings)],
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer)
        ] = None,
    ) -> RateLimitResult:
        limits = {
            "default": settings.rate_limit_default_per_minute,
            "auth": settings.rate_limit_auth_per_minute,
            "crawler": settings.rate_limit_crawler_per_minute,
            "poll": settings.rate_limit_poll_per_minute,
        }
        limit = limits.get(scope, settings.rate_limit_default_per_minute)
        identity = client_ip(request, settings)
        if use_user and credentials and credentials.credentials:
            principal = getattr(request.state, "bearer_principal", None)
            if principal is None:
                principal = _resolve_principal_from_bearer(
                    credentials, settings, allow_dev_bypass=False
                )
                request.state.bearer_principal = principal
            if principal is not None:
                identity = (
                    "u:"
                    + hashlib.sha256(str(principal.id).encode("utf-8")).hexdigest()[:32]
                )
        result = get_rate_limiter().check(
            scope=scope, identity=identity, limit=limit, window_seconds=60
        )
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Scope"] = scope
        if not result.allowed:
            logger.info(
                "api_rate_limited",
                extra={
                    "scope": scope,
                    "method": request.method,
                    "route": getattr(request.scope.get("route"), "path", "unknown"),
                    "retry_after": result.retry_after,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "RATE_LIMITED",
                    "message": "Limite de requisições excedido",
                    "retryable": True,
                    "retry_after": result.retry_after,
                    "rate_limit_scope": scope,
                    "rate_limit_policy": "token_bucket",
                },
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Scope": scope,
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        return result

    return _dep


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64

    code_challenge = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    return verifier, code_challenge


def set_refresh_cookie(response: Response, refresh_token: str, *, secure: bool) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/auth",
        max_age=60 * 60 * 24 * 30,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path="/auth")


def set_access_cookie(
    response: Response,
    access_token: str,
    *,
    secure: bool,
    max_age: int | None = None,
) -> None:
    """HttpOnly access JWT for same-site media GETs (not for JSON API CSRF)."""
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=max_age if max_age is not None else DEFAULT_ACCESS_COOKIE_MAX_AGE,
    )


def clear_access_cookie(response: Response) -> None:
    response.delete_cookie(key=ACCESS_COOKIE, path="/")


def origin_allowed(url: str, allowed: str) -> bool:
    if not allowed.strip():
        return False
    host = urlsplit(url).netloc.lower()
    for part in allowed.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        if candidate == "*":
            return False  # never allow wildcard with credentialed flows
        if urlsplit(candidate).netloc.lower() == host or candidate.rstrip(
            "/"
        ) == url.rstrip("/"):
            return True
    return False
