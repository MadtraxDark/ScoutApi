"""Auth HTTP routes: Google OAuth via Supabase + session helpers."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from scout_api.core.config import Settings, get_settings
from scout_api.modules.auth.deps import (
    PKCE_COOKIE,
    REFRESH_COOKIE,
    clear_access_cookie,
    clear_refresh_cookie,
    create_pkce_pair,
    enforce_rate_limit,
    origin_allowed,
    require_authenticated_user,
    set_access_cookie,
    set_refresh_cookie,
)
from scout_api.modules.auth.jwt_service import (
    AuthError,
    build_google_authorize_url,
    exchange_auth_code,
    refresh_session,
    verify_access_token,
)
from scout_api.modules.auth.schemas import (
    AuthenticatedPrincipal,
    AuthSessionResponse,
    GoogleAuthStartResponse,
    PublicUser,
    principal_to_public,
)

router = APIRouter(prefix="/auth", tags=["Autenticação"])


def _http_auth_error(exc: AuthError) -> HTTPException:
    code = status.HTTP_401_UNAUTHORIZED
    if exc.code == "AUTH_MISCONFIGURED":
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.code == "AUTH_PROVIDER_ERROR":
        code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


@router.get(
    "/google",
    response_model=GoogleAuthStartResponse,
    dependencies=[Depends(enforce_rate_limit("auth", use_user=False))],
    summary="Iniciar login com Google",
    description=(
        "Gera a URL de autorização Google (OAuth) "
        "para o cliente redirecionar o usuário."
    ),
)
def start_google_auth(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> GoogleAuthStartResponse:
    redirect_to = settings.auth_google_redirect_url
    if not redirect_to:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_MISCONFIGURED",
                "message": "AUTH_GOOGLE_REDIRECT_URL não configurada",
                "retryable": False,
            },
        )
    verifier, challenge = create_pkce_pair()
    secure = settings.environment.lower() == "production"
    response.set_cookie(
        key=PKCE_COOKIE,
        value=verifier,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/auth",
        max_age=600,
    )
    try:
        url = build_google_authorize_url(
            redirect_to=redirect_to, code_challenge=challenge, settings=settings
        )
    except AuthError as exc:
        raise _http_auth_error(exc) from exc
    return GoogleAuthStartResponse(authorization_url=url)


@router.get(
    "/callback",
    response_model=None,
    dependencies=[Depends(enforce_rate_limit("auth", use_user=False))],
    summary="Concluir login com Google",
    description=(
        "Troca o código OAuth por sessão autenticada. "
        "Pode redirecionar ao frontend configurado "
        "ou devolver o access token na resposta."
    ),
)
def google_auth_callback(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    code: Annotated[
        str | None,
        Query(description="Código de autorização retornado pelo provedor OAuth."),
    ] = None,
    error: Annotated[
        str | None,
        Query(description="Código de erro quando o provedor nega a autenticação."),
    ] = None,
) -> RedirectResponse | AuthSessionResponse:
    if error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_DENIED",
                "message": "Autenticação Google negada",
                "retryable": False,
            },
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_REQUEST",
                "message": "code ausente",
                "retryable": False,
            },
        )
    verifier = request.cookies.get(PKCE_COOKIE)
    if not verifier:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_TOKEN",
                "message": "PKCE verifier ausente",
                "retryable": False,
            },
        )
    try:
        tokens = exchange_auth_code(
            code=code, code_verifier=verifier, settings=settings
        )
        principal = verify_access_token(str(tokens["access_token"]), settings=settings)
    except AuthError as exc:
        raise _http_auth_error(exc) from exc

    response.delete_cookie(key=PKCE_COOKIE, path="/auth")
    refresh = tokens.get("refresh_token")
    secure = settings.environment.lower() == "production"
    access_token = str(tokens["access_token"])
    expires_in = int(tokens["expires_in"]) if tokens.get("expires_in") else None
    if isinstance(refresh, str) and refresh:
        set_refresh_cookie(response, refresh, secure=secure)
    set_access_cookie(
        response,
        access_token,
        secure=secure,
        max_age=expires_in,
    )

    session = AuthSessionResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=principal_to_public(principal),
    )
    success = settings.auth_frontend_success_url
    if success and origin_allowed(success, settings.cors_allowed_origins or success):
        # Pass access token via fragment — not query — to avoid referrer leakage.
        # Prefer SPA reading POST body; redirect is optional for browser flows.
        fragment = urlencode({"access_token": session.access_token})
        return RedirectResponse(url=f"{success}#{fragment}", status_code=302)
    return session


@router.post(
    "/refresh",
    response_model=AuthSessionResponse,
    dependencies=[Depends(enforce_rate_limit("auth", use_user=False))],
    summary="Renovar sessão",
    description=(
        "Emite um novo access token a partir do cookie de refresh HttpOnly da sessão."
    ),
)
def refresh_auth_session(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionResponse:
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Refresh token ausente",
                "retryable": False,
            },
        )
    try:
        tokens = refresh_session(refresh, settings=settings)
        principal = verify_access_token(str(tokens["access_token"]), settings=settings)
    except AuthError as exc:
        clear_refresh_cookie(response)
        raise _http_auth_error(exc) from exc
    new_refresh = tokens.get("refresh_token")
    secure = settings.environment.lower() == "production"
    access_token = str(tokens["access_token"])
    expires_in = int(tokens["expires_in"]) if tokens.get("expires_in") else None
    if isinstance(new_refresh, str) and new_refresh:
        set_refresh_cookie(response, new_refresh, secure=secure)
    set_access_cookie(
        response,
        access_token,
        secure=secure,
        max_age=expires_in,
    )
    return AuthSessionResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=principal_to_public(principal),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_rate_limit("auth", use_user=False))],
    summary="Encerrar sessão",
    description="Remove os cookies de sessão de autenticação no cliente.",
)
def logout(response: Response) -> None:
    clear_refresh_cookie(response)
    clear_access_cookie(response)
    response.delete_cookie(key=PKCE_COOKIE, path="/auth")


@router.get(
    "/me",
    response_model=PublicUser,
    dependencies=[Depends(enforce_rate_limit("default"))],
    summary="Consultar usuário autenticado",
    description=(
        "Retorna id e display_name do usuário identificado pelo access token (JWT)."
    ),
)
def me(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_user)],
) -> PublicUser:
    return principal_to_public(principal)
