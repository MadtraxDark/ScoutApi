"""API authentication and authorization (Supabase Auth)."""

from scout_api.modules.auth.deps import (
    require_admin,
    require_authenticated_user,
    require_permission,
)
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, PublicUser, UserRole

__all__ = [
    "AuthenticatedPrincipal",
    "PublicUser",
    "UserRole",
    "require_admin",
    "require_authenticated_user",
    "require_permission",
]
