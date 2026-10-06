"""Live Product Match performance benchmark (cold + warm).

Runs identity-only match for a small multi-category set, prints timing tables,
and records Camoufox launch/reuse counters from the shared fetcher.

Usage (Docker, preferred)::

  docker compose stop api
  # clear stale profile locks if needed
  docker compose run --rm --no-deps api python scripts/_bench_match_perf.py

Do not run while another Camoufox process holds the same user_data_dir.

Env (measurement defaults applied in ``main`` if unset)::

  BENCH_STORES=kabum,nissei,...
  MATCH_STORE_CONCURRENCY=3
  SCRAPE_DISTRIBUTED_COOLDOWN_ENABLED=false
  SCRAPE_URL_COOLDOWN_SECONDS=0
  SCRAPE_DOMAIN_MIN_INTERVAL_SECONDS=0
  BENCH_SKIP_CROSS=1   # optional: cold+warm only
"""

from __future__ import annotations

# Scripts insert ``src`` on sys.path before importing the package.
# ruff: noqa: E402
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.html_fetcher import CamoufoxHtmlFetcher
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.services.store_aware_fetcher import StoreAwareHtmlFetcher
from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.identity import identity_reference_item
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.store_search_order import order_stores_for_match
from scout_api.modules.matching.store_search_service import StoreSearchService

# Benchmark products only — never hard-coded into matcher fallbacks.
BENCHMARKS: tuple[dict[str, str], ...] = (
    {
        "id": "gpu_gigabyte_rtx5060",
        "title": "Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB",
        "category": "gpu",
    },
    {
        "id": "console_ps5",
        "title": "Console Sony PlayStation 5 Slim Digital CFI-2015",
        "category": "console",
    },
)


def _apply_bench_env_defaults() -> None:
    """Force-disable scrape cooldowns that dominate wall time in multi-candidate match.

    Compose/``.env`` often already set these — ``setdefault`` would leave them on.
    This script is measurement-only; production API keeps normal spacing.
    """
    os.environ["SCRAPE_DISTRIBUTED_COOLDOWN_ENABLED"] = "false"
    os.environ["SCRAPE_URL_COOLDOWN_SECONDS"] = "0"
    os.environ["SCRAPE_DOMAIN_MIN_INTERVAL_SECONDS"] = "0"
    get_settings.cache_clear()


# Optional comma-separated store allowlist via BENCH_STORES env (faster isolation).
def _bench_stores() -> list[str]:
    raw = (os.environ.get("BENCH_STORES") or "").strip()
    implemented = [k for k, c in STORE_CONFIGS.items() if c.implemented]
    if raw:
        wanted = {s.strip().lower() for s in raw.split(",") if s.strip()}
        selected = [s for s in implemented if s in wanted]
    else:
        selected = implemented
    return order_stores_for_match(
        selected,
        reference_store=None,
        reference_has_gtin=False,
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _camoufox_counters(fetcher: Any) -> dict[str, int]:
    launches = 0
    reuses = 0

    def add(obj: Any) -> None:
        nonlocal launches, reuses
        if isinstance(obj, CamoufoxHtmlFetcher):
            launches += int(obj.browser_launch_count)
            reuses += int(obj.browser_reuse_count)

    add(fetcher)
    browser = getattr(fetcher, "browser", None)
    add(browser)
    if isinstance(browser, StoreAwareHtmlFetcher) or hasattr(fetcher, "direct"):
        target = browser if browser is not None else fetcher
        add(getattr(target, "direct", None))
        add(getattr(target, "proxied", None))
    # Unwrap progressive HTTP-first layers.
    cur = fetcher
    for _ in range(6):
        nxt = getattr(cur, "browser", None) or getattr(cur, "_browser", None)
        if nxt is None or nxt is cur:
            break
        add(nxt)
        if isinstance(nxt, StoreAwareHtmlFetcher):
            add(nxt.direct)
            add(nxt.proxied)
        cur = nxt
    return {"browser_launches": launches, "browser_reuses": reuses}


def _run_one(
    *,
    label: str,
    title: str,
    category: str,
    stores: list[str],
    max_candidates: int,
) -> dict[str, Any]:
    reference = identity_reference_item(title, category=category)
    search = StoreSearchService()
    scrape = ProductScrapeService()
    matcher = ProductMatchService(scrape_service=scrape, search_service=search)

    t0 = time.perf_counter()
    response = matcher.match_from_item(
        reference,
        stores=stores,
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=max_candidates,
        clear_reference_price=True,
    )
    elapsed = time.perf_counter() - t0
    counters = _camoufox_counters(get_shared_html_fetcher())

    matches = [
        {
            "store": m.store,
            "decision": m.decision,
            "confidence": str(m.confidence),
            "title": (m.product.title or "")[:100],
            "url": m.product.url,
        }
        for m in response.matches
    ]
    errors = [
        {"store": e.store, "code": e.code, "message": e.message[:160]}
        for e in response.errors
    ]
    return {
        "label": label,
        "title": title,
        "category": category,
        "elapsed_s": round(elapsed, 1),
        "match_count": len(matches),
        "unmatched": list(response.unmatched_stores),
        "error_count": len(errors),
        "matches": matches,
        "errors": errors,
        **counters,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    # Keep logs readable: suppress noisy DEBUG from libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _apply_bench_env_defaults()
    settings = get_settings()
    print(
        "BENCH_ENV "
        f"domain_min_interval={settings.scrape_domain_min_interval_seconds} "
        f"url_cooldown={settings.scrape_url_cooldown_seconds} "
        f"distributed_cooldown={settings.scrape_distributed_cooldown_enabled} "
        f"match_concurrency={settings.match_store_concurrency}",
        flush=True,
    )

    out_dir = ROOT / "data" / "live-match-reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"match_perf_bench_{stamp}.json"

    stores = _bench_stores()
    print("BENCH_STORES", stores, flush=True)
    skip_cross = os.environ.get("BENCH_SKIP_CROSS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    results: list[dict[str, Any]] = []

    # Cold: first product (forces Camoufox launches if needed).
    first = BENCHMARKS[0]
    print(f"\n=== COLD {first['id']} ===", flush=True)
    cold = _run_one(
        label=f"cold:{first['id']}",
        title=first["title"],
        category=first["category"],
        stores=stores,
        max_candidates=3,
    )
    results.append(cold)
    print(
        f"elapsed_s={cold['elapsed_s']} matches={cold['match_count']} "
        f"errors={cold['error_count']} launches={cold['browser_launches']} "
        f"reuses={cold['browser_reuses']}",
        flush=True,
    )

    # Warm: same product again — expect higher reuses, fewer launches delta.
    print(f"\n=== WARM {first['id']} ===", flush=True)
    before = _camoufox_counters(get_shared_html_fetcher())
    warm = _run_one(
        label=f"warm:{first['id']}",
        title=first["title"],
        category=first["category"],
        stores=stores,
        max_candidates=3,
    )
    after = _camoufox_counters(get_shared_html_fetcher())
    warm["launch_delta"] = after["browser_launches"] - before["browser_launches"]
    warm["reuse_delta"] = after["browser_reuses"] - before["browser_reuses"]
    results.append(warm)
    print(
        f"elapsed_s={warm['elapsed_s']} matches={warm['match_count']} "
        f"errors={warm['error_count']} launch_delta={warm['launch_delta']} "
        f"reuse_delta={warm['reuse_delta']}",
        flush=True,
    )

    cross: dict[str, Any] | None = None
    after2 = after
    if not skip_cross:
        # Second category (uses warm browser when locale matches).
        second = BENCHMARKS[1]
        print(f"\n=== WARM-CROSS {second['id']} ===", flush=True)
        before2 = _camoufox_counters(get_shared_html_fetcher())
        cross = _run_one(
            label=f"warm_cross:{second['id']}",
            title=second["title"],
            category=second["category"],
            stores=stores,
            max_candidates=3,
        )
        after2 = _camoufox_counters(get_shared_html_fetcher())
        cross["launch_delta"] = after2["browser_launches"] - before2["browser_launches"]
        cross["reuse_delta"] = after2["browser_reuses"] - before2["browser_reuses"]
        results.append(cross)
        print(
            f"elapsed_s={cross['elapsed_s']} matches={cross['match_count']} "
            f"errors={cross['error_count']} launch_delta={cross['launch_delta']} "
            f"reuse_delta={cross['reuse_delta']}",
            flush=True,
        )

    # Per-store table from cold run (best single-product signal).
    print("\n=== COLD MATCH TABLE ===", flush=True)
    print(
        f"{'store':<16} {'status':<8} {'detail'}",
        flush=True,
    )
    matched = {m["store"]: m for m in cold["matches"]}
    erred = {e["store"]: e for e in cold["errors"]}
    for store in stores:
        if store in matched:
            m = matched[store]
            print(
                f"{store:<16} {'MATCH':<8} {m['decision']} {m['confidence']} "
                f"{m['title'][:50]}",
                flush=True,
            )
        elif store in erred:
            e = erred[store]
            print(f"{store:<16} {'ERROR':<8} {e['code']}", flush=True)
        else:
            print(f"{store:<16} {'NO_MATCH':<8}", flush=True)

    payload = {
        "stamp": stamp,
        "stores": stores,
        "results": results,
        "summary": {
            "cold_elapsed_s": cold["elapsed_s"],
            "warm_elapsed_s": warm["elapsed_s"],
            "cross_elapsed_s": None if cross is None else cross["elapsed_s"],
            "cold_matches": cold["match_count"],
            "warm_matches": warm["match_count"],
            "cross_matches": None if cross is None else cross["match_count"],
            "cold_errors": cold["error_count"],
            "warm_launch_delta": warm.get("launch_delta"),
            "warm_reuse_delta": warm.get("reuse_delta"),
            "total_launches": after2["browser_launches"],
            "total_reuses": after2["browser_reuses"],
            "skip_cross": skip_cross,
        },
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(f"\nREPORT {report_path}", flush=True)
    print("SUMMARY", json.dumps(payload["summary"], ensure_ascii=False), flush=True)

    # Best-effort close warm sessions so profile locks are released.
    try:
        shared = get_shared_html_fetcher()
        for obj in (shared, getattr(shared, "browser", None)):
            if obj is None:
                continue
            close = getattr(obj, "close", None)
            if callable(close):
                close()
            direct = getattr(obj, "direct", None)
            if isinstance(direct, CamoufoxHtmlFetcher):
                direct.close()
            proxied = getattr(obj, "proxied", None)
            if isinstance(proxied, CamoufoxHtmlFetcher):
                proxied.close()
            # Progressive wrappers
            inner = getattr(obj, "browser", None) or getattr(obj, "_browser", None)
            if inner is not None and inner is not obj:
                d = getattr(inner, "direct", None)
                if isinstance(d, CamoufoxHtmlFetcher):
                    d.close()
                p = getattr(inner, "proxied", None)
                if isinstance(p, CamoufoxHtmlFetcher):
                    p.close()
    except Exception:
        logging.exception("bench_fetcher_close_failed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
