"""Fake-clock tests for store / run wall deadlines in Product Match."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from scout_api.core.config import Settings
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.match_deadlines import MonotonicDeadline
from scout_api.modules.matching.match_hang_constants import (
    FAILURE_CODE_RUN_WALL_TIMEOUT,
    FAILURE_CODE_STORE_WALL_TIMEOUT,
)
from scout_api.modules.matching.match_progress import MatchProgressTracker
from scout_api.modules.matching.product_match_service import (
    MatchStoreOutcome,
    ProductMatchService,
)
from scout_api.modules.matching.search_candidate import SearchCandidate


def _ref_item():
    return identity_reference_item(
        "Samsung Galaxy S25 Ultra 256GB",
        brand="Samsung",
        model="Galaxy S25 Ultra",
    )


def test_store_wall_timeout_is_error_not_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    search = MagicMock()
    search.is_search_supported.return_value = True

    def slow_search(*_a, **_k):  # noqa: ANN002, ANN003
        clock["t"] += 200.0  # exceed 180s store wall after return
        return [
            SearchCandidate(
                store="amazon_br",
                title="Samsung Galaxy S25 Ultra",
                url="https://www.amazon.com.br/dp/B0TEST",
            )
        ]

    search.search.side_effect = slow_search
    scrape = MagicMock()
    scrape.scrape.side_effect = AssertionError(
        "scrape should not run after wall timeout"
    )

    outcomes: list[MatchStoreOutcome] = []
    tracker = MatchProgressTracker(monotonic=mono)
    tracker.arm(run_id="run-1", worker_id="w1")

    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.get_settings",
        lambda: Settings(
            match_store_wall_timeout_seconds=180.0,
            match_run_wall_timeout_seconds=2700.0,
            match_store_concurrency=1,
            match_search_query_budget=5,
            match_external_attempt_budget=12,
            match_browser_navigation_budget=8,
        ),
    )
    # Inject fake monotonic clock into MonotonicDeadline.start / nested_deadline.
    real_start = MonotonicDeadline.start

    def start_with_clock(*, timeout_seconds: float, monotonic=None):  # noqa: ANN001
        return real_start(timeout_seconds=timeout_seconds, monotonic=mono)

    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.MonotonicDeadline.start",
        start_with_clock,
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.nested_deadline",
        lambda outer, *, timeout_seconds, monotonic=None: (
            real_start(timeout_seconds=timeout_seconds, monotonic=mono)
            if outer.disabled
            else real_start(
                timeout_seconds=min(
                    timeout_seconds, outer.remaining_seconds() or timeout_seconds
                ),
                monotonic=mono,
            )
        ),
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.eligible_match_store_keys",
        lambda: ["amazon_br"],
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.split_stores_for_match_waves",
        lambda stores: (list(stores), [], []),
    )

    svc = ProductMatchService(search_service=search, scrape_service=scrape)
    # Advance clock past store wall before first query check: start deadline at t=0,
    # then jump before process_one loop checks.
    run_deadline = MonotonicDeadline.start(timeout_seconds=2700.0, monotonic=mono)

    # After arming store deadline at t=0 inside process_one, jump past 180 before loop.
    # Simpler path: set clock so store_deadline expires immediately at first loop check.
    # process_one creates store deadline at current mono; then for-loop checks.
    # So: let process_one start at t=0, then before search we need expired.
    # Our slow_search advances after search returns — first check is before search.
    # Advance time by wrapping begin of process via search.is_search_supported.

    def supported(_store: str) -> bool:
        clock["t"] = 181.0  # expire store wall before query loop
        return True

    search.is_search_supported.side_effect = supported

    resp = svc.match_from_item(
        _ref_item(),
        stores=["amazon_br"],
        persist=False,
        on_store_outcome=outcomes.append,
        run_deadline=run_deadline,
        progress_tracker=tracker,
        run_id="run-1",
    )
    assert outcomes
    assert outcomes[0].status == "error"
    assert outcomes[0].error_code == FAILURE_CODE_STORE_WALL_TIMEOUT
    assert not any(o.status == "no_match" for o in outcomes)
    assert resp.errors
    assert resp.errors[0].code == FAILURE_CODE_STORE_WALL_TIMEOUT
    assert search.search.call_count == 0


def test_browser_queue_expiry_maps_to_store_wall_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = MagicMock()
    search.is_search_supported.return_value = True

    def queue_until_store_deadline(*_args, **_kwargs):  # noqa: ANN002, ANN003
        time.sleep(0.04)
        raise RequestError(
            "deadline da loja expirou na fila", code="BROWSER_QUEUE_TIMEOUT"
        )

    search.search.side_effect = queue_until_store_deadline
    outcomes: list[MatchStoreOutcome] = []
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.get_settings",
        lambda: Settings(
            match_store_wall_timeout_seconds=0.01,
            match_run_wall_timeout_seconds=2700.0,
            match_store_concurrency=1,
            match_search_query_budget=5,
            match_external_attempt_budget=12,
            match_browser_navigation_budget=8,
        ),
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.eligible_match_store_keys",
        lambda: ["amazon_br"],
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.split_stores_for_match_waves",
        lambda stores: (list(stores), [], []),
    )
    tracker = MatchProgressTracker()
    tracker.arm(run_id="queue-deadline", worker_id="test")
    service = ProductMatchService(search_service=search, scrape_service=MagicMock())

    response = service.match_from_item(
        _ref_item(),
        stores=["amazon_br"],
        persist=False,
        on_store_outcome=outcomes.append,
        run_deadline=MonotonicDeadline.start(timeout_seconds=2700.0),
        progress_tracker=tracker,
        run_id="queue-deadline",
    )

    assert outcomes[0].error_code == FAILURE_CODE_STORE_WALL_TIMEOUT
    assert response.errors[0].code == FAILURE_CODE_STORE_WALL_TIMEOUT


def test_browser_queue_expiry_during_candidate_scrape_maps_to_store_wall_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(
            store="amazon_br",
            title="Samsung Galaxy S25 Ultra",
            url="https://www.amazon.com.br/dp/B0TEST",
        )
    ]
    scrape = MagicMock()

    def queue_until_store_deadline(*_args, **_kwargs):  # noqa: ANN002, ANN003
        time.sleep(0.04)
        raise RequestError(
            "deadline da loja expirou na fila", code="BROWSER_QUEUE_TIMEOUT"
        )

    scrape.scrape.side_effect = queue_until_store_deadline
    outcomes: list[MatchStoreOutcome] = []
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.get_settings",
        lambda: Settings(
            match_store_wall_timeout_seconds=0.01,
            match_run_wall_timeout_seconds=2700.0,
            match_store_concurrency=1,
            match_search_query_budget=5,
            match_external_attempt_budget=12,
            match_browser_navigation_budget=8,
        ),
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.eligible_match_store_keys",
        lambda: ["amazon_br"],
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.split_stores_for_match_waves",
        lambda stores: (list(stores), [], []),
    )
    tracker = MatchProgressTracker()
    tracker.arm(run_id="scrape-queue-deadline", worker_id="test")
    service = ProductMatchService(search_service=search, scrape_service=scrape)

    response = service.match_from_item(
        _ref_item(),
        stores=["amazon_br"],
        persist=False,
        on_store_outcome=outcomes.append,
        run_deadline=MonotonicDeadline.start(timeout_seconds=2700.0),
        progress_tracker=tracker,
        run_id="scrape-queue-deadline",
    )

    assert outcomes[0].error_code == FAILURE_CODE_STORE_WALL_TIMEOUT
    assert response.errors[0].code == FAILURE_CODE_STORE_WALL_TIMEOUT


def test_run_wall_timeout_fails_run_not_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    search = MagicMock()
    search.is_search_supported.return_value = True
    scrape = MagicMock()

    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.get_settings",
        lambda: Settings(
            match_store_wall_timeout_seconds=180.0,
            match_run_wall_timeout_seconds=10.0,
            match_store_concurrency=1,
        ),
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.eligible_match_store_keys",
        lambda: ["amazon_br", "kabum"],
    )
    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.split_stores_for_match_waves",
        lambda stores: (list(stores), [], []),
    )

    real_start = MonotonicDeadline.start

    def start_with_clock(*, timeout_seconds: float, monotonic=None):  # noqa: ANN001
        return real_start(timeout_seconds=timeout_seconds, monotonic=mono)

    monkeypatch.setattr(
        "scout_api.modules.matching.product_match_service.MonotonicDeadline.start",
        start_with_clock,
    )

    tracker = MatchProgressTracker(monotonic=mono)
    tracker.arm(run_id="run-2", worker_id="w1")
    run_deadline = MonotonicDeadline.start(timeout_seconds=10.0, monotonic=mono)
    clock["t"] = 11.0  # already expired before stores

    svc = ProductMatchService(search_service=search, scrape_service=scrape)
    with pytest.raises(RequestError) as excinfo:
        svc.match_from_item(
            _ref_item(),
            stores=["amazon_br"],
            persist=False,
            run_deadline=run_deadline,
            progress_tracker=tracker,
            run_id="run-2",
        )
    assert excinfo.value.code == FAILURE_CODE_RUN_WALL_TIMEOUT
