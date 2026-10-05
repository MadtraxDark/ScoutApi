"""Strategy A for Visão VIP: searchProducts Server Action via HTTP POST.

The action ID is deploy-coupled (changes on each Next.js build/deploy).
It MUST be discovered per session via browser intercept or JS chunk scan.
NEVER hardcode a single permanent ID without refresh/discovery.

Contract (confirmed Task 8 probe 2026-09-23):
  POST https://www.visaovip.com/busca/termo/{slug}/
  Headers: Next-Action: <id>, Content-Type: text/plain;charset=UTF-8
  Payload: [searchTerm, "termo", [], "pt-BR", 1, 24, "all"]
  Response: text/x-component RSC with line "1:{products:[...], totalCount:N}"

Unexpected shape → UNAVAILABLE or INVALID_RESPONSE → fallback B (Task 10).
Never treat as NO_RESULTS unless products:[] in a valid response.

References: Task 8 report, ADR 0038.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from enum import StrEnum
from typing import Any

import httpx

from scout_api.modules.matching.search_candidate import SearchCandidate

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.visaovip.com"

# ---------------------------------------------------------------------------
# Process-local action-id cache (deploy-coupled; invalidate on 404)
# ---------------------------------------------------------------------------

_action_id_lock = threading.Lock()
_cached_action_id: str | None = None
_cached_build_id: str | None = None


def get_cached_action_id() -> str | None:
    """Return the process-cached searchProducts action ID, if any."""
    with _action_id_lock:
        return _cached_action_id


def set_cached_action_id(action_id: str, *, build_id: str | None = None) -> None:
    """Store a freshly discovered action ID for subsequent Strategy A calls."""
    global _cached_action_id, _cached_build_id
    cleaned = (action_id or "").strip()
    if not cleaned:
        return
    with _action_id_lock:
        _cached_action_id = cleaned
        if build_id:
            _cached_build_id = build_id
    logger.info(
        "visaovip_action_id_cached",
        extra={
            "action_id_prefix": cleaned[:12],
            "build_id": build_id,
        },
    )


def invalidate_cached_action_id(*, reason: str = "rotated") -> None:
    """Drop cached ID after 404 / UNAVAILABLE so the next call rediscovers."""
    global _cached_action_id, _cached_build_id
    with _action_id_lock:
        had = _cached_action_id is not None
        _cached_action_id = None
        _cached_build_id = None
    if had:
        logger.info("visaovip_action_id_cache_invalidated", extra={"reason": reason})


def reset_action_id_cache_for_tests() -> None:
    """Clear cache between unit tests."""
    invalidate_cached_action_id(reason="test_reset")


def resolve_action_id(
    *,
    bootstrap_id: str | None = None,
    serp_html: str | None = None,
    fetch_chunk_fn: Any | None = None,
) -> str | None:
    """Resolve action ID: bootstrap → process cache → SERP HTML chunk scan.

    ``bootstrap_id`` (optional Settings override) wins when non-empty.
    Discovery from ``serp_html`` requires hydrated HTML (≥10 KB) from Camoufox.
    """
    boot = (bootstrap_id or "").strip()
    if boot:
        set_cached_action_id(boot)
        return boot

    cached = get_cached_action_id()
    if cached:
        return cached

    if serp_html:
        discovered = discover_action_id_from_serp_html(
            serp_html,
            fetch_chunk_fn=fetch_chunk_fn,
        )
        if discovered:
            set_cached_action_id(discovered)
            return discovered
    return None

# RSC payload line: "1:<json>" or "1:E<json>" (server error)
_RSC_DATA_LINE = re.compile(r"^1:(.+)$", re.MULTILINE)

# Extract build_id from RSC preamble line or from the first RSC line
_BUILD_ID_RE = re.compile(r'"b":"([A-Za-z0-9_-]{5,30})"')

# Pattern to extract searchProducts action ID from Next.js chunk JS.
# Handles both call forms (direct and Turbopack indirect):
#   createServerReference("id", ...)
#   createServerReference)("id", ...)  ← (0,x.createServerReference)("id",...)
_ACTION_ID_RE = re.compile(
    r'createServerReference\)?\("([0-9a-f]{40,})"[^,]*,[^,]+,void 0,'
    r'[^,]+findSourceMapURL,"searchProducts"\)'
)

# Chunk script URLs in Next.js hydrated HTML
_CHUNK_SCRIPT_RE = re.compile(r'src="(/_next/static/chunks/[0-9a-f]{16}\.js)"')


# ---------------------------------------------------------------------------
# StrategyResult enum
# ---------------------------------------------------------------------------


class StrategyResult(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"       # action ID rotated / 404 / contract missing
    INVALID_RESPONSE = "invalid_response"  # unexpected JSON shape
    ERROR = "error"                   # network / unexpected exception


# ---------------------------------------------------------------------------
# RSC response parsing (pure, testable)
# ---------------------------------------------------------------------------


def parse_rsc_response(
    body: str,
    slug: str = "",
) -> tuple[StrategyResult, list[SearchCandidate] | None, str | None]:
    """Parse a Next.js RSC (text/x-component) response from searchProducts.

    Expected body:
        0:{"a":"$@1","f":"","b":"<buildId>","q":"","i":false}
        1:{"products":[...],"facets":{...},"totalCount":N,...}

    Returns:
        (StrategyResult, candidates_or_None, build_id_or_None)

    IMPORTANT contract:
        - UNAVAILABLE: empty/missing body (cannot determine if action exists)
        - BLOCKED: server returned ``1:E{...}`` (rotated ID side-effect or 5xx)
        - INVALID_RESPONSE: valid RSC but wrong shape (missing ``products`` key)
        - NO_RESULTS: ``products:[]`` in otherwise valid response
        - SUCCESS: ``products`` is non-empty list
    """
    if not body or not body.strip():
        return StrategyResult.UNAVAILABLE, None, None

    # Extract build_id from preamble
    build_id: str | None = None
    build_m = _BUILD_ID_RE.search(body)
    if build_m:
        build_id = build_m.group(1)

    # Locate the payload line (starts with "1:")
    data_m = _RSC_DATA_LINE.search(body)
    if not data_m:
        return StrategyResult.INVALID_RESPONSE, None, build_id

    payload_str = data_m.group(1).strip()

    # Server error response: "1:E{...}"
    if payload_str.startswith("E"):
        return StrategyResult.BLOCKED, None, build_id

    # Parse JSON payload
    try:
        payload: Any = json.loads(payload_str)
    except json.JSONDecodeError:
        return StrategyResult.INVALID_RESPONSE, None, build_id

    # Validate contract shape
    if not isinstance(payload, dict) or "products" not in payload:
        return StrategyResult.INVALID_RESPONSE, None, build_id

    products = payload["products"]
    if not isinstance(products, list):
        return StrategyResult.INVALID_RESPONSE, None, build_id

    if not products:
        return StrategyResult.NO_RESULTS, [], build_id

    candidates = _products_to_candidates(products, slug=slug)
    return StrategyResult.SUCCESS, candidates, build_id


# ---------------------------------------------------------------------------
# Product → SearchCandidate conversion
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Simple slug: lowercase, spaces/special chars → hyphens."""
    text = (text or "").strip().lower()
    text = re.sub(r"[\s_/]+", "-", text)
    text = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "produto"


def _products_to_candidates(
    products: list[Any],
    *,
    slug: str = "",
) -> list[SearchCandidate]:
    """Convert product dicts from the action response to SearchCandidates.

    Product shape (from chunk_serp3.js ProductCard, Task 8):
        sku          int    — visaovip product ID (last URL segment)
        description  str    — product name / title
        category     str    — category name (used for category slug in URL)
        imageUrl     str    — CDN image URL
        price        float  — price in USD
        isAvailable  bool
        isPromotion  bool
        isGamer      bool
        isHighlight  bool
    """
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()

    for product in products:
        if not isinstance(product, dict):
            continue

        sku = product.get("sku")
        if sku is None:
            continue

        product_id = str(sku)
        if product_id in seen:
            continue
        seen.add(product_id)

        description: str = product.get("description") or product.get("name") or ""
        category: str = product.get("category") or ""

        category_slug = _slugify(category) if category else "produtos"
        desc_slug = _slugify(description) if description else product_id

        url = f"{_BASE_URL}/prod/{category_slug}/{desc_slug}/{product_id}/"
        title = description.strip() if description else None

        candidates.append(
            SearchCandidate(
                url=url,
                title=title,
                product_id=product_id,
                metadata={"source": "visaovip-action"},
            )
        )
        if len(candidates) >= 20:
            break

    return candidates


# ---------------------------------------------------------------------------
# HTTP POST call
# ---------------------------------------------------------------------------


def call_search_products(
    slug: str,
    action_id: str,
    *,
    timeout: float = 20.0,
    post_fn: Any | None = None,
) -> tuple[StrategyResult, list[SearchCandidate] | None]:
    """HTTP POST to the searchProducts Server Action.

    Args:
        slug: URL slug for the search term (e.g. "asus-tuf-gaming-b650m-e-wifi").
        action_id: Discovered Next-Action header value.
                   MUST be fresh per session — deploy-coupled, changes on each build.
        timeout: HTTP timeout in seconds (httpx path).
        post_fn: Optional ``(url, headers, data) -> (status, text)``. Prefer
            Camoufox ``browser_post`` so Cloudflare clearance cookies apply;
            bare httpx is often 403'd.

    Returns:
        (StrategyResult, candidates_or_None)
    """
    post_url = f"{_BASE_URL}/busca/termo/{slug}/"
    payload_bytes = json.dumps(
        [slug, "termo", [], "pt-BR", 1, 24, "all"],
        ensure_ascii=False,
    ).encode("utf-8")

    headers = {
        "Next-Action": action_id,
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "text/x-component,*/*",
        "x-intlayer-locale": "pt-BR",
        "Origin": _BASE_URL,
        "Referer": post_url,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    try:
        if post_fn is not None:
            status, body = post_fn(post_url, headers, payload_bytes)
        else:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                resp = client.post(post_url, content=payload_bytes, headers=headers)
            status = int(resp.status_code)
            body = resp.text or ""
    except Exception as exc:
        logger.warning(
            "visaovip_action_post_failed",
            extra={"slug": slug, "err": str(exc)},
            exc_info=True,
        )
        return StrategyResult.ERROR, None

    if status == 404:
        logger.info(
            "visaovip_action_id_rotated",
            extra={"slug": slug, "action_id_prefix": action_id[:12]},
        )
        return StrategyResult.UNAVAILABLE, None

    if status == 403 or (
        status == 200 and "attention required" in (body or "").casefold()
    ):
        return StrategyResult.BLOCKED, None

    if status not in (200,):
        return StrategyResult.BLOCKED, None

    result, candidates, _ = parse_rsc_response(body, slug=slug)
    return result, candidates


# ---------------------------------------------------------------------------
# Action ID discovery from JS chunk
# ---------------------------------------------------------------------------


def discover_action_id_from_chunk_js(chunk_text: str) -> str | None:
    """Extract the searchProducts action ID from a Next.js chunk JS string.

    The deploy-coupled ID is embedded as:
        createServerReference("<hex40+>", ..., "searchProducts")

    Action IDs MUST be refreshed per session — never cache across sessions.

    Args:
        chunk_text: Full text content of a Next.js JS chunk file.

    Returns:
        Action ID hex string, or None if not found.
    """
    m = _ACTION_ID_RE.search(chunk_text)
    return m.group(1) if m else None


def discover_action_id_from_serp_html(
    html: str,
    *,
    fetch_chunk_fn: Any | None = None,
    base_url: str = _BASE_URL,
) -> str | None:
    """Attempt to extract the searchProducts action ID from hydrated SERP HTML.

    Scans <script src="/_next/static/chunks/..."> tags, fetches each chunk,
    and looks for the createServerReference pattern.

    NOTE: Visão VIP SERP returns a ~5.8 KB Cloudflare shell for plain HTTP.
    This function only works when ``html`` is a fully hydrated page (≥ 20 KB),
    which requires Camoufox. For browser-based discovery, use the page request
    intercept approach instead (capture Next-Action header directly).

    Args:
        html: Full SERP HTML (hydrated, from Camoufox).
        fetch_chunk_fn: Optional callable(url: str) -> str for fetching JS chunks.
            Defaults to synchronous httpx GET.
        base_url: Base URL for constructing chunk URLs.

    Returns:
        Action ID string or None.
    """
    if len(html) < 10_000:
        return None

    chunk_srcs = _CHUNK_SCRIPT_RE.findall(html)
    if not chunk_srcs:
        return None

    if fetch_chunk_fn is None:

        def fetch_chunk_fn(url: str) -> str:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(url)
                return r.text if r.status_code == 200 else ""

    for src in chunk_srcs:
        chunk_url = f"{base_url}{src}"
        try:
            chunk_text = fetch_chunk_fn(chunk_url)
            action_id = discover_action_id_from_chunk_js(chunk_text)
            if action_id:
                logger.info(
                    "visaovip_action_id_discovered_from_chunk",
                    extra={
                        "chunk_url": chunk_url,
                        "action_id_prefix": action_id[:12],
                    },
                )
                return action_id
        except Exception as exc:
            logger.debug(
                "visaovip_chunk_fetch_failed",
                extra={"url": chunk_url, "err": str(exc)},
            )

    return None
