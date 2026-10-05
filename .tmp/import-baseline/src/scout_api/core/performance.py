"""Performance observability — budgets, slow events, retry/duplicate tracking.

Canonical thresholds and agent policy: ``docs/performance.md`` + ADR 0028.
Never log secrets, tokens, cookies, or credentials.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from scout_api.core.log_redaction import redact_mapping

logger = logging.getLogger(__name__)


class Severity(StrEnum):
    NORMAL = "NORMAL"
    WARN = "WARN"
    SLOW = "SLOW"
    CRITICAL = "CRITICAL"


class OperationCategory(StrEnum):
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    LIVE_TEST = "live_test"
    E2E = "e2e"
    HTTP_REQUEST = "http_request"
    BROWSER_NAVIGATION = "browser_navigation"
    BROWSER_LAUNCH = "browser_launch"
    CRAWLER = "crawler"
    PRODUCT_SEARCH = "product_search"
    PRODUCT_MATCH = "product_match"
    DATABASE_QUERY = "database_query"
    MIGRATION = "migration"
    DOCKER_BUILD = "docker_build"
    CI_JOB = "ci_job"
    AGENT_SHELL = "agent_shell"
    AGENT_RESEARCH = "agent_research"
    EXTERNAL_TOOL = "external_tool"


@dataclass(frozen=True, slots=True)
class Budget:
    """Duration budgets in milliseconds for one operation category."""

    expected_ms: int
    warn_ms: int
    slow_ms: int
    critical_ms: int


# Single source of truth for numeric thresholds (no magic numbers elsewhere).
BUDGETS: Mapping[OperationCategory, Budget] = {
    OperationCategory.UNIT_TEST: Budget(200, 500, 2_000, 10_000),
    OperationCategory.INTEGRATION_TEST: Budget(1_000, 2_000, 10_000, 60_000),
    OperationCategory.LIVE_TEST: Budget(15_000, 30_000, 120_000, 600_000),
    OperationCategory.E2E: Budget(30_000, 60_000, 300_000, 900_000),
    OperationCategory.HTTP_REQUEST: Budget(1_500, 3_000, 15_000, 60_000),
    OperationCategory.BROWSER_NAVIGATION: Budget(5_000, 10_000, 45_000, 120_000),
    OperationCategory.BROWSER_LAUNCH: Budget(3_000, 5_000, 20_000, 60_000),
    OperationCategory.CRAWLER: Budget(8_000, 15_000, 60_000, 180_000),
    OperationCategory.PRODUCT_SEARCH: Budget(2_000, 5_000, 30_000, 120_000),
    OperationCategory.PRODUCT_MATCH: Budget(15_000, 30_000, 180_000, 600_000),
    OperationCategory.DATABASE_QUERY: Budget(50, 200, 1_000, 5_000),
    OperationCategory.MIGRATION: Budget(2_000, 5_000, 30_000, 120_000),
    OperationCategory.DOCKER_BUILD: Budget(45_000, 60_000, 300_000, 900_000),
    OperationCategory.CI_JOB: Budget(180_000, 300_000, 900_000, 1_800_000),
    OperationCategory.AGENT_SHELL: Budget(15_000, 30_000, 120_000, 600_000),
    OperationCategory.AGENT_RESEARCH: Budget(60_000, 120_000, 600_000, 1_200_000),
    OperationCategory.EXTERNAL_TOOL: Budget(5_000, 15_000, 60_000, 300_000),
}


@dataclass(frozen=True, slots=True)
class PerformanceEvent:
    event: str
    operation: str
    category: OperationCategory
    duration_ms: float
    expected_ms: int
    severity: Severity
    stage: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_log_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": self.event,
            "operation": self.operation,
            "category": self.category.value,
            "duration_ms": round(self.duration_ms, 1),
            "expected_ms": self.expected_ms,
            "severity": self.severity.value,
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.context:
            payload["context"] = dict(self.context)
        return redact_mapping(payload)


def classify_severity(
    duration_ms: float,
    budget: Budget,
) -> Severity:
    if duration_ms >= budget.critical_ms:
        return Severity.CRITICAL
    if duration_ms >= budget.slow_ms:
        return Severity.SLOW
    if duration_ms >= budget.warn_ms:
        return Severity.WARN
    return Severity.NORMAL


def budget_for(category: OperationCategory | str) -> Budget:
    if isinstance(category, str):
        category = OperationCategory(category)
    return BUDGETS[category]


def observe(
    operation: str,
    duration_ms: float,
    *,
    category: OperationCategory | str,
    stage: str | None = None,
    context: Mapping[str, Any] | None = None,
    force_event: bool = False,
) -> PerformanceEvent:
    """Record duration; emit ``slow_operation`` when above WARN (or force_event)."""
    cat = OperationCategory(category) if isinstance(category, str) else category
    budget = BUDGETS[cat]
    severity = classify_severity(duration_ms, budget)
    event = PerformanceEvent(
        event="slow_operation" if severity != Severity.NORMAL else "operation_timing",
        operation=operation,
        category=cat,
        duration_ms=duration_ms,
        expected_ms=budget.expected_ms,
        severity=severity,
        stage=stage,
        context=context or {},
    )
    if severity == Severity.NORMAL and not force_event:
        return event

    payload = event.as_log_dict()
    log_fn = (
        logger.warning
        if severity in {Severity.SLOW, Severity.CRITICAL}
        else logger.info
    )
    log_fn(
        "%s operation=%s category=%s duration_ms=%s expected_ms=%s severity=%s",
        event.event,
        operation,
        cat.value,
        payload["duration_ms"],
        budget.expected_ms,
        severity.value,
        extra=payload,
    )
    return event


@contextmanager
def timed(
    operation: str,
    *,
    category: OperationCategory | str,
    stage: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Context manager that observes elapsed wall time on exit.

    Yields a mutable bag; callers may add keys that are merged into the
    event ``context`` (except ``duration_ms``, set automatically).
    """
    bag: dict[str, Any] = dict(context or {})
    t0 = time.perf_counter()
    try:
        yield bag
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        bag["duration_ms"] = duration_ms
        extras = {k: v for k, v in bag.items() if k != "duration_ms"}
        observe(
            operation,
            duration_ms,
            category=category,
            stage=stage,
            context=extras,
        )


@dataclass
class RetryAttempt:
    attempt: int
    duration_ms: float
    outcome: str
    code: str | None = None
    backoff_ms: float = 0.0


@dataclass
class RetryLedger:
    """Tracks per-attempt timing so retries are not collapsed into one duration."""

    operation: str
    category: OperationCategory = OperationCategory.HTTP_REQUEST
    attempts: list[RetryAttempt] = field(default_factory=list)
    _attempt_t0: float | None = field(default=None, repr=False)

    def begin_attempt(self) -> None:
        self._attempt_t0 = time.perf_counter()

    def end_attempt(
        self,
        *,
        outcome: str,
        code: str | None = None,
        backoff_ms: float = 0.0,
    ) -> None:
        started = (
            self._attempt_t0 if self._attempt_t0 is not None else time.perf_counter()
        )
        duration_ms = (time.perf_counter() - started) * 1000
        self.attempts.append(
            RetryAttempt(
                attempt=len(self.attempts) + 1,
                duration_ms=duration_ms,
                outcome=outcome,
                code=code,
                backoff_ms=backoff_ms,
            )
        )
        self._attempt_t0 = None

    def record_backoff(self, delay_seconds: float) -> None:
        if self.attempts:
            last = self.attempts[-1]
            self.attempts[-1] = RetryAttempt(
                attempt=last.attempt,
                duration_ms=last.duration_ms,
                outcome=last.outcome,
                code=last.code,
                backoff_ms=round(delay_seconds * 1000, 1),
            )

    @property
    def total_ms(self) -> float:
        return sum(a.duration_ms + a.backoff_ms for a in self.attempts)

    @property
    def backoff_ms_total(self) -> float:
        return sum(a.backoff_ms for a in self.attempts)

    def as_context(self) -> dict[str, Any]:
        return {
            "retries": max(0, len(self.attempts) - 1),
            "attempts": len(self.attempts),
            "backoff_ms_total": round(self.backoff_ms_total, 1),
            "attempt_timings": [
                {
                    "attempt": a.attempt,
                    "duration_ms": round(a.duration_ms, 1),
                    "backoff_ms": round(a.backoff_ms, 1),
                    "outcome": a.outcome,
                    "code": a.code,
                }
                for a in self.attempts
            ],
        }

    def observe(
        self,
        *,
        stage: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> PerformanceEvent:
        context = {**self.as_context(), **dict(extra or {})}
        return observe(
            self.operation,
            self.total_ms,
            category=self.category,
            stage=stage,
            context=context,
            force_event=len(self.attempts) > 1,
        )


@dataclass
class DuplicateWorkTracker:
    """Counts repeated keys (URLs, queries, tool calls) within one scope."""

    _counts: Counter[str] = field(default_factory=Counter)

    def record(self, kind: str, key: str) -> int:
        token = f"{kind}:{key}"
        self._counts[token] += 1
        return self._counts[token]

    def duplicates(self, *, min_count: int = 2) -> dict[str, int]:
        return {k: c for k, c in self._counts.items() if c >= min_count}

    def observe_if_repeated(
        self,
        *,
        operation: str = "duplicate_work",
        category: OperationCategory = OperationCategory.CRAWLER,
    ) -> None:
        dups = self.duplicates()
        if not dups:
            return
        # Cap payload size — only top offenders.
        top = dict(sorted(dups.items(), key=lambda item: item[1], reverse=True)[:20])
        payload = redact_mapping(
            {
                "event": "duplicate_work",
                "operation": operation,
                "category": category.value,
                "duplicates": top,
                "duplicate_keys": len(dups),
            }
        )
        logger.warning(
            "duplicate_work operation=%s keys=%s",
            operation,
            len(dups),
            extra=payload,
        )


def format_stage_summary(
    title: str,
    total_ms: float,
    stages: Mapping[str, float | Mapping[str, Any]],
) -> str:
    """Human-readable multi-stage timing summary (no secrets)."""
    lines = [f"{title}: {total_ms / 1000:.1f}s"]
    for name, value in stages.items():
        if isinstance(value, Mapping):
            detail = ", ".join(f"{k}={v}" for k, v in value.items())
            lines.append(f"  {name}: {detail}")
        else:
            lines.append(f"  {name}: {value / 1000:.3f}s")
    return "\n".join(lines)


def attach_slow_query_listener(engine: Any, *, min_ms: float | None = None) -> None:
    """Register SQLAlchemy ``before/after_cursor_execute`` slow-query observer."""
    from sqlalchemy import event

    threshold = (
        float(min_ms)
        if min_ms is not None
        else float(BUDGETS[OperationCategory.DATABASE_QUERY].warn_ms)
    )
    # Avoid double-registration on engine recreate in tests.
    if getattr(engine, "_scout_slow_query_listener", False):
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(  # type: ignore[no-untyped-def]
        conn, _cursor, _statement, _parameters, context, _executemany
    ) -> None:
        conn.info["scout_query_t0"] = time.perf_counter()
        context._scout_query_t0 = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(  # type: ignore[no-untyped-def]
        conn, _cursor, statement, _parameters, context, _executemany
    ) -> None:
        t0 = conn.info.pop("scout_query_t0", None) or getattr(
            context, "_scout_query_t0", None
        )
        if t0 is None:
            return
        duration_ms = (time.perf_counter() - t0) * 1000
        if duration_ms < threshold:
            return
        # Statement preview only — never bind parameters (may hold secrets).
        preview = " ".join(str(statement).split())[:180]
        observe(
            "database_query",
            duration_ms,
            category=OperationCategory.DATABASE_QUERY,
            stage="sql",
            context={"statement_preview": preview},
        )

    engine._scout_slow_query_listener = True


def category_for_pytest_item(item: Any) -> OperationCategory:
    """Map a pytest item's markers to a budget category."""
    names = {mark.name for mark in getattr(item, "iter_markers", lambda: [])()}
    if "live" in names:
        return OperationCategory.LIVE_TEST
    if "e2e" in names:
        return OperationCategory.E2E
    if "slow" in names:
        return OperationCategory.E2E
    if "integration" in names:
        return OperationCategory.INTEGRATION_TEST
    path = str(getattr(item, "fspath", "") or getattr(item, "path", "") or "")
    if "integration" in path.replace("\\", "/"):
        return OperationCategory.INTEGRATION_TEST
    return OperationCategory.UNIT_TEST
