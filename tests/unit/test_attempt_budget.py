"""Unit tests for StoreAttemptBudget — Phase 3 retry/attempt budgets.

Covers: progressive stop; cache hit no external consumed; strategy A+B = 1
query; exhaustion sets stopped_reason; first reason preserved; budget wiring.
"""

from __future__ import annotations

from scout_api.modules.matching.attempt_budget import StoreAttemptBudget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_budget(
    *,
    queries: int = 5,
    external: int = 12,
    browser: int = 8,
) -> StoreAttemptBudget:
    return StoreAttemptBudget(
        queries_budget=queries,
        external_attempt_budget=external,
        browser_navigation_budget=browser,
    )


# ---------------------------------------------------------------------------
# begin_query — query budget gate
# ---------------------------------------------------------------------------


class TestBeginQuery:
    def test_allows_exactly_budget_queries(self) -> None:
        b = make_budget(queries=3)
        assert b.begin_query() is True
        assert b.begin_query() is True
        assert b.begin_query() is True
        # 4th call exceeds budget
        assert b.begin_query() is False

    def test_progressive_stop_sets_reason(self) -> None:
        b = make_budget(queries=1)
        b.begin_query()  # uses the only slot
        result = b.begin_query()
        assert result is False
        assert b.stopped_reason == "query_budget"

    def test_zero_budget_stops_immediately(self) -> None:
        b = make_budget(queries=0)
        assert b.begin_query() is False
        assert b.stopped_reason == "query_budget"

    def test_queries_used_increments_on_success(self) -> None:
        b = make_budget(queries=5)
        b.begin_query()
        b.begin_query()
        assert b.queries_used == 2

    def test_queries_used_does_not_increment_on_exhaustion(self) -> None:
        b = make_budget(queries=2)
        b.begin_query()
        b.begin_query()
        b.begin_query()  # False; should not increment
        assert b.queries_used == 2


# ---------------------------------------------------------------------------
# record_external — external attempt budget gate
# ---------------------------------------------------------------------------


class TestRecordExternal:
    def test_allows_up_to_budget(self) -> None:
        b = make_budget(external=2)
        assert b.record_external() is True
        assert b.record_external() is True
        assert b.record_external() is False

    def test_exhaustion_sets_reason(self) -> None:
        b = make_budget(external=1)
        b.record_external()
        b.record_external()
        assert b.stopped_reason == "external_attempt_budget"

    def test_external_attempts_increments(self) -> None:
        b = make_budget(external=10)
        b.record_external()
        b.record_external()
        assert b.external_attempts == 2

    def test_zero_external_budget(self) -> None:
        b = make_budget(external=0)
        assert b.record_external() is False
        assert b.stopped_reason == "external_attempt_budget"


# ---------------------------------------------------------------------------
# record_browser_nav — browser navigation budget gate
# ---------------------------------------------------------------------------


class TestRecordBrowserNav:
    def test_allows_up_to_budget(self) -> None:
        b = make_budget(browser=2)
        assert b.record_browser_nav() is True
        assert b.record_browser_nav() is True
        assert b.record_browser_nav() is False

    def test_exhaustion_sets_reason(self) -> None:
        b = make_budget(browser=1)
        b.record_browser_nav()
        b.record_browser_nav()
        assert b.stopped_reason == "browser_nav_budget"

    def test_browser_navigations_increments(self) -> None:
        b = make_budget(browser=5)
        b.record_browser_nav()
        b.record_browser_nav()
        assert b.browser_navigations == 2


# ---------------------------------------------------------------------------
# skip_cached — cache/dedup hits consume nothing
# ---------------------------------------------------------------------------


class TestSkipCached:
    def test_cache_hit_does_not_consume_external(self) -> None:
        b = make_budget(queries=5, external=1, browser=1)
        for _ in range(5):
            b.skip_cached()
        assert b.external_attempts == 0
        assert b.browser_navigations == 0
        assert b.stopped_reason is None

    def test_cache_hit_does_not_affect_queries_used(self) -> None:
        b = make_budget(queries=3)
        b.skip_cached()
        b.skip_cached()
        # Queries used should still be 0 (no begin_query called)
        assert b.queries_used == 0


# ---------------------------------------------------------------------------
# A+B strategy — 1 begin_query, 2 record_external calls
# ---------------------------------------------------------------------------


class TestStrategyABOnOneQuery:
    def test_ab_counts_as_one_query_two_externals(self) -> None:
        """Strategy A+B under one query counts as 1 query; 2 external attempts."""
        b = make_budget(queries=1, external=2, browser=2)
        # Single begin_query for A+B
        assert b.begin_query() is True  # 1 query used
        assert b.record_external() is True  # Strategy A external
        assert b.record_external() is True  # Strategy B external
        # No more queries available
        assert b.begin_query() is False
        assert b.queries_used == 1
        assert b.external_attempts == 2

    def test_ab_with_browser_nav_on_each(self) -> None:
        b = make_budget(queries=1, external=4, browser=2)
        b.begin_query()
        b.record_external()
        b.record_browser_nav()  # A navigated
        b.record_external()
        b.record_browser_nav()  # B navigated
        assert b.queries_used == 1
        assert b.external_attempts == 2
        assert b.browser_navigations == 2


# ---------------------------------------------------------------------------
# stopped_reason preservation — first reason wins
# ---------------------------------------------------------------------------


class TestStoppedReasonPreservation:
    def test_first_reason_not_overwritten(self) -> None:
        """Once stopped_reason is set, subsequent exhaustions do not change it."""
        b = make_budget(queries=1, external=0, browser=0)
        b.begin_query()  # uses query slot
        b.begin_query()  # triggers query_budget
        b.record_external()  # would trigger external_attempt_budget — ignored
        b.record_browser_nav()  # would trigger browser_nav_budget — ignored
        assert b.stopped_reason == "query_budget"

    def test_external_reason_preserved_over_browser_nav(self) -> None:
        b = make_budget(queries=10, external=1, browser=0)
        b.record_external()  # uses slot
        b.record_external()  # triggers external_attempt_budget
        b.record_browser_nav()  # would trigger browser_nav_budget — ignored
        assert b.stopped_reason == "external_attempt_budget"


# ---------------------------------------------------------------------------
# Settings integration — defaults surfaced via config
# ---------------------------------------------------------------------------


def test_settings_have_correct_budget_defaults() -> None:
    """Config defaults match spec: query=5, external=12, browser=8."""
    from scout_api.core.config import Settings

    s = Settings()
    assert s.match_search_query_budget == 5
    assert s.match_external_attempt_budget == 12
    assert s.match_browser_navigation_budget == 8


def test_budget_from_settings() -> None:
    """StoreAttemptBudget constructed from settings has expected defaults."""
    from scout_api.core.config import Settings

    s = Settings()
    b = StoreAttemptBudget(
        queries_budget=s.match_search_query_budget,
        external_attempt_budget=s.match_external_attempt_budget,
        browser_navigation_budget=s.match_browser_navigation_budget,
    )
    assert b.queries_budget == 5
    assert b.external_attempt_budget == 12
    assert b.browser_navigation_budget == 8
    assert b.queries_used == 0
    assert b.external_attempts == 0
    assert b.browser_navigations == 0
    assert b.stopped_reason is None
