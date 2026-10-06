"""Root-cause A/B probe — Visão VIP SERP: S25 Ultra vs B650M-E WIFI.

Task 7 / Phase 7: Diagnose why Samsung Galaxy S25 Ultra SERP returns
UPSTREAM_BLOCKED while ASUS TUF B650M-E WIFI succeeds consistently.

Captures per op: query, search URL, slot_id, profile, HTTP status (if
available), final URL, nav/settle result, DOM size, /prod/ link count,
challenge markers, classification, duration_ms.

Usage (Docker, same config as bench_camoufox_capacity / Task 5):

  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_serp_ab.py

  # More iterations (default: 3 per query):
  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_serp_ab.py --iterations 5

  # Custom profile dir (default: /tmp/probe_ab_slot0):
  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_serp_ab.py \\
      --profile-dir /home/app/.cache/scout-api/camoufox-profiles/slot-0

Env: respects CAMOUFOX_* via get_settings().
Output: stdout + memory/working/2026-09-23-camoufox-visaovip-reliability.md
        (VISAO_VIP_DISCOVERY section).
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scout_api.core.config import get_settings
from scout_api.modules.crawler.services.html_fetcher import (
    CamoufoxHtmlFetcher,
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.matching.search_adapters.paraguay.visaovip import (
    VisaoVipSearchAdapter,
)

# ---------------------------------------------------------------------------
# Probe queries (identity-style short queries matching production matching)
# ---------------------------------------------------------------------------

PROBE_QUERIES: list[tuple[str, str]] = [
    ("s25_ultra", "samsung galaxy s25 ultra"),
    ("b650m_wifi", "asus tuf gaming b650m-e wifi"),
]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class OpResult:
    query_id: str
    query: str
    search_url: str
    iteration: int

    # Outcome
    ok: bool = False
    error_type: str | None = None
    error_msg: str | None = None

    # Navigation
    final_url: str | None = None
    http_status: int | None = None
    nav_result: str = "unknown"  # ok | error | challenge | auth_wall | exception

    # DOM analysis
    dom_bytes: int = 0
    prod_link_count: int = 0

    # Challenge / WAF markers
    challenge_detected: bool = False
    auth_wall_detected: bool = False
    challenge_markers_found: list[str] = field(default_factory=list)

    # Classification (per adapter logic)
    empty_classification: str | None = (
        None  # incomplete | genuine_empty | unknown | n/a
    )

    # Duration
    duration_ms: float = 0.0

    # Infra
    slot_id: int = 0
    profile_dir: str = ""


@dataclass
class ProbeRun:
    timestamp: str
    iterations: int
    profile_dir: str
    results: list[OpResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Challenge marker detection (extended, WAF-level)
# ---------------------------------------------------------------------------

_CHALLENGE_MARKERS: list[tuple[str, str]] = [
    # Generic WAF / CF
    ("just a moment", "cf_just_a_moment"),
    ("cf-challenge", "cf_challenge_platform"),
    ("challenge-platform", "cf_challenge_platform"),
    ("attention required", "cf_hard_block"),
    ("you have been blocked", "cf_hard_block"),
    # Akamai
    ("akamai-bot", "akamai_bot_manager"),
    ("sec-if-cpt-container", "akamai_sec_cpt"),
    ("behavioral-content", "akamai_behavioral"),
    # Generic anti-bot markers
    ("verifying you are human", "captcha_interstitial"),
    ("performing security verification", "security_verification"),
    ("robot check", "robot_check"),
    ("validatecaptcha", "amazon_captcha"),
    ("nenhum resultado", "genuine_empty_br"),
    ("não encontramos", "genuine_empty_br"),
    ("nao encontramos", "genuine_empty_br"),
    ("no results", "genuine_empty_en"),
    ("sin resultados", "genuine_empty_es"),
    ("0 resultados", "zero_results"),
]

# Visão VIP specific: the RSC incomplete-hydration shell markers
_INCOMPLETE_HYDRATE_MARKERS: list[tuple[str, str]] = [
    ("__next_data__", "nextjs_inline_data"),
    ("__rsc_", "rsc_payload"),
    ("visaovip.com", "domain_present"),
    ("busca/termo/", "serp_path_in_html"),
    ("<html", "has_html_root"),
    ("/prod/", "prod_link_present"),
    ("loading", "loading_state"),
]


def _scan_markers(
    html: str,
    markers: list[tuple[str, str]],
) -> list[str]:
    lower = html.casefold()
    found = []
    for needle, label in markers:
        if needle in lower:
            found.append(label)
    return found


def _count_prod_links(html: str) -> int:
    """Count distinct /prod/ path occurrences in HTML (href or src)."""
    return len(re.findall(r"/prod/[^\"'\s>]+", html, re.IGNORECASE))


def _infer_nav_result(
    response_text: str,
    final_url: str,
    challenge_detected: bool,
    auth_wall_detected: bool,
    prod_link_count: int,
    classification: str | None,
) -> str:
    if challenge_detected:
        return "challenge"
    if auth_wall_detected:
        return "auth_wall"
    if prod_link_count > 0:
        return "ok"
    if classification == "genuine_empty":
        return "genuine_empty"
    if classification == "incomplete":
        return "incomplete_hydrate"
    if "/busca/termo/" not in final_url.casefold() and response_text:
        return "redirect_away"
    return "ok_but_empty"


# ---------------------------------------------------------------------------
# Fetcher factory (mirrors bench_camoufox_capacity._make_fetcher)
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
        fetch_strategy="probe_ab",
        # No scheduler: probe owns its single fetcher exclusively.
        scheduler=None,
        scheduler_enabled=False,
    )


# ---------------------------------------------------------------------------
# Single op runner
# ---------------------------------------------------------------------------


def _run_op(
    fetcher: CamoufoxHtmlFetcher,
    adapter: VisaoVipSearchAdapter,
    query_id: str,
    query: str,
    iteration: int,
    profile_dir: str,
) -> OpResult:
    request = adapter.build_search_request(query)
    search_url = request.url

    result = OpResult(
        query_id=query_id,
        query=query,
        search_url=search_url,
        iteration=iteration,
        slot_id=0,
        profile_dir=profile_dir,
    )

    t0 = time.perf_counter()
    try:
        response = fetcher.fetch(search_url)
        result.duration_ms = (time.perf_counter() - t0) * 1000

        html = response.text or ""
        final_url = str(response.url or search_url)
        # HtmlResponse.status may be None when Camoufox drives navigation
        raw_status = getattr(response, "status", None)
        result.final_url = final_url
        result.http_status = int(raw_status) if raw_status is not None else None
        result.dom_bytes = len(html.encode("utf-8", errors="replace"))
        result.prod_link_count = _count_prod_links(html)
        result.challenge_detected = is_challenge_page(html)
        result.auth_wall_detected = is_auth_wall_page(html, url=final_url)
        result.challenge_markers_found = _scan_markers(html, _CHALLENGE_MARKERS)

        # Adapter classification (same logic used in production)
        try:
            candidates = adapter.parse_candidates(response)
            if candidates:
                result.empty_classification = "n/a"
            else:
                result.empty_classification = adapter.classify_empty_result(response)
        except Exception as parse_exc:  # noqa: BLE001
            result.empty_classification = f"parse_error:{parse_exc}"

        result.nav_result = _infer_nav_result(
            html,
            final_url,
            result.challenge_detected,
            result.auth_wall_detected,
            result.prod_link_count,
            result.empty_classification,
        )
        result.ok = (
            not result.challenge_detected
            and not result.auth_wall_detected
            and (
                result.prod_link_count > 0
                or result.empty_classification == "genuine_empty"
            )
        )

    except Exception as exc:  # noqa: BLE001
        result.duration_ms = (time.perf_counter() - t0) * 1000
        result.ok = False
        result.error_type = type(exc).__name__
        result.error_msg = str(exc)
        result.nav_result = "exception"

        # Classify exception: check if it's a RequestError with known code
        code = getattr(exc, "code", None)
        if code:
            result.nav_result = f"exception:{code}"

    return result


# ---------------------------------------------------------------------------
# Summary and verdict
# ---------------------------------------------------------------------------


def _summarise(results: list[OpResult]) -> dict[str, Any]:
    by_query: dict[str, list[OpResult]] = {}
    for r in results:
        by_query.setdefault(r.query_id, []).append(r)

    summary: dict[str, Any] = {}
    for qid, ops in by_query.items():
        ok_count = sum(1 for o in ops if o.ok)
        challenge_count = sum(1 for o in ops if o.challenge_detected)
        incomplete_count = sum(1 for o in ops if o.nav_result == "incomplete_hydrate")
        genuine_empty_count = sum(1 for o in ops if o.nav_result == "genuine_empty")
        exception_count = sum(1 for o in ops if o.nav_result.startswith("exception"))
        avg_prod_links = sum(o.prod_link_count for o in ops) / len(ops) if ops else 0
        avg_dom_kb = sum(o.dom_bytes for o in ops) / len(ops) / 1024 if ops else 0
        avg_duration = sum(o.duration_ms for o in ops) / len(ops) if ops else 0
        all_markers: list[str] = []
        for o in ops:
            all_markers.extend(o.challenge_markers_found)
        unique_markers = sorted(set(all_markers))
        summary[qid] = {
            "query": ops[0].query if ops else "",
            "iterations": len(ops),
            "ok_count": ok_count,
            "challenge_count": challenge_count,
            "incomplete_hydrate_count": incomplete_count,
            "genuine_empty_count": genuine_empty_count,
            "exception_count": exception_count,
            "avg_prod_links": round(avg_prod_links, 1),
            "avg_dom_kb": round(avg_dom_kb, 1),
            "avg_duration_ms": round(avg_duration, 0),
            "all_challenge_markers": unique_markers,
            "nav_results": [o.nav_result for o in ops],
            "error_msgs": [o.error_msg for o in ops if o.error_msg],
            "final_urls": list({o.final_url for o in ops if o.final_url}),
        }
    return summary


def _verdict(summary: dict[str, Any]) -> str:
    """Generate a root-cause verdict string from aggregated summary."""
    s25 = summary.get("s25_ultra", {})
    b650m = summary.get("b650m_wifi", {})

    s25_ok = s25.get("ok_count", 0)
    s25_n = s25.get("iterations", 1)
    b650_ok = b650m.get("ok_count", 0)
    b650_n = b650m.get("iterations", 1)

    s25_markers = s25.get("all_challenge_markers", [])
    b650_markers = b650m.get("all_challenge_markers", [])

    s25_nav_results = s25.get("nav_results", [])
    _b650_nav_results = b650m.get("nav_results", [])

    s25_incomplete = s25.get("incomplete_hydrate_count", 0)
    s25_genuine_empty = s25.get("genuine_empty_count", 0)
    s25_challenge = s25.get("challenge_count", 0)
    s25_exception = s25.get("exception_count", 0)
    s25_avg_links = s25.get("avg_prod_links", 0)
    s25_avg_dom = s25.get("avg_dom_kb", 0)

    b650_avg_links = b650m.get("avg_prod_links", 0)

    # Decision tree
    waf_markers = {
        "cf_just_a_moment",
        "cf_challenge_platform",
        "cf_hard_block",
        "akamai_bot_manager",
        "akamai_sec_cpt",
        "security_verification",
        "robot_check",
        "captcha_interstitial",
    }
    s25_waf_hit = bool(set(s25_markers) & waf_markers)
    b650_waf_hit = bool(set(b650_markers) & waf_markers)

    lines = []

    # Both fail?
    if s25_ok == 0 and b650_ok == 0:
        if s25_challenge > 0 or b650_waf_hit or s25_waf_hit:
            lines.append(
                "VERDICT: INFRA/WAF — both queries blocked; not query-specific"
            )
        else:
            lines.append(
                "VERDICT: INFRA — fetcher/session failure affecting all queries"
            )
    # Only S25 fails?
    elif s25_ok == 0 and b650_ok > 0:
        if s25_waf_hit:
            lines.append(
                "VERDICT: WAF/ANTI-BOT — S25 consistently triggers challenge page "
                "(WAF markers present); B650M succeeds → query or product category "
                "triggers more aggressive anti-bot profile on Visão VIP."
            )
        elif s25_incomplete > 0 and s25_avg_dom > 5:
            lines.append(
                "VERDICT: INCOMPLETE HYDRATION — S25 SERP loads a Next.js shell but "
                "RSC/JS never populates /prod/ cards. DOM present but no products. "
                "B650M hydrates OK. Possible: S25 is in a different category or "
                "page-model that takes longer to hydrate, or SERP JS bundle fails "
                "on that category. Camoufox settle timeout may be too short."
            )
        elif s25_genuine_empty > 0:
            lines.append(
                "VERDICT: GENUINE EMPTY — Visão VIP search genuinely returns no "
                "Samsung Galaxy S25 Ultra results (product not in catalogue or "
                "query slug doesn't match). Not a WAF/infra issue. "
                "B650M has products → store has inventory of that category."
            )
        elif s25_exception > 0:
            errors = s25.get("error_msgs", [])
            error_sample = errors[0] if errors else "unknown"
            lines.append(
                f"VERDICT: INFRA/EXCEPTION — S25 throws exception "
                f"({error_sample[:120]}); "
                f"B650M succeeds → likely retryable network error or Camoufox crash "
                f"on that specific navigation, not a query-category difference."
            )
        else:
            lines.append(
                f"VERDICT: UNKNOWN — S25 fails (nav_results={s25_nav_results}) "
                f"without clear WAF/hydration/empty signal. Further inspection needed."
            )
    # Both succeed
    elif s25_ok > 0 and b650_ok > 0:
        lines.append(
            f"VERDICT: BOTH OK (S25={s25_ok}/{s25_n}, B650M={b650_ok}/{b650_n}) — "
            f"no systematic difference detected in this run. Earlier S25 failure "
            f"may have been transient or session-specific."
        )
    # S25 better than B650M (unusual)
    elif s25_ok > 0 and b650_ok == 0:
        lines.append(
            "VERDICT: INVERTED — B650M fails, S25 succeeds. "
            "Possibly transient WAF hit on B650M. Re-run to confirm."
        )
    else:
        lines.append(
            f"VERDICT: PARTIAL — S25={s25_ok}/{s25_n} ok, B650M={b650_ok}/{b650_n} ok. "
            f"Inconsistent; session state or timing-dependent."
        )

    # Append supporting evidence
    lines.append(
        f"Evidence: S25 avg_prod_links={s25_avg_links}, dom_kb={s25_avg_dom:.1f}, "
        f"challenge_markers={s25_markers}; "
        f"B650M avg_prod_links={b650_avg_links}, challenge_markers={b650_markers}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Working log updater
# ---------------------------------------------------------------------------

_LOG_PATH = ROOT / "memory" / "working" / "2026-09-23-camoufox-visaovip-reliability.md"
_SECTION_START = "## VISAO_VIP_DISCOVERY"
_AB_PROBE_START = "### A/B Probe — Task 7 (probe_visaovip_serp_ab)"


def _update_working_log(
    run: ProbeRun, summary: dict[str, Any], verdict_text: str
) -> None:
    """Append/replace the A/B probe section in the working log."""
    log_path = _LOG_PATH
    if not log_path.exists():
        print(f"[WARN] Working log not found at {log_path}; skipping update.")
        return

    content = log_path.read_text(encoding="utf-8")

    # Build the block to insert
    block_lines = [
        "",
        _AB_PROBE_START,
        "",
        f"**Date:** {run.timestamp}",
        f"**Iterations:** {run.iterations} per query",
        f"**Profile dir:** `{run.profile_dir}`",
        "",
        "#### Per-query summary",
        "",
    ]

    for qid, s in summary.items():
        block_lines += [
            f"**Query `{qid}`** — `{s['query']}`",
            "",
            f"- OK: {s['ok_count']}/{s['iterations']}",
            (
                f"- challenge: {s['challenge_count']}, "
                f"incomplete_hydrate: {s['incomplete_hydrate_count']}, "
                f"genuine_empty: {s['genuine_empty_count']}, "
                f"exception: {s['exception_count']}"
            ),
            (
                f"- avg /prod/ links: {s['avg_prod_links']}, avg DOM: "
                f"{s['avg_dom_kb']} KB, avg duration: {s['avg_duration_ms']} ms"
            ),
            f"- nav_results: {s['nav_results']}",
            f"- challenge markers: {s['all_challenge_markers']}",
            f"- final URLs: {s['final_urls']}",
            f"- errors: {s['error_msgs']}",
            "",
        ]

    block_lines += [
        "#### Root-cause verdict",
        "",
    ]
    for line in verdict_text.splitlines():
        block_lines.append(line)

    block_lines += [
        "",
        "---",
        "",
    ]

    block = "\n".join(block_lines)

    # Replace existing block if present, otherwise append after VISAO_VIP_DISCOVERY
    # header
    if _AB_PROBE_START in content:
        # Remove old block (from _AB_PROBE_START to next --- separator or end)
        pattern = re.compile(
            r"(\n?"
            + re.escape(_AB_PROBE_START)
            + r".*?)(?=\n## |\n### (?!"
            + re.escape("A/B")
            + r")|\Z)",
            re.DOTALL,
        )
        content = pattern.sub(block, content, count=1)
    elif _SECTION_START in content:
        # Insert after the section header
        insert_after = content.find(_SECTION_START)
        newline_after = content.find("\n", insert_after)
        if newline_after == -1:
            newline_after = len(content)
        content = content[:newline_after] + "\n" + block + content[newline_after:]
    else:
        # Append at end
        content += "\n" + block

    log_path.write_text(content, encoding="utf-8")
    print(f"\n[LOG] Working log updated: {log_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _print_result(r: OpResult) -> None:
    status = "✓ OK" if r.ok else "✗ FAIL"
    print(
        f"  [{status}] iter={r.iteration} nav={r.nav_result} "
        f"prod_links={r.prod_link_count} dom_kb={r.dom_bytes // 1024} "
        f"duration={r.duration_ms:.0f}ms"
    )
    if r.final_url and r.final_url != r.search_url:
        print(f"         final_url={r.final_url}")
    if r.challenge_markers_found:
        print(f"         challenge_markers={r.challenge_markers_found}")
    if r.error_msg:
        print(f"         error={r.error_type}: {r.error_msg[:200]}")
    if r.empty_classification and r.empty_classification not in ("n/a",):
        print(f"         classification={r.empty_classification}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visão VIP A/B SERP root-cause probe (Task 7)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of fetch iterations per query (default: 3)",
    )
    parser.add_argument(
        "--profile-dir",
        default="/tmp/probe_ab_slot0",
        help="Camoufox profile directory (isolated from production bind mount)",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Skip writing to working log",
    )
    args = parser.parse_args()

    profile_dir = Path(args.profile_dir)
    print("\n=== Visão VIP A/B SERP Root-Cause Probe (Task 7) ===")
    print(f"Profile: {profile_dir}")
    print(f"Iterations per query: {args.iterations}")
    print(f"Queries: {[q for _, q in PROBE_QUERIES]}")
    print()

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = ProbeRun(
        timestamp=timestamp,
        iterations=args.iterations,
        profile_dir=str(profile_dir),
    )

    fetcher = _make_fetcher(profile_dir)
    adapter = VisaoVipSearchAdapter()

    try:
        for query_id, query in PROBE_QUERIES:
            request = adapter.build_search_request(query)
            print(f"── Query: [{query_id}] '{query}'")
            print(f"   URL: {request.url}")
            print()
            for i in range(1, args.iterations + 1):
                print(f"  Iteration {i}/{args.iterations} ...")
                op = _run_op(
                    fetcher=fetcher,
                    adapter=adapter,
                    query_id=query_id,
                    query=query,
                    iteration=i,
                    profile_dir=str(profile_dir),
                )
                run.results.append(op)
                _print_result(op)
                # Brief pause between iterations to avoid session spam
                if i < args.iterations:
                    time.sleep(1.0)
            print()
    finally:
        fetcher.close()

    summary = _summarise(run.results)
    verdict_text = _verdict(summary)

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for qid, s in summary.items():
        print(
            f"\n{qid}: ok={s['ok_count']}/{s['iterations']} "
            f"prod_links_avg={s['avg_prod_links']} "
            f"dom_kb_avg={s['avg_dom_kb']} "
            f"duration_avg={s['avg_duration_ms']}ms"
        )
        print(f"  nav_results:  {s['nav_results']}")
        print(f"  challenge:    {s['all_challenge_markers']}")
        print(f"  final_urls:   {s['final_urls']}")

    print()
    print("=" * 60)
    print("ROOT-CAUSE VERDICT")
    print("=" * 60)
    print(verdict_text)
    print()

    # Save JSON summary alongside log
    json_out = ROOT / "memory" / "working" / f"probe_ab_visaovip_{timestamp[:10]}.json"
    try:
        json_out.write_text(
            json.dumps(
                {
                    "timestamp": timestamp,
                    "profile_dir": str(profile_dir),
                    "iterations": args.iterations,
                    "summary": summary,
                    "verdict": verdict_text,
                    "raw_results": [asdict(r) for r in run.results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[JSON] {json_out}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Could not write JSON: {exc}")

    if not args.no_log:
        _update_working_log(run, summary, verdict_text)

    # Exit code: 0 if B650M works (baseline sanity); 1 if both fail (infra issue)
    b650_ok = summary.get("b650m_wifi", {}).get("ok_count", 0)
    if b650_ok == 0:
        print("\n[WARN] B650M baseline also failed — possible infra issue in this env.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
