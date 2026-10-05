"""SQLAlchemy engine and session helpers for domain persistence.

PostgreSQL (including Supabase-hosted) is the source of truth. Connect via
``DATABASE_URL`` with the ``postgresql+psycopg`` driver — never the Supabase
Data API / PostgREST for backend persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from scout_api.core.config import Settings, get_settings

DatabaseStatus = Literal["ok", "unavailable", "not_configured"]

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def normalize_database_url(url: str) -> str:
    """Normalize postgres URLs to the SQLAlchemy + psycopg3 dialect."""
    cleaned = url.strip()
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned.removeprefix("postgres://")
    if (
        cleaned.startswith("postgresql://")
        and "+psycopg" not in cleaned.split("://", 1)[0]
    ):
        cleaned = "postgresql+psycopg://" + cleaned.removeprefix("postgresql://")
    return cleaned


def is_supabase_host(url: str) -> bool:
    host = (make_url(normalize_database_url(url)).host or "").lower()
    return "supabase.co" in host or "pooler.supabase.com" in host


def is_transaction_pooler(url: str) -> bool:
    """Supabase/Supavisor transaction mode uses port 6543 (no prepared stmts)."""
    parsed = make_url(normalize_database_url(url))
    return parsed.port == 6543


def ensure_sslmode(url: str, *, default_sslmode: str | None) -> str:
    """Append sslmode when connecting to Supabase and none was provided."""
    normalized = normalize_database_url(url)
    parsed = urlparse(normalized)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" in query:
        return normalized
    if default_sslmode:
        query["sslmode"] = default_sslmode
        return urlunparse(parsed._replace(query=urlencode(query)))
    if is_supabase_host(normalized):
        query["sslmode"] = "require"
        return urlunparse(parsed._replace(query=urlencode(query)))
    return normalized


def build_connect_args(settings: Settings, url: str) -> dict[str, Any]:
    """Driver connect args (timeouts; disable prepared stmts on Tx pooler)."""
    connect_args: dict[str, Any] = {
        "connect_timeout": max(1, int(settings.database_connect_timeout_seconds)),
    }
    options: list[str] = []
    if settings.database_statement_timeout_ms > 0:
        options.append(f"-c statement_timeout={settings.database_statement_timeout_ms}")
    if settings.database_application_name:
        options.append(f"-c application_name={settings.database_application_name}")
    if options:
        connect_args["options"] = " ".join(options)
    if is_transaction_pooler(url):
        # Supavisor transaction mode does not support prepared statements.
        connect_args["prepare_threshold"] = None
    return connect_args


def resolve_engine_url(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    if not cfg.database_url:
        raise RuntimeError(
            "DATABASE_URL não configurada. Defina a URL PostgreSQL para "
            "persistência de matching/ofertas."
        )
    sslmode = cfg.database_sslmode
    return ensure_sslmode(cfg.database_url, default_sslmode=sslmode)


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Create a configured SQLAlchemy engine (not cached)."""
    cfg = settings or get_settings()
    url = resolve_engine_url(cfg)
    connect_args = build_connect_args(cfg, url)
    common: dict[str, Any] = {
        "pool_pre_ping": True,
        "future": True,
        "connect_args": connect_args,
    }
    if is_transaction_pooler(url):
        # Serverless / transaction pooler: rely on Supavisor; no client QueuePool.
        engine = create_engine(url, poolclass=NullPool, **common)
    else:
        engine = create_engine(
            url,
            pool_size=cfg.database_pool_size,
            max_overflow=cfg.database_max_overflow,
            pool_timeout=cfg.database_pool_timeout_seconds,
            pool_recycle=cfg.database_pool_recycle_seconds,
            **common,
        )
    from scout_api.core.performance import attach_slow_query_listener

    attach_slow_query_listener(engine)
    return engine


@lru_cache
def get_engine() -> Engine:
    return create_db_engine()


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database(engine: Engine | None = None) -> None:
    """Execute ``SELECT 1``; raises on connection/query failure."""
    eng = engine or get_engine()
    with eng.connect() as connection:
        connection.execute(text("SELECT 1"))


def check_database() -> DatabaseStatus:
    """Return database readiness without raising."""
    settings = get_settings()
    if not settings.database_url:
        return "not_configured"
    try:
        ping_database()
    except Exception:
        return "unavailable"
    return "ok"


def dispose_database_engine() -> None:
    """Dispose the cached Engine (if any) and clear session/engine caches.

    Idempotent and lazy-safe: does not create an Engine or open a connection
    when none has been cached yet.
    """
    get_session_factory.cache_clear()
    if get_engine.cache_info().currsize == 0:
        return
    engine = get_engine()
    try:
        engine.dispose()
        logger.debug("database_engine_disposed")
    finally:
        get_engine.cache_clear()


def reset_database_cache() -> None:
    """Dispose pooled connections and clear cached engine/session factory."""
    dispose_database_engine()
