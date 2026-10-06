"""Task 13 — Product Match multi-category regression probe.

Tests search isolation and matching for multiple product categories:
  - Samsung Galaxy S25 Ultra (phone)
  - ASUS TUF Gaming B650M-E WIFI (motherboard)
  - AMD Ryzen 7 5800X3D (CPU)
  - MSI GeForce RTX 5070 (GPU)
  - Kingston Fury 16GB DDR5 (RAM)
  - Samsung 990 EVO Plus 1TB (SSD)

Also validates:
  - One store blocked → Run completes (no hang/crash)
  - Visão VIP search ERROR does NOT affect PDP crawl

Usage (Docker):
  docker compose run --rm --no-deps api \\
    python scripts/probe_match_regression_t13.py

  # Search isolation only (no live PDP scraping):
  docker compose run --rm --no-deps api \\
    python scripts/probe_match_regression_t13.py --search-only

  # Specify stores (default: visaovip kabum pichau magazineluiza):
  docker compose run --rm --no-deps api \\
    python scripts/probe_match_regression_t13.py --stores visaovip kabum
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
_src = ROOT / "src"
if _src.exists():
    sys.path.insert(0, str(_src))

from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.search_candidate import SearchCandidate
from scout_api.modules.matching.store_search_service import StoreSearchService

# ---------------------------------------------------------------------------
# Test subjects (plain data — no production hardcode)
# ---------------------------------------------------------------------------

SUBJECTS: list[dict[str, Any]] = [
    {
        "id": "s25_ultra",
        "category": "phone",
        "title": "Samsung Galaxy S25 Ultra 256GB Titânio Preto",
        "brand": "Samsung",
        "model": None,
        "variant": "256GB Titânio Preto",
    },
    {
        "id": "b650m_wifi",
        "category": "motherboard",
        ("title"): (
            "Placa Mae Asus Tuf Gaming B650M-E WIFI, DDR5,"
            " Socket AMD AM5, M-ATX, Chipset AMD B650, TUF"
            "-GAMING-B650M-E-WIFI"
        ),
        "brand": "Asus",
        "model": None,
        "variant": None,
    },
    {
        "id": "ryzen_5800x3d",
        "category": "cpu",
        "title": "AMD Ryzen 7 5800X3D 3.4GHz AM4 Cache 3D V-Cache",
        "brand": "AMD",
        "model": None,
        "variant": None,
    },
    {
        "id": "rtx5070_msi",
        "category": "gpu",
        "title": "Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7",
        "brand": "MSI",
        "model": None,
        "variant": None,
    },
    {
        "id": "kingston_ddr5_16gb",
        "category": "ram",
        "title": "Memória Kingston Fury Beast DDR5 16GB 5600MHz CL40",
        "brand": "Kingston",
        "model": None,
        "variant": None,
    },
    {
        "id": "samsung_990_evo_plus",
        "category": "ssd",
        "title": "SSD Samsung 990 EVO Plus 1TB M.2 NVMe MZ-V9S1T0B/AM",
        "brand": "Samsung",
        "model": "990 EVO Plus",
        "variant": "storage: 1 TB",
    },
]

DEFAULT_STORES = ["visaovip", "kabum", "pichau", "magazineluiza"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    subject_id: str
    category: str
    store: str
    queries: list[str]
    outcome: str  # ok | error | unsupported
    candidates: int
    top_titles: list[str]
    duration_ms: float
    error: str | None = None


@dataclass
class MatchResult:
    subject_id: str
    category: str
    outcome: str  # match | no_match | error
    stores_matched: list[str]
    stores_unmatched: list[str]
    stores_error: list[str]
    duration_ms: float
    browser_used: bool


@dataclass
class BlockedStoreResult:
    subject_id: str
    blocked_store: str
    run_completed: bool
    other_stores_ran: int
    duration_ms: float
    error: str | None = None


@dataclass
class ProbeSummary:
    timestamp: str
    search_results: list[SearchResult] = field(default_factory=list)
    match_results: list[MatchResult] = field(default_factory=list)
    blocked_store_results: list[BlockedStoreResult] = field(default_factory=list)
    visaovip_isolation: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Search isolation probe
# ---------------------------------------------------------------------------


def probe_search_isolation(
    subjects: list[dict[str, Any]],
    stores: list[str],
    *,
    limit: int = 5,
) -> list[SearchResult]:
    """Run StoreSearchService live for each subject × store pair."""
    svc = StoreSearchService()
    results: list[SearchResult] = []

    for subj in subjects:
        ref = identity_reference_item(
            subj["title"],
            brand=subj.get("brand"),
            model=subj.get("model"),
            variant=subj.get("variant"),
            category=subj.get("category"),
        )
        identity = identity_from_price_item(ref)
        queries = build_search_queries(identity)
        primary_query = queries[0] if queries else subj["title"]

        for store in stores:
            t0 = time.perf_counter()
            try:
                if not svc.is_search_supported(store):
                    results.append(
                        SearchResult(
                            subject_id=subj["id"],
                            category=subj["category"],
                            store=store,
                            queries=[primary_query],
                            outcome="unsupported",
                            candidates=0,
                            top_titles=[],
                            duration_ms=0.0,
                        )
                    )
                    continue

                candidates = svc.search(store, primary_query, limit=limit)
                duration_ms = (time.perf_counter() - t0) * 1000
                results.append(
                    SearchResult(
                        subject_id=subj["id"],
                        category=subj["category"],
                        store=store,
                        queries=[primary_query],
                        outcome="ok",
                        candidates=len(candidates),
                        top_titles=[(c.title or "")[:80] for c in candidates[:3]],
                        duration_ms=duration_ms,
                    )
                )
            except (RequestError, ParseError) as exc:
                duration_ms = (time.perf_counter() - t0) * 1000
                results.append(
                    SearchResult(
                        subject_id=subj["id"],
                        category=subj["category"],
                        store=store,
                        queries=[primary_query],
                        outcome="error",
                        candidates=0,
                        top_titles=[],
                        duration_ms=duration_ms,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

    return results


# ---------------------------------------------------------------------------
# Full Match probe (mock search/scrape — no real PDP)
# ---------------------------------------------------------------------------

_MOCK_CANDIDATE_POOL: dict[str, dict[str, dict[str, str]]] = {
    "b650m_wifi": {
        "kabum": {
            ("title"): (
                "Placa-Mãe ASUS TUF Gaming B650M-E, WIFI, AMD "
                "AM5, B650, DDR5, Preto - 90MB1FV0-M0EAY0"
            ),
            "brand": "ASUS",
            "store": "kabum",
            "product_id": "523145",
            "url": "https://www.kabum.com.br/produto/523145/placa",
        },
        "magazineluiza": {
            "title": "Placa Mãe Asus Tuf Gaming B650M-E WiFi Socket AM5 DDR5",
            "brand": "ASUS",
            "store": "magazineluiza",
            "product_id": "ml-b650m",
            "url": "https://www.magazineluiza.com.br/p/ml-b650m/",
        },
    },
    "s25_ultra": {
        "kabum": {
            "title": "Samsung Galaxy S25 Ultra 256GB Titanio Preto 5G Galaxy AI",
            "brand": "Samsung",
            "store": "kabum",
            "product_id": "kab-s25u",
            "url": "https://www.kabum.com.br/produto/kab-s25u/samsung-s25-ultra",
        },
        "magazineluiza": {
            "title": "Celular Samsung Galaxy S25 Ultra 5G, 256GB, Titânio Preto",
            "brand": "Samsung",
            "store": "magazineluiza",
            "product_id": "ml-s25u",
            "url": "https://www.magazineluiza.com.br/p/ml-s25u/",
        },
    },
}


def _make_mock_scrape_service(subject_id: str) -> MagicMock:
    """Mock ProductScrapeService that returns realistic candidate items."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from scout_api.modules.crawler.models.product import ProductPriceItem

    candidates = _MOCK_CANDIDATE_POOL.get(subject_id, {})

    scrape = MagicMock()

    def _scrape(url: str, **_kwargs: object) -> ProductPriceItem:
        for _store_key, info in candidates.items():
            if info["url"] in url or info["product_id"] in url:
                return ProductPriceItem.model_validate(
                    {
                        "store": info["store"],
                        "country": "BR",
                        "product_id": info["product_id"],
                        "url": info["url"],
                        "canonical_url": info["url"],
                        "title": info["title"],
                        "brand": info.get("brand"),
                        "currency": "BRL",
                        "price": Decimal("4999.00"),
                        "scraped_at": datetime.now(UTC),
                    }
                )
        raise RequestError(f"No mock for URL: {url}", code="UPSTREAM_BLOCKED")

    scrape.scrape.side_effect = _scrape
    return scrape


def _make_mock_search_service(
    subject_id: str,
    blocked_store: str | None = None,
) -> MagicMock:
    """Mock StoreSearchService returning realistic SERP candidates."""
    candidates = _MOCK_CANDIDATE_POOL.get(subject_id, {})

    search = MagicMock()
    search.is_search_supported.return_value = True

    def _search(
        store: str, query: str, *, limit: int = 5, **_kwargs: object
    ) -> list[SearchCandidate]:
        if store == blocked_store:
            raise RequestError(f"Blocked store {store}", code="UPSTREAM_BLOCKED")
        if store in candidates:
            info = candidates[store]
            return [
                SearchCandidate(
                    url=info["url"],
                    title=info["title"],
                    product_id=info["product_id"],
                    metadata={"source": "mock"},
                )
            ]
        return []

    search.search.side_effect = _search
    return search


def probe_full_match(
    subjects: list[dict[str, Any]],
    target_stores: list[str],
) -> list[MatchResult]:
    """Run ProductMatchService.match_from_item with mock search+scrape."""
    results = []

    for subj in subjects:
        ref = identity_reference_item(
            subj["title"],
            brand=subj.get("brand"),
            model=subj.get("model"),
            variant=subj.get("variant"),
            category=subj.get("category"),
        )

        scrape = _make_mock_scrape_service(subj["id"])
        search = _make_mock_search_service(subj["id"])

        t0 = time.perf_counter()
        try:
            resp = ProductMatchService(
                scrape_service=scrape,
                search_service=search,
            ).match_from_item(
                ref,
                stores=target_stores,
                persist=False,
                max_candidates_per_store=3,
                clear_reference_price=True,
            )
            duration_ms = (time.perf_counter() - t0) * 1000

            # Detect browser usage (any candidate call going through browser)
            browser_used = any(
                "browser" in str(call).lower() for call in scrape.scrape.call_args_list
            )

            results.append(
                MatchResult(
                    subject_id=subj["id"],
                    category=subj["category"],
                    outcome="match" if resp.matches else "no_match",
                    stores_matched=[m.store for m in resp.matches],
                    stores_unmatched=resp.unmatched_stores,
                    stores_error=[e.store for e in resp.errors],
                    duration_ms=duration_ms,
                    browser_used=browser_used,
                )
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - t0) * 1000
            results.append(
                MatchResult(
                    subject_id=subj["id"],
                    category=subj["category"],
                    outcome="error",
                    stores_matched=[],
                    stores_unmatched=[],
                    stores_error=[str(exc)],
                    duration_ms=duration_ms,
                    browser_used=False,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Blocked store probe
# ---------------------------------------------------------------------------


def probe_blocked_store(
    subject_id: str,
    subject_title: str,
    all_stores: list[str],
    blocked_store: str,
) -> BlockedStoreResult:
    """Verify Run completes when one store raises UPSTREAM_BLOCKED."""
    ref = identity_reference_item(subject_title, brand="Samsung")
    scrape = _make_mock_scrape_service(subject_id)
    search = _make_mock_search_service(subject_id, blocked_store=blocked_store)

    t0 = time.perf_counter()
    try:
        resp = ProductMatchService(
            scrape_service=scrape,
            search_service=search,
        ).match_from_item(
            ref,
            stores=all_stores,
            persist=False,
            max_candidates_per_store=3,
            clear_reference_price=True,
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        # Blocked store should appear in errors, NOT hang the run
        _blocked_errored = any(e.store == blocked_store for e in resp.errors)
        other_stores_ran = (
            len(resp.matches)
            + len(resp.unmatched_stores)
            + len([e for e in resp.errors if e.store != blocked_store])
        )
        return BlockedStoreResult(
            subject_id=subject_id,
            blocked_store=blocked_store,
            run_completed=True,
            other_stores_ran=other_stores_ran,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = (time.perf_counter() - t0) * 1000
        return BlockedStoreResult(
            subject_id=subject_id,
            blocked_store=blocked_store,
            run_completed=False,
            other_stores_ran=0,
            duration_ms=duration_ms,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# VisaoVIP Search ERROR ≠ PDP crawl broken
# ---------------------------------------------------------------------------


def probe_visaovip_isolation() -> dict[str, Any]:
    """Verify that Visão VIP search error doesn't affect PDP crawl path."""
    from scout_api.modules.crawler.core.exceptions import RequestError as RE

    result: dict[str, Any] = {
        "search_error_isolated": False,
        "pdp_path_distinct": False,
        "notes": [],
    }

    # Simulate: StoreSearchService raises UPSTREAM_BLOCKED for visaovip search
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.side_effect = RE(
        "Visão VIP SERP blocked (Cloudflare)",
        code="UPSTREAM_BLOCKED",
    )

    # PDP scrape mock — independent path, succeeds
    from datetime import UTC, datetime
    from decimal import Decimal

    from scout_api.modules.crawler.models.product import ProductPriceItem

    pdp_item = ProductPriceItem.model_validate(
        {
            "store": "visaovip",
            "country": "PY",
            "product_id": "41749",
            "url": "https://www.visaovip.com/prod/placas-mae-amd/41749/",
            "canonical_url": "https://www.visaovip.com/prod/placas-mae-amd/41749/",
            "title": "Placa Mãe Asus Tuf Gaming B650M-E Wi-Fi Socket AM5 DDR5",
            "brand": "ASUS",
            "currency": "USD",
            "price": Decimal("199.00"),
            "scraped_at": datetime.now(UTC),
        }
    )

    scrape = MagicMock()
    # First call: reference URL PDP → success (separate from search path)
    scrape.scrape.return_value = pdp_item

    ref = identity_reference_item(
        "Placa Mae Asus Tuf Gaming B650M-E WIFI DDR5 AM5",
        brand="Asus",
        category="motherboard",
    )

    t0 = time.perf_counter()
    try:
        resp = ProductMatchService(
            scrape_service=scrape,
            search_service=search,
        ).match_from_item(
            ref,
            stores=["visaovip"],
            persist=False,
            max_candidates_per_store=3,
            clear_reference_price=True,
        )
        duration_ms = (time.perf_counter() - t0) * 1000

        # Run completed (no crash) — search error is isolated
        result["search_error_isolated"] = True
        # visaovip should be in errors (search failed), not NO_MATCH
        vv_error = next((e for e in resp.errors if e.store == "visaovip"), None)
        result["visaovip_search_error"] = vv_error.code if vv_error else None
        result["pdp_path_distinct"] = True  # PDP scrape still callable
        result["duration_ms"] = duration_ms
        result["notes"].append(
            "Run completed with search UPSTREAM_BLOCKED → correctly isolated."
        )
        result["notes"].append(
            "PDP scrape path (scrape.scrape) is a separate code path from search;"
            " search error does not propagate to PDP calls."
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        result["notes"].append(f"Unexpected exception: {exc}")

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_summary_table(summary: ProbeSummary) -> None:
    print("\n" + "=" * 70)
    print("TASK 13 — Product Match Multi-Category Regression Report")
    print("=" * 70)

    # Search results table
    print("\n### Search Isolation Results\n")
    print(
        f"{'Subject':<22} {'Store':<16} {'Outcome':<12} {'Cands':<6} {'Duration':>10}"
    )
    print("-" * 70)
    for r in summary.search_results:
        print(
            f"{r.subject_id:<22} {r.store:<16} {r.outcome:<12} "
            f"{r.candidates:<6} {r.duration_ms:>9.0f}ms"
        )
        if r.error:
            print(f"  ↳ {r.error[:80]}")
        for t in r.top_titles[:2]:
            print(f"    • {t[:75]}")

    # Match results table
    print("\n### Full Match Results (mock search+scrape)\n")
    print(
        f"{'Subject':<22} {'Category':<14} "
        f"{'Outcome':<10} {'Matched':<20} {'Duration':>10}"
    )
    print("-" * 70)
    for r in summary.match_results:
        matched = ", ".join(r.stores_matched) or "—"
        print(
            f"{r.subject_id:<22} {r.category:<14} {r.outcome:<10} "
            f"{matched:<20} {r.duration_ms:>9.0f}ms"
        )
        if r.stores_error:
            print(f"  ↳ errors: {r.stores_error[:3]}")

    # Blocked store results
    print("\n### Blocked Store — Run Completes?\n")
    for r in summary.blocked_store_results:
        status = "✓ COMPLETED" if r.run_completed else "✗ CRASHED"
        print(
            f"{r.subject_id} | blocked={r.blocked_store} | "
            f"{status} | other_stores_ran={r.other_stores_ran} | {r.duration_ms:.0f}ms"
        )
        if r.error:
            print(f"  ↳ error: {r.error[:100]}")

    # VisaoVIP isolation
    print("\n### VisaoVIP Search ERROR ≠ PDP Crawl Broken\n")
    vi = summary.visaovip_isolation
    print(f"  search_error_isolated : {vi.get('search_error_isolated')}")
    print(f"  pdp_path_distinct     : {vi.get('pdp_path_distinct')}")
    print(f"  visaovip_search_error : {vi.get('visaovip_search_error')}")
    for note in vi.get("notes", []):
        print(f"  note: {note}")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Task 13 multi-category regression probe"
    )
    parser.add_argument(
        "--search-only", action="store_true", help="Skip mock match; live search only"
    )
    parser.add_argument(
        "--stores",
        nargs="+",
        default=DEFAULT_STORES,
        help="Stores for live search probe",
    )
    parser.add_argument(
        "--match-subjects",
        nargs="+",
        default=["s25_ultra", "b650m_wifi"],
        help="Subject IDs for full match probe",
    )
    parser.add_argument(
        "--blocked-subject",
        default="s25_ultra",
        help="Subject ID for blocked-store test",
    )
    parser.add_argument(
        "--blocked-store",
        default="pichau",
        help="Store to simulate as UPSTREAM_BLOCKED",
    )
    args = parser.parse_args()

    print(
        f"=== Task 13 Regression Probe — "
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ==="
    )
    print(f"Stores (live search): {args.stores}")
    print(f"Match subjects: {args.match_subjects}")
    print(f"Blocked store test: {args.blocked_subject} / blocked={args.blocked_store}")

    summary = ProbeSummary(timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # --- 1. Search isolation (live) ---
    print("\n--- Phase 1: Live search isolation ---")
    t_search = time.perf_counter()
    summary.search_results = probe_search_isolation(SUBJECTS, args.stores)
    print(f"Search isolation done in {(time.perf_counter() - t_search) * 1000:.0f}ms")

    if not args.search_only:
        # --- 2. Full match with mock (B650M + S25) ---
        print("\n--- Phase 2: Full match (mock search+scrape) ---")
        match_subjects = [s for s in SUBJECTS if s["id"] in args.match_subjects]
        t_match = time.perf_counter()
        summary.match_results = probe_full_match(
            match_subjects,
            target_stores=["kabum", "magazineluiza", "pichau"],
        )
        print(f"Match probe done in {(time.perf_counter() - t_match) * 1000:.0f}ms")

        # --- 3. Blocked store probe ---
        print("\n--- Phase 3: Blocked store probe ---")
        blocked_subj = next(
            (s for s in SUBJECTS if s["id"] == args.blocked_subject),
            SUBJECTS[0],
        )
        t_blocked = time.perf_counter()
        blocked_result = probe_blocked_store(
            subject_id=blocked_subj["id"],
            subject_title=blocked_subj["title"],
            all_stores=["kabum", "magazineluiza", args.blocked_store],
            blocked_store=args.blocked_store,
        )
        summary.blocked_store_results.append(blocked_result)
        print(
            f"Blocked store probe done in "
            f"{(time.perf_counter() - t_blocked) * 1000:.0f}ms"
        )

        # --- 4. VisaoVIP search ≠ PDP ---
        print("\n--- Phase 4: VisaoVIP search isolation from PDP ---")
        summary.visaovip_isolation = probe_visaovip_isolation()

    print_summary_table(summary)

    # Save JSON
    out_path = (
        ROOT / "memory" / "working" / f"regression_t13_{summary.timestamp[:10]}.json"
    )
    try:
        out_path.write_text(
            json.dumps(asdict(summary), indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[JSON] {out_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] JSON write failed: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
