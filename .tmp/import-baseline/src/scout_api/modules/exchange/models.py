"""SQLAlchemy ORM models for the exchange-rate subsystem (ADR 0034)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from scout_api.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExchangeRateLatest(Base):
    """Latest known-good rate for each (base_currency, quote_currency, rate_type).

    Single-row upsert: always represents the most recently validated rate.
    Consumers read from here; never from ExchangeRateObservation directly.
    """

    __tablename__ = "exchange_rate_latest"
    __table_args__ = (
        UniqueConstraint(
            "base_currency",
            "quote_currency",
            "rate_type",
            name="uq_exchange_rate_latest_key",
        ),
        Index("ix_exchange_rate_latest_pair", "base_currency", "quote_currency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    rate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Decimal(24,12) — enough precision for PYG/BRL ≈ 0.000859
    rate: Mapped[object] = mapped_column(Numeric(24, 12), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="fresh"
    )
    """fresh | stale | unavailable"""
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ExchangeRateObservation(Base):
    """Append-only history of all validated rate observations."""

    __tablename__ = "exchange_rate_observations"
    __table_args__ = (
        Index(
            "ix_exchange_rate_obs_pair_type",
            "base_currency",
            "quote_currency",
            "rate_type",
        ),
        Index("ix_exchange_rate_obs_observed_at", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    rate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rate: Mapped[object] = mapped_column(Numeric(24, 12), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class ExchangeRateSchedulerState(Base):
    """Singleton row (id=1) tracking the refresh scheduler state."""

    __tablename__ = "exchange_rate_scheduler_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    """Always 1."""
    next_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
