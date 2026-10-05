"""Map SQLAlchemy / DBAPI failures to stable API error codes."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError


@dataclass(frozen=True, slots=True)
class DatabaseFailure:
    """Normalized database failure for HTTP / domain boundaries."""

    code: str
    message: str
    retryable: bool


def classify_database_error(exc: BaseException) -> DatabaseFailure:
    """Classify a database exception without leaking credentials or SQL."""
    if isinstance(exc, IntegrityError):
        return DatabaseFailure(
            code="DATABASE_INTEGRITY_ERROR",
            message="Violação de integridade no banco de dados",
            retryable=False,
        )
    if isinstance(exc, OperationalError):
        return DatabaseFailure(
            code="DATABASE_UNAVAILABLE",
            message="Banco de dados indisponível ou timeout de conexão",
            retryable=True,
        )
    if isinstance(exc, DBAPIError):
        return DatabaseFailure(
            code="DATABASE_ERROR",
            message="Erro do driver PostgreSQL",
            retryable=False,
        )
    if isinstance(exc, SQLAlchemyError):
        return DatabaseFailure(
            code="DATABASE_ERROR",
            message="Erro de persistência SQLAlchemy",
            retryable=False,
        )
    return DatabaseFailure(
        code="DATABASE_ERROR",
        message="Erro inesperado de banco de dados",
        retryable=False,
    )


def is_database_error(exc: BaseException) -> bool:
    return isinstance(exc, SQLAlchemyError)
