"""Alembic migration helpers and schema bootstrap."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from scout_api.core.database import Base
from scout_api.modules.images import models as _image_models  # noqa: F401
from scout_api.modules.matching import models as _models  # noqa: F401


def create_all(engine: Engine) -> None:
    """Create matching tables (dev/test bootstrap; prefer Alembic in prod)."""
    Base.metadata.create_all(bind=engine)
