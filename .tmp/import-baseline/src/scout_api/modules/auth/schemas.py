"""Authenticated principal and public user schemas (whitelist minimization)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Internal identity derived from a verified Supabase JWT (never serialize raw)."""

    id: UUID
    role: UserRole = UserRole.USER
    email: str | None = None
    display_name: str | None = None
    provider: str | None = None
    claims: dict[str, object] = field(default_factory=dict, repr=False)

    def has_permission(self, permission: str) -> bool:
        if self.role == UserRole.ADMIN:
            return True
        # Authenticated users: business permissions; admin-only gated separately.
        return permission in {
            "crawl",
            "match",
            "products:read",
            "products:write",
            "offers:refresh",
            "exchange:read",
        }


class PublicUser(BaseModel):
    """Dados mínimos do usuário expostos pela API pública."""

    id: UUID
    display_name: str | None = None


class AuthSessionResponse(BaseModel):
    """Sessão autenticada: access token para a API e dados públicos do usuário."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    user: PublicUser


class GoogleAuthStartResponse(BaseModel):
    authorization_url: str = Field(
        description="URL para redirecionar o usuário ao login Google."
    )


class AuthErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


def principal_to_public(principal: AuthenticatedPrincipal) -> PublicUser:
    return PublicUser(id=principal.id, display_name=principal.display_name)
