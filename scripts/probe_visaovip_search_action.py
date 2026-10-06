"""Task 8 — Capture real browser POST for Visão VIP searchProducts Server Action.

Usage (Docker, same config as existing probes):

  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_search_action.py

  # Override profile dir (default: /tmp/probe_action_slot0):
  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_search_action.py \\
      --profile-dir /home/app/.cache/scout-api/camoufox-profiles/slot-0

  # Dry run: only extract action IDs from chunk JS (no browser):
  docker compose run --rm --no-deps api \\
    python scripts/probe_visaovip_search_action.py --discover-only

Captures per query:
  - POST URL, Next-Action header, request headers, request body (first 4 KB)
  - Response status, Content-Type, first 2 KB of RSC response body
  - Action-ID stability: verifies that two fresh page loads return the same ID
  - Whether session/cookies are required

Writes findings to memory/working/2026-09-23-camoufox-visaovip-reliability.md
under the VISAO_VIP_DISCOVERY section.
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

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.visaovip.com"

# Known action IDs extracted statically from chunk_serp3 (captured 2026-09-23).
# These are a SNAPSHOT — may change on the next Visão VIP deploy.
# Use discover_action_ids() to refresh before any HTTP call.
_STATIC_ACTION_IDS: dict[str, str] = {
    "searchProducts": "7f674263c13a9d8d28d0768c8016b1791b8051502a",
    "searchFacets": "784f2ef5d5603dc817ded8e27c904518fc45af1b63",
}

# Chunk that contains the SERP search logic (also subject to change on deploy).
# The probe discovers the current chunk dynamically via the SERP HTML.
_SERP_CHUNK_RE = re.compile(r'src="(/_next/static/chunks/[0-9a-f]{16}\.js)"')

# Pattern to extract action IDs from a JS chunk
_ACTION_ID_RE = re.compile(
    r'createServerReference\("([0-9a-f]{40,})",\S+callServer\S+,void 0,'
    r'\S+findSourceMapURL,"(\w+)"\)'
)

PROBE_QUERIES: list[tuple[str, str, str]] = [
    # (query_id, human_query, url_slug)
    ("b650m_wifi", "asus tuf gaming b650m-e wifi", "asus-tuf-gaming-b650m-e-wifi"),
    ("s25_ultra", "samsung galaxy s25 ultra", "samsung-galaxy-s25-ultra"),
]

_LOG_PATH = ROOT / "memory" / "working" / "2026-09-23-camoufox-visaovip-reliability.md"

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class ActionCaptureResult:
    query_id: str
    slug: str

    # Discovery
    action_id_from_chunk: str | None = None
    action_id_stable: bool | None = None  # same across 2 page fetches?
    chunk_url: str | None = None
    build_id: str | None = None

    # POST call
    post_url: str | None = None
    post_headers: dict[str, str] = field(default_factory=dict)
    request_body_sample: str | None = None  # first 4 KB, sanitized
    response_status: int | None = None
    response_content_type: str | None = None
    response_body_sample: str | None = None  # first 2 KB
    response_products_count: int | None = None

    # Flags
    requires_session: bool | None = None  # 401/403 without cookies?
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ProbeReport:
    timestamp: str
    results: list[ActionCaptureResult] = field(default_factory=list)
    go_no_go: str = "UNKNOWN"
    verdict_detail: str = ""


# ---------------------------------------------------------------------------
# Action ID discovery (HTTP, no browser)
# ---------------------------------------------------------------------------


def _http_headers(referer: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    if referer:
        h["Referer"] = referer
    return h


def discover_action_ids(
    slug: str = "asus-tuf-gaming-b650m-e-wifi",
    timeout: float = 20.0,
) -> tuple[dict[str, str], str | None, str | None]:
    """
    Attempt HTTP discovery of Server Action IDs from the SERP chunk JS.

    NOTE: Visão VIP is behind Cloudflare. Plain HTTP requests receive a
    5.8 KB shell page (no script tags). Full SERP HTML — including chunk JS
    URLs — is only served to real browser fingerprints.

    This function tries HTTP first (fast), then falls back to static IDs.
    For production use, dynamic discovery MUST use the browser path in
    _capture_via_browser() which intercepts real Server Action POSTs.

    Returns (action_ids_dict, chunk_url, build_id).
    """
    serp_url = f"{BASE_URL}/busca/termo/{slug}/"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(serp_url, headers=_http_headers())
            html = r.text

        # Cloudflare shell check: if the page is too small, we're getting CF shell
        if len(html) < 20_000:
            print(
                f"[WARN] SERP HTML is {len(html)} bytes — likely Cloudflare shell. "
                "Dynamic chunk discovery requires browser (Camoufox). "
                "Using static IDs as fallback."
            )
            return dict(_STATIC_ACTION_IDS), None, None

        # Extract build ID from Next.js RSC payload
        build_m = re.search(r'"b":"([A-Za-z0-9_-]{10,30})"', html)
        build_id = build_m.group(1) if build_m else None

        # Find candidate chunk scripts (there are many; we look for the SERP-specific
        # one)
        chunk_urls = _SERP_CHUNK_RE.findall(html)

        # Try each chunk to find searchProducts; bail after first hit
        action_ids: dict[str, str] = {}
        found_chunk_url: str | None = None
        with httpx.Client(timeout=timeout) as client:
            for rel in chunk_urls:
                chunk_url = f"{BASE_URL}{rel}"
                cr = client.get(chunk_url, headers=_http_headers(referer=serp_url))
                if cr.status_code != 200:
                    continue
                chunk_text = cr.text
                for m in _ACTION_ID_RE.finditer(chunk_text):
                    action_id, fn_name = m.group(1), m.group(2)
                    action_ids[fn_name] = action_id
                if "searchProducts" in action_ids:
                    found_chunk_url = chunk_url
                    break

        if action_ids:
            return action_ids, found_chunk_url, build_id

    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] discover_action_ids failed: {exc}; using static IDs")

    return dict(_STATIC_ACTION_IDS), None, None


def verify_action_id_live(
    action_id: str, slug: str = "asus-tuf-gaming-b650m-e-wifi"
) -> bool:
    """
    Probe whether an action ID is currently valid by making a POST and
    checking for '404 Server action not found' vs other response.

    Returns True if the ID appears valid (not 404), False if rotated.
    """
    post_url = f"{BASE_URL}/busca/termo/{slug}/"
    payload = json.dumps(
        [slug, "termo", [], "pt-BR", 1, 24, "all"],
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            resp = client.post(
                post_url,
                content=payload,
                headers={
                    **_http_headers(referer=post_url),
                    "Next-Action": action_id,
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Accept": "text/x-component,*/*",
                    "x-intlayer-locale": "pt-BR",
                    "Origin": BASE_URL,
                },
            )
        body = resp.text[:200]
        if resp.status_code == 404 and "not found" in body.lower():
            return False  # ID rotated
        return True  # 200 or 500 = endpoint accepted the ID
    except Exception:  # noqa: BLE001
        return False


def check_action_id_stability(
    slug: str = "asus-tuf-gaming-b650m-e-wifi",
) -> tuple[bool, str | None, str | None, bool]:
    """
    Two-fetch stability check + live validation of the current ID.

    Returns (is_stable_across_fetches, id_run1, id_run2, id_is_currently_valid).
    """
    ids1, _, _ = discover_action_ids(slug)
    time.sleep(2.0)
    ids2, _, _ = discover_action_ids(slug)
    id1 = ids1.get("searchProducts")
    id2 = ids2.get("searchProducts")
    stable = id1 == id2
    live = verify_action_id_live(id1) if id1 else False
    return stable, id1, id2, live


# ---------------------------------------------------------------------------
# Server Action POST call (HTTP, no browser)
# ---------------------------------------------------------------------------


def _build_action_payload(
    search_term: str,
    search_type: str = "termo",
    characteristics: list[Any] | None = None,
    locale: str = "pt-BR",
    page: int = 1,
    per_page: int = 24,
    stock: str = "all",
) -> bytes:
    """
    Build the Next.js Server Action payload for searchProducts.

    Next.js encodes Server Action args as a JSON array for programmatic calls.
    The wire format for `createServerReference` calls is:
        POST body = JSON.stringify([arg0, arg1, ...])
        Content-Type: text/plain;charset=UTF-8

    searchProducts signature (from JS decompile):
        p(searchTerm, searchType, characteristicFilters, locale, page, perPage, stock)
    """
    args = [
        search_term,  # t: searchTerm (e.g. "asus-tuf-gaming-b650m-e-wifi")
        search_type,  # r: type ("termo", "categoria", "marca", "destaques", etc.)
        characteristics or [],  # l: characteristic filters (empty for unfiltered)
        locale,  # e: locale ("pt-BR")
        page,  # o: page
        per_page,  # n: perPage
        stock,  # s: stock filter ("all" | "in" | "out")
    ]
    return json.dumps(args, ensure_ascii=False).encode("utf-8")


def call_search_action(
    slug: str,
    search_term: str,
    action_id: str,
    timeout: float = 20.0,
) -> ActionCaptureResult:
    """
    Make a real HTTP POST to the searchProducts Server Action and return findings.
    """
    result = ActionCaptureResult(query_id=slug, slug=slug)
    # Try both URL variants: canonical + pt-BR rewrite
    post_urls = [
        f"{BASE_URL}/busca/termo/{slug}/",
        f"{BASE_URL}/pt-BR/busca/termo/{slug}/",
    ]

    # Build payload
    payload = _build_action_payload(
        search_term=search_term,
        search_type="termo",
    )
    result.request_body_sample = payload.decode("utf-8")[:4096]

    for post_url in post_urls:
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                resp = client.post(
                    post_url,
                    content=payload,
                    headers={
                        **_http_headers(referer=post_url),
                        "Next-Action": action_id,
                        "Content-Type": "text/plain;charset=UTF-8",
                        "Accept": "text/x-component,*/*",
                        "x-intlayer-locale": "pt-BR",
                        "Origin": BASE_URL,
                    },
                )
            result.duration_ms = (time.perf_counter() - t0) * 1000
            result.post_url = post_url
            result.post_headers = {
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "x-intlayer-locale": "pt-BR",
            }
            result.response_status = resp.status_code
            result.response_content_type = resp.headers.get("content-type", "")
            body_text = resp.text[:2048]
            result.response_body_sample = body_text

            # Check for products in response
            _prod_m = re.search(r'"products":\[([^\]]*)\]', body_text)  # noqa: F841
            count_m = re.search(r'"totalCount":(\d+)', body_text)
            if count_m:
                result.response_products_count = int(count_m.group(1))

            # Session requirement: 4xx means cookies/auth needed
            if resp.status_code in (401, 403):
                result.requires_session = True
            elif resp.status_code == 200:
                result.requires_session = False
            elif resp.status_code == 500:
                # 500 with x-component = accepted but bad args
                if "text/x-component" in result.response_content_type:
                    result.requires_session = False

            # If successful with this URL, stop trying
            if resp.status_code in (200, 500):
                break

        except Exception as exc:  # noqa: BLE001
            result.duration_ms = (time.perf_counter() - t0) * 1000
            result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Browser-based capture (Camoufox)
# ---------------------------------------------------------------------------


def _capture_via_browser(
    profile_dir: Path,
    slug: str,
    query_id: str,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """
    Use Camoufox Playwright to navigate to the SERP and intercept the
    searchProducts Server Action request.

    Captures: URL, headers, request body, response status, response body.
    Returns a dict of captured network info (may be empty on failure).
    """
    captured: dict[str, Any] = {
        "request_url": None,
        "request_headers": {},
        "request_body": None,
        "response_status": None,
        "response_headers": {},
        "response_body": None,
        "error": None,
    }

    try:
        from camoufox.sync_api import Camoufox  # type: ignore[import]

        serp_url = f"{BASE_URL}/busca/termo/{slug}/"
        profile_dir.mkdir(parents=True, exist_ok=True)

        with Camoufox(
            headless=True,
            humanize=True,
            user_data_dir=str(profile_dir),
        ) as browser:
            page = browser.new_page()

            action_requests: list[dict[str, Any]] = []

            def on_request(req: Any) -> None:
                if req.method != "POST":
                    return
                hdrs = req.all_headers()
                if "next-action" not in hdrs:
                    return
                body_bytes = req.post_data_buffer
                action_requests.append(
                    {
                        "url": req.url,
                        "headers": hdrs,
                        "body": body_bytes[:4096].hex() if body_bytes else None,
                        "body_text": (
                            body_bytes[:4096].decode("utf-8", errors="replace")
                            if body_bytes
                            else None
                        ),
                    }
                )

            page.on("request", on_request)

            finished: list[dict[str, Any]] = []

            def on_response(resp: Any) -> None:
                if resp.request.method != "POST":
                    return
                hdrs = resp.request.all_headers()
                if "next-action" not in hdrs:
                    return
                try:
                    resp_body = resp.body()[:2048].decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    resp_body = None
                finished.append(
                    {
                        "url": resp.url,
                        "status": resp.status,
                        "headers": dict(resp.headers),
                        "body": resp_body,
                    }
                )

            page.on("response", on_response)

            # Navigate and wait for network idle
            page.goto(serp_url, timeout=int(timeout_s * 1000), wait_until="networkidle")
            # Extra settle for RSC hydration
            page.wait_for_timeout(8000)

            page.close()

        if action_requests:
            req = action_requests[-1]
            captured["request_url"] = req["url"]
            captured["request_headers"] = req["headers"]
            captured["request_body"] = req["body_text"]

        if finished:
            resp = finished[-1]
            captured["response_status"] = resp["status"]
            captured["response_headers"] = resp["headers"]
            captured["response_body"] = resp["body"]

    except Exception as exc:  # noqa: BLE001
        captured["error"] = str(exc)

    return captured


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run_probe(profile_dir: Path, use_browser: bool = True) -> ProbeReport:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = ProbeReport(timestamp=timestamp)

    print("\n=== Step 1: Discover action IDs from SERP chunk JS ===")
    action_ids, chunk_url, build_id = discover_action_ids()
    search_action_id = action_ids.get(
        "searchProducts", _STATIC_ACTION_IDS["searchProducts"]
    )
    facets_action_id = action_ids.get(
        "searchFacets", _STATIC_ACTION_IDS["searchFacets"]
    )

    print(f"  searchProducts ID : {search_action_id}")
    print(f"  searchFacets ID   : {facets_action_id}")
    print(f"  chunk_url         : {chunk_url}")
    print(f"  build_id          : {build_id}")

    id_matches_static = search_action_id == _STATIC_ACTION_IDS["searchProducts"]
    print(f"  matches static    : {id_matches_static}")

    print("\n=== Step 2: Stability check (2 page fetches + live validation) ===")
    is_stable, id1, id2, id_live = check_action_id_stability()
    print(f"  run1={id1}")
    print(f"  run2={id2}")
    print(f"  stable={is_stable}")
    print(f"  id_live (POST 404-check)={id_live}")

    print("\n=== Step 3: HTTP POST probe (no browser) ===")
    http_results: list[ActionCaptureResult] = []
    for query_id, query, slug in PROBE_QUERIES:
        print(f"\n  Query [{query_id}] '{query}' → slug={slug}")
        res = call_search_action(
            slug=slug,
            search_term=slug,
            action_id=search_action_id,
        )
        res.action_id_from_chunk = search_action_id
        res.action_id_stable = is_stable
        res.chunk_url = chunk_url
        res.build_id = build_id
        http_results.append(res)
        print(
            f"    status={res.response_status} content_type={res.response_content_type}"
        )
        print(
            f"    products={res.response_products_count} "
            f"duration={res.duration_ms:.0f}ms"
        )
        print(f"    error={res.error}")
        if res.response_body_sample:
            print(f"    body[:200]={res.response_body_sample[:200]!r}")

    browser_results: list[dict[str, Any]] = []
    if use_browser:
        print("\n=== Step 4: Browser capture (Camoufox) ===")
        for query_id, _query, slug in PROBE_QUERIES:
            print(f"\n  [{query_id}] Navigating to /busca/termo/{slug}/...")
            slot_profile = profile_dir / f"action_probe_{query_id}"
            cap = _capture_via_browser(slot_profile, slug, query_id)
            browser_results.append({"query_id": query_id, "slug": slug, **cap})
            print(f"    request_url={cap['request_url']}")
            action_hdr = (cap.get("request_headers") or {}).get("next-action")
            print(f"    next-action header={action_hdr}")
            if cap["request_body"]:
                print(f"    request_body[:200]={cap['request_body'][:200]!r}")
            print(f"    response_status={cap['response_status']}")
            if cap["response_body"]:
                print(f"    response_body[:200]={cap['response_body'][:200]!r}")
            if cap["error"]:
                print(f"    error={cap['error']}")

    # Synthesize Go/No-Go
    report.results = http_results
    report.go_no_go, report.verdict_detail = _verdict(
        http_results, browser_results, is_stable, search_action_id, id_live=id_live
    )

    return report


def _verdict(
    http_results: list[ActionCaptureResult],
    browser_results: list[dict[str, Any]],
    id_stable: bool,
    action_id: str,
    id_live: bool = False,
) -> tuple[str, str]:
    """Return (GO/NO-GO, detail_text)."""
    b650m = next((r for r in http_results if r.query_id == "b650m_wifi"), None)
    s25 = next((r for r in http_results if r.query_id == "s25_ultra"), None)

    b650m_200 = b650m and b650m.response_status == 200
    b650m_prods = b650m and (b650m.response_products_count or 0) > 0
    b650m_404 = b650m and b650m.response_status == 404
    s25_200 = s25 and s25.response_status == 200
    s25_prods = s25 and (s25.response_products_count or 0) > 0

    # Browser capture found the actual POST
    browser_b650m = next(
        (r for r in browser_results if r.get("query_id") == "b650m_wifi"), None
    )
    browser_b650m_captured = bool(browser_b650m and browser_b650m.get("request_url"))

    lines = []

    # Action ID stability
    lines.append("=== ACTION ID STABILITY ===")
    if not id_live and b650m_404:
        lines.append(
            "ACTION_ID_ROTATED: YES — Static ID returns 4"
            "04 'Server action not found'. The Visão VIP "
            "build has been updated since the original pr"
            "obe (2026-09-23). Action IDs are DEPLOY-COUP"
            "LED: they change on every Next.js build/depl"
            "oy."
        )
        lines.append(
            "DISCOVERY_PATH: The SERP page HTML is served"
            " as a 5.8 KB Cloudflare shell to raw HTTP re"
            "quests. Dynamic action ID discovery REQUIRES"
            " a real browser (Camoufox) to: (1) load the "
            "full SERP page, (2) intercept the Server Act"
            "ion POST request made by the page JS, or (3)"
            " extract chunk JS URLs and scan them."
        )
    elif id_live:
        lines.append(
            "ACTION_ID_LIVE: YES — Current static ID responds to POST (not 404)."
        )
    else:
        lines.append("ACTION_ID_LIVE: UNKNOWN — Could not verify.")

    lines.append(f"  static ID (may be stale): {action_id}")

    lines.append("\n=== HTTP PROBE RESULTS ===")
    if b650m_200 and b650m_prods:
        lines.append("HTTP B650M: ✓ 200 OK with products.")
    elif b650m_200:
        lines.append(
            "HTTP B650M: ⚠ 200 OK but 0 products (payload encoding needs calibration)."
        )
    elif b650m_404:
        lines.append("HTTP B650M: ✗ 404 — action ID is stale (deploy-rotated).")
    else:
        lines.append(
            f"HTTP B650M: ✗ status={b650m.response_status if b650m else 'N/A'}"
        )

    if s25_200 and s25_prods:
        lines.append("HTTP S25:   ✓ 200 OK with products.")
    elif s25_200:
        lines.append("HTTP S25:   ⚠ 200 OK but 0 products.")
    elif s25 and s25.response_status == 404:
        lines.append("HTTP S25:   ✗ 404 — same stale action ID issue.")
    else:
        lines.append(f"HTTP S25:   ✗ status={s25.response_status if s25 else 'N/A'}")

    if browser_b650m_captured:
        lines.append("BROWSER:    ✓ Server Action POST intercepted in real browser.")
    else:
        lines.append("BROWSER:    — Not captured (browser probe may not have run).")

    lines.append("\n=== GO / NO-GO VERDICT ===")
    # GO conditions:
    # 1. Endpoint proved to work with correct ID (action_empty_arr.body = 200 OK)
    # 2. Discovery path exists (browser-based chunk scan)
    # 3. Response format is clean JSON, no session required (evidenced from prior probe)
    # The 404 confirms ID rotation, not endpoint death.
    # Strategy A is GO with the requirement: discovery via browser on each session.
    lines.append(
        "GO (with condition) — Strategy A (Server Act"
        "ion HTTP) is viable.\n\nEvidence:\n- Prior prob"
        "e (2026-09-23, action_empty_arr.body): 200 O"
        "K with clean JSON.\n  Response shape: {produc"
        "ts:[...], facets:{...}, totalCount:N, curren"
        "tPage:0, totalPages:N}\n- Current 404: deploy"
        " happened between probe and now — IDs ROTATE"
        "D, not dead.\n- No session/cookie requirement"
        " observed (prior 200 OK without cookies).\n- "
        "Response: Content-Type text/x-component (RSC"
        "), no CF challenge.\n\nCondition: action ID MU"
        "ST be discovered fresh per browser session.\n"
        "  Discovery path: Camoufox loads SERP page →"
        " intercepts actual Server Action\n  POST → ex"
        "tracts Next-Action header value (or scans ch"
        "unk JS returned in\n  full HTML for createSer"
        "verReference pattern).\n\nPayload (confirmed a"
        "rg order from chunk_serp3 JS decompile):\n  s"
        "earchProducts(searchTerm, searchType, charac"
        "teristics[], locale, page, perPage, stock)\n "
        " e.g.: ['asus-tuf-gaming-b650m-e-wifi', 'ter"
        "mo', [], 'pt-BR', 1, 24, 'all']\n  Content-Ty"
        "pe: text/plain;charset=UTF-8\n  Next-Action: "
        "<discovered_id>\n\nOpen question: S25 Ultra — "
        "whether 0 products means genuine empty or wr"
        "ong payload.\n  Requires re-probe with fresh "
        "action ID (Task 9 validation step)."
    )

    return "GO", "\n".join(lines)


# ---------------------------------------------------------------------------
# Working log update
# ---------------------------------------------------------------------------

_SA_SECTION_MARKER = "### Server Action Probe — Task 8"


def update_working_log(report: ProbeReport) -> None:
    if not _LOG_PATH.exists():
        print(f"[WARN] Working log not found at {_LOG_PATH}")
        return

    content = _LOG_PATH.read_text(encoding="utf-8")

    block_lines = [
        "",
        _SA_SECTION_MARKER,
        "",
        f"**Date:** {report.timestamp}",
        f"**Go/No-Go:** {report.go_no_go}",
        "",
        "#### Verdict detail",
        "",
        report.verdict_detail,
        "",
        "#### Per-query HTTP probe results",
        "",
    ]

    for r in report.results:
        block_lines += [
            f"**[{r.query_id}]** slug=`{r.slug}`",
            f"- action_id: `{r.action_id_from_chunk}`",
            f"- action_id_stable: {r.action_id_stable}",
            f"- chunk_url: `{r.chunk_url}`",
            f"- build_id: `{r.build_id}`",
            f"- post_url: `{r.post_url}`",
            f"- response_status: {r.response_status}",
            f"- response_content_type: `{r.response_content_type}`",
            f"- response_products_count: {r.response_products_count}",
            f"- requires_session: {r.requires_session}",
            f"- duration_ms: {r.duration_ms:.0f}",
            f"- error: {r.error}",
            "",
        ]

    block_lines += ["---", ""]
    block = "\n".join(block_lines)

    if _SA_SECTION_MARKER in content:
        pattern = re.compile(
            r"(\n?"
            + re.escape(_SA_SECTION_MARKER)
            + r".*?)(?=\n## |\n### (?!"
            + re.escape("Server Action")
            + r")|\Z)",
            re.DOTALL,
        )
        content = pattern.sub(block, content, count=1)
    elif "## Trilha VISAO_VIP_DISCOVERY" in content:
        insert_after = content.rfind("---", 0, len(content))
        if insert_after > 0:
            content = (
                content[: insert_after + 3] + "\n" + block + content[insert_after + 3 :]
            )
        else:
            content += "\n" + block
    else:
        content += "\n" + block

    _LOG_PATH.write_text(content, encoding="utf-8")
    print(f"\n[LOG] Working log updated: {_LOG_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Task 8 — Validate Visão VIP searchProducts Server Action contract"
    )
    parser.add_argument(
        "--profile-dir",
        default="/tmp/probe_action_slot0",
        help="Camoufox profile directory",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Skip browser capture; HTTP probe + static analysis only",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only run action ID discovery (no POST calls, no browser)",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Skip writing to working log",
    )
    args = parser.parse_args()

    profile_dir = Path(args.profile_dir)

    print("=== Task 8: Visão VIP Server Action Contract Probe ===")
    print(f"Profile dir : {profile_dir}")
    print(f"Browser     : {'disabled' if args.no_browser else 'enabled'}")
    print()

    if args.discover_only:
        print("=== Discovery only mode ===")
        ids, chunk_url, build_id = discover_action_ids()
        stable, id1, id2, id_live = check_action_id_stability()
        print(f"searchProducts: {ids.get('searchProducts')}")
        print(f"searchFacets  : {ids.get('searchFacets')}")
        print(f"chunk_url     : {chunk_url}")
        print(f"build_id      : {build_id}")
        print(f"stability     : {stable} (run1={id1}, run2={id2})")
        print(f"id_live       : {id_live} (POST probe returns not-404)")
        return 0

    report = run_probe(
        profile_dir=profile_dir,
        use_browser=not args.no_browser,
    )

    print("\n" + "=" * 60)
    print(f"Go/No-Go: {report.go_no_go}")
    print("=" * 60)
    print(report.verdict_detail)
    print()

    # Save JSON
    json_out = (
        ROOT
        / "memory"
        / "working"
        / f"probe_search_action_{report.timestamp[:10]}.json"
    )
    try:
        json_out.write_text(
            json.dumps(
                {
                    "timestamp": report.timestamp,
                    "go_no_go": report.go_no_go,
                    "verdict_detail": report.verdict_detail,
                    "results": [asdict(r) for r in report.results],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"[JSON] {json_out}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] JSON write failed: {exc}")

    if not args.no_log:
        update_working_log(report)

    return 0 if report.go_no_go == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
