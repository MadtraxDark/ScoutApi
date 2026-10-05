"""Per-store attempt budget tracking for Product Match progressive search.

Semantics (approved spec §2 — Budget):

- ``queries_budget``: MAX queries per store per MatchRun — progressive stop,
  not a mandatory count.  ``begin_query()`` returns False once the ceiling is
  reached; the caller must stop issuing further queries.
- ``external_attempt_budget``: upstream requests that actually left the
  process (non-cached SERP fetches + candidate PDP fetches).
  Cache/dedup hits do NOT consume this budget (use ``skip_cached()``).
- ``browser_navigation_budget``: real Camoufox navigations.
- Strategy A+B in the same query counts as ONE ``begin_query()`` call; each
  strategy that issues a network request counts as a separate
  ``record_external()`` call.
- ``stopped_reason``: first exhaustion reason is preserved (never overwritten).

Observability fields logged per-store at the end of a Match run::

    queries_used / queries_budget / external_attempts / external_attempt_budget /
    browser_navigations / browser_navigation_budget / stopped_reason
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StoreAttemptBudget:
    """Track and enforce attempt budgets for one store within a Match run."""

    queries_budget: int
    external_attempt_budget: int
    browser_navigation_budget: int
    queries_used: int = 0
    external_attempts: int = 0
    browser_navigations: int = 0
    stopped_reason: str | None = None

    # ------------------------------------------------------------------
    # Query gate — call once per *distinct* (non-deduped) query.
    # ------------------------------------------------------------------

    def begin_query(self) -> bool:
        """Try to start a new progressive query.

        Returns False when the query budget has been reached; the caller must
        stop issuing further queries.  Increments ``queries_used`` on success.
        The first exhaustion reason is captured in ``stopped_reason``.
        """
        if self.queries_used >= self.queries_budget:
            if self.stopped_reason is None:
                self.stopped_reason = "query_budget"
            return False
        self.queries_used += 1
        return True

    # ------------------------------------------------------------------
    # External request gate — call before each non-cached upstream fetch.
    # ------------------------------------------------------------------

    def record_external(self) -> bool:
        """Record one upstream (external) request attempt.

        Returns False when the external-attempt budget is exhausted; the
        caller should skip the request.  Increments ``external_attempts`` on
        success.
        """
        if self.external_attempts >= self.external_attempt_budget:
            if self.stopped_reason is None:
                self.stopped_reason = "external_attempt_budget"
            return False
        self.external_attempts += 1
        return True

    # ------------------------------------------------------------------
    # Browser navigation gate — call when Camoufox performs a navigation.
    # ------------------------------------------------------------------

    def record_browser_nav(self) -> bool:
        """Record one real Camoufox browser navigation.

        Returns False when the browser-navigation budget is exhausted.
        Increments ``browser_navigations`` on success.

        Note: at SERP level this is called via the ``on_browser_nav_used``
        hook in ``StoreSearchService.search()``.  Candidate-level browser
        nav tracking requires deeper wiring into ``ProductScrapeService``
        (out of scope for this task).
        """
        if self.browser_navigations >= self.browser_navigation_budget:
            if self.stopped_reason is None:
                self.stopped_reason = "browser_nav_budget"
            return False
        self.browser_navigations += 1
        return True

    # ------------------------------------------------------------------
    # Cache/dedup — explicit no-op to document intent at call sites.
    # ------------------------------------------------------------------

    def skip_cached(self) -> None:
        """Signal a cache or dedup hit — no external or browser nav consumed."""
