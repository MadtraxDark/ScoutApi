"""Chaos / integration / soak tests — Phase 12: browser+match reliability.

Chaos checklist:
  [CHAOS-1]  browser process killed (poison=True) — slot returned, no false NO_MATCH
  [CHAOS-2]  launch timeout → BROWSER_* ERROR not NO_MATCH (ref existing test)
  [CHAOS-3]  owner-thread failure → trial TTL reap (ref existing test)
  [CHAOS-4]  profile lock held by peer (ref existing test)
  [CHAOS-5]  queue full → BROWSER_QUEUE_SATURATED not NO_MATCH
  [CHAOS-6]  store WAF block → store circuit / ERROR (ref existing test)
  [CHAOS-7]  operation nav timeout → SINGLE_OPERATION scope, no infra circuit trip
  [CHAOS-8]  Server Action invalid contract → fallback B (ref existing test)
  [SOAK-1]   capacity=1 serial soak, 20 iterations — consistent state, no hangs
  [SOAK-2]   concurrent queue pressure, recycle/poison mix — no deadlock
  [COV-1]    coverage gate — all eligible stores appear in on_store_outcome callback
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from scout_api.modules.crawler.core.browser_health import (
    BROWSER_INFRASTRUCTURE_ERROR_CODES,
)
from scout_api.modules.crawler.core.browser_scheduler import BrowserScheduler
from scout_api.modules.crawler.core.exceptions import (
    BROWSER_INFRASTRUCTURE_ERROR_CODES as EXC_BROWSER_INFRA_CODES,
)
from scout_api.modules.crawler.core.exceptions import (
    RequestError,
)
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.eligibility import eligible_match_store_keys
from scout_api.modules.matching.product_match_service import (
    MatchStoreOutcome,
    ProductMatchService,
)
from scout_api.modules.matching.schemas import MatchRequest
from scout_api.modules.matching.search_candidate import SearchCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sched(
    capacity: int = 1, queue_capacity: int = 8, timeout_ms: int = 5_000
) -> BrowserScheduler:
    return BrowserScheduler(
        capacity=capacity,
        queue_capacity=queue_capacity,
        queue_timeout_ms=timeout_ms,
    )


def _item(**overrides: object) -> ProductPriceItem:
    base: dict[str, object] = {
        "store": "mercadolivre",
        "country": "BR",
        "product_id": "MLB1",
        "url": "https://www.mercadolivre.com.br/p/MLB1",
        "canonical_url": "https://www.mercadolivre.com.br/p/MLB1",
        "title": "Samsung Galaxy S25 Ultra 256GB Titânio Preto",
        "brand": "Samsung",
        "model": "Galaxy S25 Ultra",
        "currency": "BRL",
        "price": Decimal("7999.00"),
        "scraped_at": datetime(2026, 9, 23, tzinfo=UTC),
    }
    base.update(overrides)
    return ProductPriceItem.model_validate(base)


# ---------------------------------------------------------------------------
# [CHAOS-1] browser process killed — release(poison=True) returns slot to pool
# ---------------------------------------------------------------------------


def test_poison_release_returns_slot_to_pool() -> None:
    """release(poison=True) must return the slot to the pool — not lose it."""
    sched = _sched()
    lease = sched.acquire()
    assert sched.snapshot()["active"] == 1

    sched.release(lease, poison=True)

    snap = sched.snapshot()
    assert snap["active"] == 0, "Slot must return to pool after poison release"
    assert snap["browser_active_jobs"] == 0

    # Slot is usable again — no false NO_MATCH from infrastructure leak.
    lease2 = sched.acquire()
    sched.release(lease2)
    assert sched.snapshot()["active"] == 0


def test_poison_release_wakes_waiter() -> None:
    """After poison release, queued waiter must receive the slot."""
    sched = _sched()
    lease1 = sched.acquire()

    acquired = threading.Event()

    def worker() -> None:
        lease = sched.acquire()
        acquired.set()
        sched.release(lease)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(0.1)
    assert not acquired.is_set()

    sched.release(lease1, poison=True)
    assert acquired.wait(timeout=3.0), "Waiter must receive slot after poison release"
    t.join(timeout=3.0)


# ---------------------------------------------------------------------------
# [CHAOS-1] browser infrastructure error → Match ERROR, not NO_MATCH
# ---------------------------------------------------------------------------


def test_browser_infra_error_from_search_is_match_error_not_no_match() -> None:
    """BROWSER_QUEUE_SATURATED from search → Match ERROR (not silent NO_MATCH)."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.side_effect = RequestError(
        "fila saturada",
        code="BROWSER_QUEUE_SATURATED",
        retryable=False,
    )

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    assert resp.matches == []
    error_codes = {e.code for e in resp.errors}
    # Infra error MUST appear in errors — the caller can distinguish error from genuine
    # NO_MATCH
    assert "BROWSER_QUEUE_SATURATED" in error_codes, (
        f"Esperava BROWSER_QUEUE_SATURATED em errors, obteve: {error_codes}"
    )
    # Store may appear in unmatched_stores (no match found) AND in errors (reason why).
    # The important invariant is that errors is populated — not silent NO_MATCH.
    kabum_errors = [e for e in resp.errors if e.store == "kabum"]
    assert kabum_errors, "Kabum deve ter um MatchStoreError (não NO_MATCH silencioso)"


def test_browser_queue_timeout_from_search_is_match_error_not_no_match() -> None:
    """BROWSER_QUEUE_TIMEOUT from search → Match ERROR (not silent NO_MATCH)."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.side_effect = RequestError(
        "timeout na fila",
        code="BROWSER_QUEUE_TIMEOUT",
        retryable=True,
    )

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    assert resp.matches == []
    error_codes = {e.code for e in resp.errors}
    assert "BROWSER_QUEUE_TIMEOUT" in error_codes
    # Error is populated — not silent NO_MATCH
    kabum_errors = [e for e in resp.errors if e.store == "kabum"]
    assert kabum_errors, "Kabum deve ter um MatchStoreError (não NO_MATCH silencioso)"


# ---------------------------------------------------------------------------
# [CHAOS-7] nav timeout → SINGLE_OPERATION scope, no infra circuit trip
# ---------------------------------------------------------------------------


def test_nav_timeout_code_not_in_browser_infrastructure_error_codes() -> None:
    """A per-operation navigation error must NOT be treated as infra circuit trip.

    BROWSER_LAUNCH_ERROR, BROWSER_INFRASTRUCTURE_UNAVAILABLE trip the circuit;
    REQUEST_ERROR (general) and UPSTREAM_BLOCKED (WAF) do not.
    Page navigation timeouts are store-specific, not launcher failures.
    """
    # Infra-circuit codes — all must be present
    assert "BROWSER_LAUNCH_ERROR" in BROWSER_INFRASTRUCTURE_ERROR_CODES
    assert "BROWSER_INFRASTRUCTURE_UNAVAILABLE" in BROWSER_INFRASTRUCTURE_ERROR_CODES
    assert "BROWSER_QUEUE_SATURATED" in BROWSER_INFRASTRUCTURE_ERROR_CODES
    assert "BROWSER_QUEUE_TIMEOUT" in BROWSER_INFRASTRUCTURE_ERROR_CODES

    # Non-infra codes — navigation timeouts must NOT trip the global circuit
    nav_timeout_codes = [
        "UPSTREAM_BLOCKED",
        "REQUEST_ERROR",
        "PARSE_ERROR",
        "RATE_LIMITED",
        # A hypothetical per-operation timeout code must also be excluded
        "BROWSER_NAVIGATION_TIMEOUT",
    ]
    for code in nav_timeout_codes:
        assert code not in BROWSER_INFRASTRUCTURE_ERROR_CODES, (
            f"Código de nav/operação {code!r} não deve estar em "
            "BROWSER_INFRASTRUCTURE_ERROR_CODES (trip do circuit global)"
        )


def test_nav_timeout_does_not_stop_query_loop() -> None:
    """A non-infra error (e.g. REQUEST_ERROR) continues the query loop."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    call_count = 0

    def _search(store_key: str, query: str, **_kwargs: object) -> list[SearchCandidate]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First query: transient, non-infra error (nav timeout equivalent)
            raise RequestError(
                "page load timeout", code="REQUEST_ERROR", retryable=True
            )
        return []

    search.search.side_effect = _search

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    # Query loop continued after non-infra error → at least 2 query attempts
    assert call_count >= 2, (
        f"Nav timeout (REQUEST_ERROR) deve continuar o loop de queries, "
        f"mas só houve {call_count} tentativa(s)"
    )
    # No match is expected (all queries failed/empty) — kabum is unmatched, not errored
    # (last_error is REQUEST_ERROR, but loop continued through other queries)
    # Either unmatched or error is acceptable; what's forbidden is fabricated match
    assert resp.matches == []


def test_infra_error_stops_query_loop_immediately() -> None:
    """BROWSER_LAUNCH_ERROR stops the query loop after 1 attempt (no repeat launch)."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    call_count = 0

    def _search(store_key: str, query: str, **_kwargs: object) -> list[SearchCandidate]:
        nonlocal call_count
        call_count += 1
        raise RequestError(
            "launch failed", code="BROWSER_LAUNCH_ERROR", retryable=False
        )

    search.search.side_effect = _search

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            stores=["kabum"],
            persist=False,
        )
    )
    assert call_count == 1, (
        f"BROWSER_LAUNCH_ERROR deve parar o loop após 1 tentativa, "
        f"mas houve {call_count}"
    )
    assert any(e.code == "BROWSER_LAUNCH_ERROR" for e in resp.errors)
    assert resp.matches == []


# ---------------------------------------------------------------------------
# Infra codes consistency: browser_health ↔ exceptions module
# ---------------------------------------------------------------------------


def test_browser_infra_codes_consistent_between_modules() -> None:
    """BROWSER_INFRASTRUCTURE_ERROR_CODES must be identical in both modules."""
    assert BROWSER_INFRASTRUCTURE_ERROR_CODES == EXC_BROWSER_INFRA_CODES, (
        "browser_health.BROWSER_INFRASTRUCTURE_ERROR_CODES e "
        "exceptions.BROWSER_INFRASTRUCTURE_ERROR_CODES devem ser idênticos"
    )


# ---------------------------------------------------------------------------
# [SOAK-1] capacity=1 serial soak — 20 iterations, consistent state
# ---------------------------------------------------------------------------


def test_soak_serial_acquire_release_capacity1() -> None:
    """20 serial acquire/release cycles (mix of normal and poison) — no hangs,
    no slot leak, snapshot always consistent."""
    sched = _sched(capacity=1, queue_capacity=4)

    for i in range(20):
        lease = sched.acquire()
        snap = sched.snapshot()
        assert snap["active"] == 1
        assert snap["browser_active_jobs"] == 1

        # Every 3rd iteration: poison release (simulate browser recycle)
        poison = i % 3 == 0
        sched.release(lease, poison=poison)

        snap2 = sched.snapshot()
        assert snap2["active"] == 0
        assert snap2["depth"] == 0

    # Final state must be fully idle
    final = sched.snapshot()
    assert final["active"] == 0
    assert final["depth"] == 0
    assert final["total_acquisitions"] == 20


# ---------------------------------------------------------------------------
# [SOAK-2] concurrent queue pressure with poison — no deadlock, no slot loss
# ---------------------------------------------------------------------------


def test_soak_concurrent_queue_pressure_with_poison() -> None:
    """8 concurrent workers, capacity=1, queue=4 — no deadlock within 10 s.

    Some workers intentionally release with poison=True. Verifies that the
    scheduler never loses a slot (pool stays consistent after all workers finish).
    """
    sched = _sched(capacity=1, queue_capacity=4, timeout_ms=5_000)
    results: list[str] = []
    lock = threading.Lock()

    def worker(wid: int) -> None:
        try:
            lease = sched.acquire()
            time.sleep(0.02)  # hold slot briefly
            poison = wid % 4 == 0
            sched.release(lease, poison=poison)
            with lock:
                results.append("ok")
        except RequestError as exc:
            with lock:
                results.append(exc.code)

    threads = [
        threading.Thread(target=worker, args=(i,), daemon=True) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    # All workers must have completed (no deadlock)
    assert len(results) == 8, f"Nem todos os workers terminaram: {results}"

    # No slots lost — pool must be fully idle
    snap = sched.snapshot()
    assert snap["active"] == 0, f"Slot vazado após soak: {snap}"

    # At least some workers succeeded; others may have got BROWSER_QUEUE_SATURATED
    ok_count = results.count("ok")
    assert ok_count >= 1, "Pelo menos 1 worker deve ter obtido o slot"


# ---------------------------------------------------------------------------
# [COV-1] Coverage gate — all eligible stores appear in on_store_outcome
# ---------------------------------------------------------------------------


def test_coverage_gate_all_eligible_stores_reported_in_outcome() -> None:
    """No eligible store may be silently dropped from the Match result.

    Coverage: eligible_stores == {match} ∪ {no_match} ∪ {error}.
    Ensures optimizations (skip/filter) don't silently drop stores.
    """
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []  # all stores: no candidates → no_match

    outcomes: list[MatchStoreOutcome] = []

    ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            persist=False,
        ),
        on_store_outcome=outcomes.append,
    )

    eligible = set(eligible_match_store_keys()) - {"mercadolivre"}
    attempted = {o.store for o in outcomes}

    missing = eligible - attempted
    assert not missing, (
        f"Lojas elegíveis não reportadas no on_store_outcome (cobertura silenciosa "
        f"removida?): {missing}"
    )

    # All stores must have a terminal status
    for outcome in outcomes:
        assert outcome.status in {"match", "no_match", "error"}, (
            f"Store {outcome.store!r} tem status inesperado: {outcome.status!r}"
        )


def test_coverage_gate_skip_stores_are_excluded_but_others_reported() -> None:
    """skip_stores removes specific stores; remaining eligible must all be reported."""
    scrape = MagicMock()
    scrape.scrape.return_value = _item()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    outcomes: list[MatchStoreOutcome] = []
    skip = {"kabum", "pichau"}

    ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.mercadolivre.com.br/p/MLB1",
            persist=False,
        ),
        on_store_outcome=outcomes.append,
        skip_stores=skip,
    )

    attempted = {o.store for o in outcomes}

    # Skipped stores must not appear
    for s in skip:
        assert s not in attempted, f"{s!r} foi skip_stores mas apareceu no outcome"

    # Remaining eligible must all appear
    eligible = set(eligible_match_store_keys()) - {"mercadolivre"} - skip
    missing = eligible - attempted
    assert not missing, f"Lojas elegíveis (excluindo skip) não reportadas: {missing}"
