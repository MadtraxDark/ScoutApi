"""Verify Supabase Auth JWTs (JWKS ES256/RS256 or HS256 secret for tests/legacy)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx
import jwt
from jwt import PyJWKClient

from scout_api.core.config import Settings, get_settings
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole

logger = logging.getLogger(__name__)

_ALLOWED_ASYMMETRIC = ("ES256", "RS256")
_ALLOWED_SYMMETRIC = ("HS256",)


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=300)


def _admin_ids(settings: Settings) -> set[str]:
    raw = (settings.auth_admin_user_ids or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _role_for(sub: str, claims: dict[str, Any], settings: Settings) -> UserRole:
    if sub in _admin_ids(settings):
        return UserRole.ADMIN
    app_meta = claims.get("app_metadata")
    if isinstance(app_meta, dict):
        role = app_meta.get("role")
        if role == "admin":
            return UserRole.ADMIN
    return UserRole.USER


def _display_name(claims: dict[str, Any]) -> str | None:
    meta = claims.get("user_metadata")
    if isinstance(meta, dict):
        for key in ("full_name", "name", "preferred_username"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:128]
    return None


def _provider(claims: dict[str, Any]) -> str | None:
    app_meta = claims.get("app_metadata")
    if isinstance(app_meta, dict):
        provider = app_meta.get("provider")
        if isinstance(provider, str):
            return provider
    return None


def _audience_ok(aud: Any, expected: str) -> bool:
    if aud is None:
        return True
    if aud == expected:
        return True
    return isinstance(aud, list) and expected in aud


def _principal_from_claims(
    claims: dict[str, Any], settings: Settings
) -> AuthenticatedPrincipal:
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise AuthError("INVALID_TOKEN", "Subject JWT ausente")
    try:
        user_id = UUID(sub)
    except ValueError as exc:
        raise AuthError("INVALID_TOKEN", "Subject JWT inválido") from exc
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    return AuthenticatedPrincipal(
        id=user_id,
        role=_role_for(sub, claims, settings),
        email=email,
        display_name=_display_name(claims),
        provider=_provider(claims),
        claims=dict(claims),
    )


def _principal_from_auth_user(
    user: dict[str, Any], settings: Settings
) -> AuthenticatedPrincipal:
    """Build principal from Supabase Auth /user payload (server-validated)."""
    raw_id = user.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise AuthError("INVALID_TOKEN", "Subject Auth ausente")
    try:
        user_id = UUID(raw_id)
    except ValueError as exc:
        raise AuthError("INVALID_TOKEN", "Subject Auth inválido") from exc
    meta = user.get("user_metadata")
    if not isinstance(meta, dict):
        meta = {}
    app_meta = user.get("app_metadata")
    if not isinstance(app_meta, dict):
        app_meta = {}
    claims: dict[str, Any] = {
        "sub": raw_id,
        "email": user.get("email"),
        "user_metadata": meta,
        "app_metadata": app_meta,
    }
    display = None
    for key in ("full_name", "name", "preferred_username"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            display = value.strip()[:128]
            break
    provider = app_meta.get("provider")
    return AuthenticatedPrincipal(
        id=user_id,
        role=_role_for(raw_id, claims, settings),
        email=user.get("email") if isinstance(user.get("email"), str) else None,
        display_name=display,
        provider=provider if isinstance(provider, str) else None,
        claims=claims,
    )


def verify_access_token_via_auth_server(
    token: str,
    settings: Settings | None = None,
) -> AuthenticatedPrincipal:
    """Validate access token with Supabase Auth /user (fallback path)."""
    cfg = settings or get_settings()
    if not cfg.supabase_url or not cfg.supabase_anon_key:
        raise AuthError("AUTH_MISCONFIGURED", "Supabase Auth não configurado")
    url = f"{cfg.supabase_url.rstrip('/')}/auth/v1/user"
    headers = {
        "apikey": cfg.supabase_anon_key,
        "Authorization": f"Bearer {token}",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        raise AuthError(
            "AUTH_PROVIDER_ERROR", "Falha ao validar token no Supabase Auth"
        ) from exc
    if response.status_code >= 400:
        raise AuthError("INVALID_TOKEN", "Token inválido")
    data = response.json()
    if not isinstance(data, dict):
        raise AuthError("AUTH_PROVIDER_ERROR", "Resposta Auth inválida")
    return _principal_from_auth_user(data, cfg)


def _try_auth_server_fallback(
    token: str,
    *,
    settings: Settings,
    alg: str | None,
    kid: Any,
    local_error: BaseException,
) -> AuthenticatedPrincipal:
    if not (settings.supabase_url and settings.supabase_anon_key):
        raise AuthError("INVALID_TOKEN", "Token inválido") from local_error
    try:
        principal = verify_access_token_via_auth_server(token, settings=settings)
    except AuthError:
        raise AuthError("INVALID_TOKEN", "Token inválido") from local_error
    logger.warning(
        "JWT local verify failed; accepted via Supabase /auth/v1/user "
        "alg=%s kid=%s local_error=%s",
        alg,
        kid,
        type(local_error).__name__,
    )
    return principal


def _decode_asymmetric(
    token: str,
    *,
    alg: str,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.supabase_url:
        raise AuthError("AUTH_MISCONFIGURED", "SUPABASE_URL não configurada")
    jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    client = _jwks_client(jwks_url)
    signing_key = client.get_signing_key_from_jwt(token)
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    # Prefer strict aud/iss when claims match Supabase defaults; otherwise
    # accept signature-valid tokens (jose-style) and check claims manually.
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            leeway=30,
            options={
                "require": ["exp", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except (
        jwt.InvalidAudienceError,
        jwt.InvalidIssuerError,
        jwt.MissingRequiredClaimError,
    ):
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            leeway=30,
            options={
                "require": ["exp", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
        if not _audience_ok(claims.get("aud"), settings.supabase_jwt_audience):
            raise AuthError("INVALID_TOKEN", "Audience JWT inválida") from None
        iss = claims.get("iss")
        if iss is not None and iss != issuer:
            raise AuthError("INVALID_TOKEN", "Issuer JWT inválido") from None
        return claims


def _decode_symmetric(token: str, *, settings: Settings) -> dict[str, Any]:
    if not settings.supabase_jwt_secret:
        raise AuthError(
            "AUTH_MISCONFIGURED",
            "SUPABASE_JWT_SECRET necessário para HS256",
        )
    expected_issuer = None
    if settings.supabase_url:
        expected_issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    decode_kwargs: dict[str, Any] = {
        "algorithms": list(_ALLOWED_SYMMETRIC),
        "audience": settings.supabase_jwt_audience,
        "leeway": 30,
        "options": {
            "require": ["exp", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_aud": True,
        },
    }
    if expected_issuer:
        decode_kwargs["issuer"] = expected_issuer
        decode_kwargs["options"]["verify_iss"] = True
    try:
        return jwt.decode(token, settings.supabase_jwt_secret, **decode_kwargs)
    except (
        jwt.InvalidAudienceError,
        jwt.InvalidIssuerError,
        jwt.MissingRequiredClaimError,
    ):
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=list(_ALLOWED_SYMMETRIC),
            leeway=30,
            options={
                "require": ["exp", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
        if not _audience_ok(claims.get("aud"), settings.supabase_jwt_audience):
            raise AuthError("INVALID_TOKEN", "Audience JWT inválida") from None
        if expected_issuer is not None:
            iss = claims.get("iss")
            if iss is not None and iss != expected_issuer:
                raise AuthError("INVALID_TOKEN", "Issuer JWT inválido") from None
        return claims


def verify_access_token(
    token: str,
    settings: Settings | None = None,
    *,
    _jwks_retry: bool = True,
) -> AuthenticatedPrincipal:
    """Validate signature + standard claims; raise AuthError on failure."""
    cfg = settings or get_settings()
    if not token or token.count(".") != 2:
        raise AuthError("INVALID_TOKEN", "Token ausente ou malformado")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("INVALID_TOKEN", "Cabeçalho JWT inválido") from exc

    alg = header.get("alg")
    if alg is None or alg == "none" or not isinstance(alg, str):
        raise AuthError("INVALID_TOKEN", "Algoritmo JWT não permitido")

    try:
        if alg in _ALLOWED_ASYMMETRIC:
            claims = _decode_asymmetric(token, alg=alg, settings=cfg)
        elif alg in _ALLOWED_SYMMETRIC:
            claims = _decode_symmetric(token, settings=cfg)
        else:
            raise AuthError("INVALID_TOKEN", "Algoritmo JWT não permitido")
    except AuthError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("TOKEN_EXPIRED", "Token expirado") from exc
    except jwt.PyJWTError as exc:
        if _jwks_retry and alg in _ALLOWED_ASYMMETRIC and cfg.supabase_url:
            _jwks_client.cache_clear()
            return verify_access_token(token, settings=cfg, _jwks_retry=False)
        logger.warning(
            "JWT verification failed alg=%s kid=%s error_type=%s error=%s",
            alg,
            header.get("kid"),
            type(exc).__name__,
            str(exc),
        )
        return _try_auth_server_fallback(
            token,
            settings=cfg,
            alg=alg,
            kid=header.get("kid"),
            local_error=exc,
        )

    return _principal_from_claims(claims, cfg)


def exchange_auth_code(
    *,
    code: str,
    code_verifier: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Exchange PKCE auth code with Supabase Auth (server-side only)."""
    cfg = settings or get_settings()
    if not cfg.supabase_url or not cfg.supabase_anon_key:
        raise AuthError("AUTH_MISCONFIGURED", "Supabase Auth não configurado")
    url = f"{cfg.supabase_url.rstrip('/')}/auth/v1/token?grant_type=pkce"
    headers = {
        "apikey": cfg.supabase_anon_key,
        "Authorization": f"Bearer {cfg.supabase_anon_key}",
        "Content-Type": "application/json",
    }
    payload = {"auth_code": code, "code_verifier": code_verifier}
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
    except httpx.HTTPError as exc:
        raise AuthError(
            "AUTH_PROVIDER_ERROR", "Falha ao contatar Supabase Auth"
        ) from exc
    if response.status_code >= 400:
        raise AuthError("INVALID_TOKEN", "Código de autenticação inválido")
    data = response.json()
    if not isinstance(data, dict) or "access_token" not in data:
        raise AuthError("AUTH_PROVIDER_ERROR", "Resposta Auth inválida")
    return data


def refresh_session(
    refresh_token: str, settings: Settings | None = None
) -> dict[str, Any]:
    cfg = settings or get_settings()
    if not cfg.supabase_url or not cfg.supabase_anon_key:
        raise AuthError("AUTH_MISCONFIGURED", "Supabase Auth não configurado")
    url = f"{cfg.supabase_url.rstrip('/')}/auth/v1/token?grant_type=refresh_token"
    headers = {
        "apikey": cfg.supabase_anon_key,
        "Authorization": f"Bearer {cfg.supabase_anon_key}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            url,
            headers=headers,
            json={"refresh_token": refresh_token},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError("AUTH_PROVIDER_ERROR", "Falha ao renovar sessão") from exc
    if response.status_code >= 400:
        raise AuthError("INVALID_TOKEN", "Refresh token inválido")
    data = response.json()
    if not isinstance(data, dict) or "access_token" not in data:
        raise AuthError("AUTH_PROVIDER_ERROR", "Resposta Auth inválida")
    return data


def build_google_authorize_url(
    *,
    redirect_to: str,
    code_challenge: str,
    settings: Settings | None = None,
) -> str:
    cfg = settings or get_settings()
    if not cfg.supabase_url:
        raise AuthError("AUTH_MISCONFIGURED", "SUPABASE_URL não configurada")
    from urllib.parse import urlencode

    query = urlencode(
        {
            "provider": "google",
            "redirect_to": redirect_to,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{cfg.supabase_url.rstrip('/')}/auth/v1/authorize?{query}"
