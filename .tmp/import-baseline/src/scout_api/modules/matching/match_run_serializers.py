"""Serialize durable MatchRun / Notification ORM rows to API views."""

from __future__ import annotations

from typing import Any

from scout_api.modules.matching.models import (
    MatchCandidateLog,
    MatchStoreRun,
    ProductMatchRun,
    UserNotification,
)
from scout_api.modules.matching.schemas import (
    MatchCandidateLogView,
    MatchRunDetailView,
    MatchRunStatusView,
    MatchStoreRunView,
    NotificationView,
)


def match_run_to_status(
    run: ProductMatchRun, *, already_active: bool = False
) -> MatchRunStatusView:
    claimed_at = run.claimed_at
    active_since = claimed_at or run.started_at
    return MatchRunStatusView(
        id=run.id,
        product_id=run.product_id,
        status=run.status,  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_activity_at=run.last_activity_at,
        claimed_at=claimed_at,
        active_since=active_since,
        attempts=int(run.attempts or 0),
        total_duration_ms=run.total_duration_ms,
        stores_total=int(run.stores_total or 0),
        stores_completed=int(run.stores_completed or 0),
        matches_found=int(run.matches_found or 0),
        no_matches=int(run.no_matches or 0),
        errors=int(run.errors or 0),
        failure_code=run.failure_code,
        failure_message=run.failure_message,
        already_active=already_active,
    )


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def candidate_to_view(row: MatchCandidateLog) -> MatchCandidateLogView:
    return MatchCandidateLogView(
        sequence=int(row.sequence or 0),
        title=row.title,
        url=row.url,
        store_product_id=row.store_product_id,
        decision=row.decision,
        confidence=row.confidence,
        reasons=_as_str_list(row.reasons),
        embedding_evidence=row.embedding_evidence,
        duration_ms=row.duration_ms,
    )


def store_run_to_view(
    row: MatchStoreRun, *, include_candidates: bool = True
) -> MatchStoreRunView:
    candidates: list[MatchCandidateLogView] = []
    if include_candidates:
        candidates = [candidate_to_view(c) for c in (row.candidates or [])]
    return MatchStoreRunView(
        id=row.id,
        store=row.store,
        store_display_name=row.store_display_name,
        status=row.status,  # type: ignore[arg-type]
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        queries=_as_str_list(row.queries),
        queries_count=int(row.queries_count or 0),
        candidates_found=int(row.candidates_found or 0),
        candidates_evaluated=int(row.candidates_evaluated or 0),
        matched_url=row.matched_url,
        matched_title=row.matched_title,
        matched_price=row.matched_price,
        matched_currency=row.matched_currency,
        matched_confidence=row.matched_confidence,
        matched_reasons=_as_str_list(row.matched_reasons),
        error_code=row.error_code,
        error_message=row.error_message,
        search_duration_ms=row.search_duration_ms,
        candidate_fetch_duration_ms=row.candidate_fetch_duration_ms,
        candidates=candidates,
    )


def match_run_to_detail(run: ProductMatchRun) -> MatchRunDetailView:
    stores = [
        store_run_to_view(row, include_candidates=True)
        for row in (run.store_runs or [])
    ]
    return MatchRunDetailView(run=match_run_to_status(run), stores=stores)


def notification_to_view(row: UserNotification) -> NotificationView:
    return NotificationView(
        id=row.id,
        type=row.type,
        product_id=row.product_id,
        match_run_id=row.match_run_id,
        title=row.title,
        message=row.message,
        created_at=row.created_at,
        read_at=row.read_at,
        metadata=dict(row.metadata_json or {}),
    )
