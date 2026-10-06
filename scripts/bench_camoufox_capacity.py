"""Camoufox capacity benchmark harness (C1 / C2 / C3).

Phase 0 (Task 0): skeleton + C1 baseline metrics only.
Task 5: completes multi-capacity runs with isolated ``slot-*`` profile dirs,
        parallel workers, and full metric collection.

Usage (Docker, isolated profile — does not touch shared bind mount)::

  # Layer 1 — infra (example.com)
  docker compose run --rm --no-deps api python scripts/bench_camoufox_capacity.py \\
    --capacity 1 --iterations 10 --layer layer1

  # Layer 2 — Visão VIP SERP
  docker compose run --rm --no-deps api python scripts/bench_camoufox_capacity.py \\
    --capacity 2 --iterations 10 --layer layer2

  # Both layers (default)
  docker compose run --rm --no-deps api python scripts/bench_camoufox_capacity.py \\
    --capacity 2 --iterations 10 --layer both

Env: respects ``CAMOUFOX_*`` via ``get_settings()`` when profile dir omitted.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.html_fetcher import CamoufoxHtmlFetcher

LAYER1_URL = "https://example.com/"
LAYER2_URL = "https://www.visaovip.com/busca/termo/notebook/"


# ---------------------------------------------------------------------------
# System metric helpers
# ---------------------------------------------------------------------------


def _rss_kb() -> int | None:
    """Best-effort RSS sample (Linux /proc or resource module)."""
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(usage.ru_maxrss)
        if sys.platform == "darwin":
            return rss // 1024
        return rss
    except Exception:  # noqa: BLE001
        return None


def _cpu_time_s() -> float:
    """Return current process CPU time (user+sys) in seconds."""
    try:
        return time.process_time()
    except Exception:  # noqa: BLE001
        return 0.0


def _count_firefox_processes() -> int:
    """Count running firefox-* processes (Linux /proc)."""
    count = 0
    proc_path = Path("/proc")
    if proc_path.is_dir():
        for pid_dir in proc_path.iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text(errors="ignore").strip().lower()
                if "firefox" in comm:
                    count += 1
            except OSError:
                pass
        return count
    try:
        import psutil  # type: ignore[import-not-found]

        return sum(
            1
            for p in psutil.process_iter(["name"])
            if "firefox" in (p.info.get("name") or "").lower()
        )
    except Exception:  # noqa: BLE001
        return -1


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FetchSample:
    iteration: int
    slot_id: int
    ok: bool
    wall_ms: float
    error: str | None = None
    rss_kb: int | None = None


@dataclass
class SlotStats:
    slot_id: int
    profile_dir: str
    browser_launches: int
    browser_reuses: int
    browser_launch_failures: int
    circuit_open_hits: int
    fetch_count: int
    ok_count: int


@dataclass
class BenchResult:
    capacity: int
    layer: str  # "layer1" | "layer2"
    url: str
    profile_root: str
    iterations: int  # iterations per slot (not total)

    # Aggregate timing
    elapsed_s: float = 0.0
    throughput_rps: float = 0.0

    # Per-fetch latency (all successful fetches across all slots)
    nav_wall_ms_p50: float = 0.0
    nav_wall_ms_p95: float = 0.0
    nav_wall_ms_max: float = 0.0

    # Counts (aggregate)
    total_fetches: int = 0
    total_ok: int = 0
    total_errors: int = 0
    launch_failures: int = 0
    circuit_open_hits: int = 0

    # Memory / CPU
    rss_kb_start: int | None = None
    rss_kb_end: int | None = None
    rss_kb_peak: int | None = None
    cpu_delta_s: float = 0.0
    firefox_processes_start: int = 0
    firefox_processes_peak: int = 0

    # Per-slot summary
    slot_stats: list[SlotStats] = field(default_factory=list)

    # Raw samples (all slots combined)
    fetch_samples: list[FetchSample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Fetcher factory
# ---------------------------------------------------------------------------


def _make_fetcher(profile_dir: Path) -> CamoufoxHtmlFetcher:
    settings = get_settings()
    profile_dir.mkdir(parents=True, exist_ok=True)
    return CamoufoxHtmlFetcher(
        headless=settings.camoufox_headless,
        humanize=settings.camoufox_humanize,
        timeout_ms=int(settings.camoufox_timeout_ms),
        launch_timeout_ms=int(settings.camoufox_launch_timeout_ms),
        settle_ms=int(settings.camoufox_settle_ms),
        max_settle_attempts=int(settings.camoufox_max_settle_attempts),
        user_data_dir=profile_dir,
        disable_coop=settings.camoufox_disable_coop,
        warmup_origin=settings.camoufox_warmup_origin,
        warmup_policy="once_per_session",
        warm_reuse=True,
        warm_max_fetches=int(settings.camoufox_warm_max_fetches),
        fetch_strategy="bench",
        # No scheduler in bench: each slot gets its own fetcher
        scheduler=None,
        scheduler_enabled=False,
    )


# ---------------------------------------------------------------------------
# Single-slot sequential runner (used by C1 and as sub-run inside parallel)
# ---------------------------------------------------------------------------


def _run_slot(
    fetcher: CamoufoxHtmlFetcher,
    slot_id: int,
    url: str,
    iterations: int,
    start_iter: int = 1,
    rss_samples: list[int] | None = None,
    ff_samples: list[int] | None = None,
) -> tuple[list[FetchSample], SlotStats]:
    """Run ``iterations`` sequential fetches on one slot/fetcher.

    Thread-safe: each call owns its own fetcher instance.
    """
    samples: list[FetchSample] = []

    for i in range(iterations):
        t0 = time.perf_counter()
        err: str | None = None
        ok = True
        try:
            fetcher.fetch(url)
        except Exception as exc:  # noqa: BLE001
            ok = False
            err = f"{type(exc).__name__}: {exc}"
        wall_ms = (time.perf_counter() - t0) * 1000
        rss = _rss_kb()
        if rss is not None and rss_samples is not None:
            rss_samples.append(rss)
        ff = _count_firefox_processes()
        if ff >= 0 and ff_samples is not None:
            ff_samples.append(ff)
        samples.append(
            FetchSample(
                iteration=start_iter + i,
                slot_id=slot_id,
                ok=ok,
                wall_ms=round(wall_ms, 1),
                error=err,
                rss_kb=rss,
            )
        )

    stats = SlotStats(
        slot_id=slot_id,
        profile_dir=str(fetcher._user_data_dir),
        browser_launches=fetcher.browser_launch_count,
        browser_reuses=fetcher.browser_reuse_count,
        browser_launch_failures=fetcher.browser_launch_failure_count,
        circuit_open_hits=fetcher.browser_circuit_open_hit_count,
        fetch_count=iterations,
        ok_count=sum(1 for s in samples if s.ok),
    )
    return samples, stats


# ---------------------------------------------------------------------------
# Multi-capacity runner
# ---------------------------------------------------------------------------


def run_capacity(
    capacity: int,
    *,
    url: str,
    profile_root: Path,
    iterations: int,
    layer: str,
) -> BenchResult:
    """Run ``capacity`` parallel browser slots, ``iterations`` fetches each.

    C1: sequential (capacity=1).
    C2/C3: parallel workers, each with an isolated slot-{id} profile dir.
    """
    slot_dirs = [profile_root / f"slot-{i}" for i in range(capacity)]
    for d in slot_dirs:
        d.mkdir(parents=True, exist_ok=True)

    rss_start = _rss_kb()
    cpu_start = _cpu_time_s()
    ff_start = max(0, _count_firefox_processes())

    # Shared mutable state for cross-thread metrics
    rss_samples: list[int] = []
    ff_samples: list[int] = []
    rss_lock = threading.Lock()
    ff_lock = threading.Lock()

    all_samples: list[FetchSample] = []
    all_stats: list[SlotStats] = []

    t_wall_start = time.perf_counter()

    if capacity == 1:
        # Sequential: C1 path (direct, no threading overhead)
        fetcher = _make_fetcher(slot_dirs[0])
        slot_rss: list[int] = []
        slot_ff: list[int] = []
        slot_samples, slot_stat = _run_slot(
            fetcher,
            0,
            url,
            iterations,
            rss_samples=slot_rss,
            ff_samples=slot_ff,
        )
        all_samples.extend(slot_samples)
        all_stats.append(slot_stat)
        rss_samples.extend(slot_rss)
        ff_samples.extend(slot_ff)
        try:
            fetcher.close()
        except Exception:  # noqa: BLE001
            pass
    else:
        # Parallel: one thread per slot, each with its own fetcher
        fetchers = [_make_fetcher(d) for d in slot_dirs]

        def run_slot_thread(idx: int) -> tuple[list[FetchSample], SlotStats]:
            slot_rss: list[int] = []
            slot_ff: list[int] = []
            s, st = _run_slot(
                fetchers[idx],
                idx,
                url,
                iterations,
                start_iter=idx * iterations + 1,
                rss_samples=slot_rss,
                ff_samples=slot_ff,
            )
            with rss_lock:
                rss_samples.extend(slot_rss)
            with ff_lock:
                ff_samples.extend(slot_ff)
            return s, st

        with ThreadPoolExecutor(
            max_workers=capacity, thread_name_prefix="bench-slot"
        ) as pool:
            futures: list[Future[tuple[list[FetchSample], SlotStats]]] = [
                pool.submit(run_slot_thread, i) for i in range(capacity)
            ]
            for fut in as_completed(futures):
                slot_s, slot_st = fut.result()
                all_samples.extend(slot_s)
                all_stats.append(slot_st)

        for f in fetchers:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass

    elapsed_s = time.perf_counter() - t_wall_start
    cpu_delta = _cpu_time_s() - cpu_start
    rss_end = _rss_kb()

    # Aggregate metrics
    total_fetches = len(all_samples)
    total_ok = sum(1 for s in all_samples if s.ok)
    total_errors = total_fetches - total_ok
    ok_walls = sorted(s.wall_ms for s in all_samples if s.ok)

    launch_failures = sum(st.browser_launch_failures for st in all_stats)
    circuit_open_hits = sum(st.circuit_open_hits for st in all_stats)

    rss_peak: int | None = None
    if rss_samples:
        rss_peak = max(rss_samples)

    ff_peak = max(ff_samples, default=0)

    throughput = total_ok / elapsed_s if elapsed_s > 0 else 0.0

    # Sort samples by iteration for readability
    all_samples.sort(key=lambda s: (s.slot_id, s.iteration))

    return BenchResult(
        capacity=capacity,
        layer=layer,
        url=url,
        profile_root=str(profile_root),
        iterations=iterations,
        elapsed_s=round(elapsed_s, 3),
        throughput_rps=round(throughput, 4),
        nav_wall_ms_p50=round(_percentile(ok_walls, 50), 1),
        nav_wall_ms_p95=round(_percentile(ok_walls, 95), 1),
        nav_wall_ms_max=round(max(ok_walls, default=0.0), 1),
        total_fetches=total_fetches,
        total_ok=total_ok,
        total_errors=total_errors,
        launch_failures=launch_failures,
        circuit_open_hits=circuit_open_hits,
        rss_kb_start=rss_start,
        rss_kb_end=rss_end,
        rss_kb_peak=rss_peak,
        cpu_delta_s=round(cpu_delta, 3),
        firefox_processes_start=ff_start,
        firefox_processes_peak=ff_peak,
        slot_stats=all_stats,
        fetch_samples=all_samples,
    )


# ---------------------------------------------------------------------------
# Backward-compat shim (kept for Task 0 compatibility)
# ---------------------------------------------------------------------------


def run_c1_baseline(
    *,
    url: str,
    profile_dir: Path,
    iterations: int,
) -> BenchResult:
    """C1 sequential baseline (Task 0 compat). Prefer run_capacity(1, ...)."""
    return run_capacity(
        1,
        url=url,
        profile_root=profile_dir.parent,
        iterations=iterations,
        layer="layer1",
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _default_profile_root() -> Path:
    raw = os.environ.get("BENCH_CAMOUFOX_PROFILE_ROOT", "").strip()
    if raw:
        return Path(raw)
    return Path("/tmp/scout-bench-camoufox-capacity")


def _layer2_iterations(base: int) -> int:
    """Layer 2 (live site) uses fewer iterations to avoid long runtimes."""
    return max(1, min(base, 5))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camoufox capacity benchmark (C1/C2/C3)"
    )
    parser.add_argument("--capacity", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Iterations per slot (not total). Layer 2 capped at 5.",
    )
    parser.add_argument(
        "--layer",
        default="both",
        choices=("layer1", "layer2", "both"),
        help="Which layer(s) to run",
    )
    parser.add_argument(
        "--layer1-url", default=os.environ.get("BENCH_LAYER1_URL", LAYER1_URL)
    )
    parser.add_argument(
        "--layer2-url", default=os.environ.get("BENCH_LAYER2_URL", LAYER2_URL)
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=None,
        help="Root for slot-* dirs (default /tmp/scout-bench-camoufox-capacity)",
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, help="Write combined JSON to this file"
    )
    args = parser.parse_args()

    profile_root = args.profile_root or _default_profile_root()
    cap = args.capacity
    iters = max(1, args.iterations)
    l2_iters = _layer2_iterations(iters)

    results: dict[str, Any] = {
        "capacity": cap,
        "date": time.strftime("%Y-%m-%d"),
        "measurement_meta": {
            "container": "docker compose run --rm --no-deps api",
            "isolated_profiles": True,
            "profile_root": str(profile_root),
            "note": f"Capacity C{cap} bench — Task 5",
        },
    }

    if args.layer in ("layer1", "both"):
        print(
            (
                f"\n[bench] Layer 1 — {args.layer1_url!r} "
                f" capacity={cap}  iterations={iters}"
            ),
            flush=True,
        )
        r1 = run_capacity(
            cap,
            url=args.layer1_url,
            profile_root=profile_root / "layer1",
            iterations=iters,
            layer="layer1",
        )
        results["layer1"] = r1.to_dict()
        print(
            f"[bench] Layer1 done: ok={r1.total_ok}/{r1.total_fetches}  "
            f"elapsed={r1.elapsed_s:.1f}s  throughput={r1.throughput_rps:.3f}rps  "
            f"P50={r1.nav_wall_ms_p50}ms  P95={r1.nav_wall_ms_p95}ms  "
            f"launches={r1.launch_failures}fail",
            flush=True,
        )

    if args.layer in ("layer2", "both"):
        print(
            (
                f"\n[bench] Layer 2 — {args.layer2_url!r} "
                f" capacity={cap}  iterations={l2_iters}"
            ),
            flush=True,
        )
        r2 = run_capacity(
            cap,
            url=args.layer2_url,
            profile_root=profile_root / "layer2",
            iterations=l2_iters,
            layer="layer2",
        )
        results["layer2"] = r2.to_dict()
        print(
            f"[bench] Layer2 done: ok={r2.total_ok}/{r2.total_fetches}  "
            f"elapsed={r2.elapsed_s:.1f}s  throughput={r2.throughput_rps:.3f}rps  "
            f"P50={r2.nav_wall_ms_p50}ms  P95={r2.nav_wall_ms_p95}ms  "
            f"launches={r2.launch_failures}fail",
            flush=True,
        )

    text = json.dumps(results, indent=2, ensure_ascii=False)
    print("\n" + text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[bench] JSON saved → {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
