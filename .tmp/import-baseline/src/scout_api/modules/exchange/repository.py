"""Repository for exchange-rate ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from scout_api.modules.exchange.domain import FetchedRate, RateStatus, RateType
from scout_api.modules.exchange.models import (
    ExchangeRateLatest,
    ExchangeRateObservation,
    ExchangeRateSchedulerState,
)


class ExchangeRateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ latest

    def get_latest(
        self,
        base_currency: str,
        quote_currency: str,
        rate_type: RateType,
    ) -> ExchangeRateLatest | None:
        stmt = (
            select(ExchangeRateLatest)
            .where(ExchangeRateLatest.base_currency == base_currency)
            .where(ExchangeRateLatest.quote_currency == quote_currency)
            .where(ExchangeRateLatest.rate_type == str(rate_type))
        )
        return self._session.scalars(stmt).first()

    def list_latest(
        self,
        *,
        base_currency: str | None = None,
        quote_currency: str | None = None,
    ) -> list[ExchangeRateLatest]:
        stmt = select(ExchangeRateLatest)
        if base_currency:
            stmt = stmt.where(ExchangeRateLatest.base_currency == base_currency)
        if quote_currency:
            stmt = stmt.where(ExchangeRateLatest.quote_currency == quote_currency)
        return list(self._session.scalars(stmt).all())

    def upsert_latest(
        self,
        fetched: FetchedRate,
        *,
        status: RateStatus = RateStatus.FRESH,
    ) -> ExchangeRateLatest:
        """Upsert the latest rate row. Uses PostgreSQL ON CONFLICT."""
        now = datetime.now(UTC)
        stmt = pg_insert(ExchangeRateLatest).values(
            base_currency=fetched.base_currency,
            quote_currency=fetched.quote_currency,
            rate_type=str(fetched.rate_type),
            rate=fetched.rate,
            source=fetched.source,
            source_timestamp=fetched.source_timestamp,
            fetched_at=fetched.fetched_at,
            status=str(status),
            consecutive_failures=0,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_exchange_rate_latest_key",
            set_={
                "rate": stmt.excluded.rate,
                "source": stmt.excluded.source,
                "source_timestamp": stmt.excluded.source_timestamp,
                "fetched_at": stmt.excluded.fetched_at,
                "status": stmt.excluded.status,
                "consecutive_failures": 0,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self._session.execute(stmt)
        self._session.flush()
        return self.get_latest(  # type: ignore[return-value]
            fetched.base_currency, fetched.quote_currency, fetched.rate_type
        )

    def mark_stale(self, *, stale_after_seconds: int) -> int:
        """Mark all fresh rates older than stale_after_seconds as stale."""
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        result = self._session.execute(
            update(ExchangeRateLatest)
            .where(ExchangeRateLatest.status == "fresh")
            .where(ExchangeRateLatest.fetched_at < cutoff)
            .values(status="stale", updated_at=datetime.now(UTC))
        )
        return result.rowcount  # type: ignore[return-value]

    def increment_failures(
        self,
        base_currency: str,
        quote_currency: str,
        rate_type: RateType,
    ) -> None:
        """Increment consecutive_failures for a rate row."""
        row = self.get_latest(base_currency, quote_currency, rate_type)
        if row is not None:
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.updated_at = datetime.now(UTC)
            self._session.flush()

    # ----------------------------------------------------------- observations

    def append_observation(self, fetched: FetchedRate) -> ExchangeRateObservation:
        obs = ExchangeRateObservation(
            base_currency=fetched.base_currency,
            quote_currency=fetched.quote_currency,
            rate_type=str(fetched.rate_type),
            rate=fetched.rate,
            source=fetched.source,
            source_timestamp=fetched.source_timestamp,
            fetched_at=fetched.fetched_at,
        )
        self._session.add(obs)
        self._session.flush()
        return obs

    # ----------------------------------------------------------- scheduler state

    def get_scheduler_state(self) -> ExchangeRateSchedulerState | None:
        return self._session.get(ExchangeRateSchedulerState, 1)

    def get_or_create_scheduler_state(self) -> ExchangeRateSchedulerState:
        state = self.get_scheduler_state()
        if state is None:
            state = ExchangeRateSchedulerState(id=1)
            self._session.add(state)
            self._session.flush()
        return state

    def update_scheduler_state(
        self,
        *,
        next_refresh_at: datetime | None = None,
        last_refresh_at: datetime | None = None,
        last_success_at: datetime | None = None,
        last_error: str | None = None,
        consecutive_failures: int | None = None,
    ) -> ExchangeRateSchedulerState:
        state = self.get_or_create_scheduler_state()
        if next_refresh_at is not None:
            state.next_refresh_at = next_refresh_at
        if last_refresh_at is not None:
            state.last_refresh_at = last_refresh_at
        if last_success_at is not None:
            state.last_success_at = last_success_at
        if last_error is not None:
            state.last_error = last_error[:1000]
        if consecutive_failures is not None:
            state.consecutive_failures = consecutive_failures
        state.updated_at = datetime.now(UTC)
        self._session.flush()
        return state

    # ----------------------------------------------------------------- LKG map

    def get_last_known_map(self) -> dict[tuple[str, str, RateType], Decimal]:
        """Return current rates as a lookup map for outlier validation."""
        rows = self.list_latest()
        result: dict[tuple[str, str, RateType], Decimal] = {}
        for row in rows:
            try:
                rt = RateType(row.rate_type)
            except ValueError:
                continue
            result[(row.base_currency, row.quote_currency, rt)] = Decimal(str(row.rate))
        return result
