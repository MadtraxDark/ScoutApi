"""Shared Amazon SERP parsing for regional search adapters."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.services.html_fetcher import (
    is_amazon_robot_check,
    is_amazon_soft_error_page,
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.matching.search_adapters.base import EmptySearchClassification
from scout_api.modules.matching.search_candidate import SearchCandidate

_AMAZON_GENUINE_EMPTY_MARKERS = (
    "nenhum resultado",
    "não encontramos",
    "nao encontramos",
    "no results for",
    "did not match any products",
    "0 results for",
)

_ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
_DATA_ASIN_RE = re.compile(r'data-asin=["\']([A-Z0-9]{10})["\']', re.I)

AmazonSerpClassification = Literal[
    "valid",
    "genuine_empty",
    "challenge",
    "soft_error",
    "incomplete",
    "parse_error",
    "unknown",
]

_CARD_SELECTORS = (
    "div[data-component-type='s-search-result']",
    "div.s-result-item[data-asin]",
)

# Broader fallback only when classic cards yield nothing.
_FALLBACK_ASIN_SELECTORS = (
    "[data-asin]",
)

_TITLE_SELECTORS = (
    "h2 a span::text",
    "h2 span.a-text-normal::text",
    "h2 span::text",
    "h2::attr(aria-label)",
    "a.a-link-normal.s-line-clamp-2 span::text",
    "span.a-size-medium.a-color-base.a-text-normal::text",
    "span.a-size-base-plus.a-color-base.a-text-normal::text",
    "h2 a::text",
)

_HREF_SELECTORS = (
    "h2 a::attr(href)",
    "a.a-link-normal.s-no-outline::attr(href)",
    "a[href*='/dp/']::attr(href)",
)


def _page_title(response: Response) -> str | None:
    raw = response.css("title::text").get()
    if not raw:
        return None
    return re.sub(r"\s+", " ", raw).strip() or None


def _asin_ok(asin: str | None) -> str | None:
    value = (asin or "").strip().upper()
    if len(value) == 10 and re.fullmatch(r"[A-Z0-9]{10}", value):
        return value
    return None


def _clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    # Sponsored aria-label prefix (pt-BR / en).
    for prefix in (
        "anúncio patrocinado – ",
        "anuncio patrocinado – ",
        "sponsored ad – ",
        "sponsored – ",
    ):
        if text.casefold().startswith(prefix):
            text = text[len(prefix) :].strip()
    return text or None


def _card_title(card) -> str | None:
    parts = [
        part.strip()
        for part in card.css(", ".join(_TITLE_SELECTORS)).getall()
        if part and part.strip()
    ]
    # Prefer the longest non-empty fragment (aria-label often duplicates span).
    if not parts:
        return None
    parts.sort(key=len, reverse=True)
    return _clean_title(parts[0])


def _canonical_dp_url(*, host: str, asin: str, href: str | None, response_url: str) -> str:
    """Prefer stable /dp/{ASIN}; unwrap sspa click URLs when present."""
    origin = f"https://www.{host}"
    if href:
        joined = urljoin(response_url, href.strip())
        match = _ASIN_RE.search(joined)
        if match and _asin_ok(match.group(1)) == asin:
            # Keep path slug when the href already contains /dp/{asin}.
            if f"/dp/{asin}" in joined or f"/dp/{asin.lower()}" in joined.casefold():
                return joined.split("?", 1)[0] if "/sspa/click" not in joined else (
                    f"{origin}/dp/{asin}"
                )
        # Encoded sspa target: ...url=%2F...%2Fdp%2FASIN...
        encoded = re.search(
            rf"%2Fdp%2F{re.escape(asin)}(?:%2F|$)", href, flags=re.I
        )
        if encoded or "/sspa/click" in joined:
            return f"{origin}/dp/{asin}"
        if match:
            return joined
    return f"{origin}/dp/{asin}"


def _append_candidate(
    *,
    candidates: list[SearchCandidate],
    seen_asins: set[str],
    seen_urls: set[str],
    asin: str,
    absolute: str,
    title: str | None,
    source: str,
    limit: int,
) -> bool:
    """Append when under limit; return True if limit reached."""
    if asin in seen_asins:
        return len(candidates) >= limit
    canonical = canonicalize_url(absolute)
    if canonical in seen_urls:
        return len(candidates) >= limit
    seen_asins.add(asin)
    seen_urls.add(canonical)
    candidates.append(
        SearchCandidate(
            url=absolute,
            title=title.strip() if title else None,
            product_id=asin,
            metadata={"source": source, "asin": asin},
        )
    )
    return len(candidates) >= limit


def parse_amazon_search_results(
    response: Response,
    *,
    host: str,
    source: str,
    limit: int = 10,
) -> list[SearchCandidate]:
    """Parse Amazon SERP into PDP candidates via stable ASIN signals.

    Strategies (in order, dedup by ASIN + canonical URL):

    1. Classic ``s-search-result`` / ``s-result-item`` cards with ``data-asin``
    2. Broader ``[data-asin]`` only if classic cards produced nothing
    3. Bare ``/dp/{ASIN}`` anchors (layout-variant fallback)
    """
    candidates: list[SearchCandidate] = []
    seen_asins: set[str] = set()
    seen_urls: set[str] = set()

    def _ingest_cards(selector: str, *, source_tag: str) -> bool:
        for card in response.css(selector):
            asin = _asin_ok(card.attrib.get("data-asin"))
            if not asin:
                continue
            href = card.css(", ".join(_HREF_SELECTORS)).get()
            absolute = _canonical_dp_url(
                host=host, asin=asin, href=href, response_url=response.url
            )
            if _append_candidate(
                candidates=candidates,
                seen_asins=seen_asins,
                seen_urls=seen_urls,
                asin=asin,
                absolute=absolute,
                title=_card_title(card),
                source=source_tag,
                limit=limit,
            ):
                return True
        return False

    # --- Strategy 1: classic SERP cards only ---
    if _ingest_cards(", ".join(_CARD_SELECTORS), source_tag=source):
        return candidates

    # --- Strategy 2: broader data-asin (layout variants / no classic class) ---
    if not candidates:
        if _ingest_cards(", ".join(_FALLBACK_ASIN_SELECTORS), source_tag=f"{source}-asin"):
            return candidates

    # --- Strategy 3: /dp/ anchors ---
    for anchor in response.css("a[href*='/dp/'], a[href*='/gp/product/']"):
        href = (anchor.attrib.get("href") or "").strip()
        if not href:
            continue
        match = _ASIN_RE.search(href)
        if not match:
            continue
        asin = _asin_ok(match.group(1))
        if not asin:
            continue
        absolute = _canonical_dp_url(
            host=host, asin=asin, href=href, response_url=response.url
        )
        title = _clean_title(
            " ".join(
                part.strip()
                for part in anchor.css("span::text, ::text").getall()
                if part and part.strip()
            )
        )
        if _append_candidate(
            candidates=candidates,
            seen_asins=seen_asins,
            seen_urls=seen_urls,
            asin=asin,
            absolute=absolute,
            title=title,
            source=f"{source}-dp-link",
            limit=limit,
        ):
            break

    return candidates


def _has_result_markers(text: str) -> bool:
    folded = (text or "").casefold()
    if "s-search-result" in folded:
        return True
    if _DATA_ASIN_RE.search(text or ""):
        return True
    if _ASIN_RE.search(text or ""):
        return True
    return False


def classify_amazon_serp_response(response: Response) -> AmazonSerpClassification:
    """Classify Amazon SERP HTML before treating empty candidates as NO_RESULTS.

    ``0 candidates`` alone is never enough — prove the page is a valid SERP first.
    """
    text = response.text or ""
    page_url = str(response.url or "")
    title = _page_title(response)

    if is_amazon_soft_error_page(text, title=title):
        return "soft_error"
    if is_challenge_page(text, title=title) or is_amazon_robot_check(
        text, title=title
    ):
        return "challenge"
    if is_auth_wall_page(text, url=page_url, title=title):
        return "challenge"

    path = page_url.split("?", 1)[0]
    folded = text.casefold()
    genuine_empty = any(marker in folded for marker in _AMAZON_GENUINE_EMPTY_MARKERS)
    has_markers = _has_result_markers(text)

    if genuine_empty and not has_markers:
        return "genuine_empty"

    if "/s" not in path:
        return "unknown"

    if not has_markers:
        # Tiny shells / soft-blocked HTML without ASIN signals.
        if len(text.encode("utf-8", errors="replace")) < 8_000:
            return "incomplete"
        return "incomplete"

    # Markers present — if a caller already failed to parse, map to parse_error
    # via classify_amazon_empty_result (which re-parses lightly).
    sample = parse_amazon_search_results(
        response, host="amazon.com", source="classify", limit=1
    )
    if sample:
        return "valid"
    if genuine_empty:
        return "genuine_empty"
    return "parse_error"


def classify_amazon_empty_result(response: Response) -> EmptySearchClassification:
    """Map SERP classification to the StoreSearch empty-result contract.

    - ``genuine_empty`` → empty list (NO_RESULTS path)
    - ``soft_error`` / ``incomplete`` / ``challenge`` → ``incomplete``
      (``SEARCH_INCOMPLETE_RESPONSE`` / WAF already handled upstream when possible)
    - ``parse_error`` → ``parse_error`` (``SEARCH_PARSE_ERROR``)
    """
    kind = classify_amazon_serp_response(response)
    if kind == "genuine_empty":
        return "genuine_empty"
    if kind == "parse_error":
        return "parse_error"
    if kind in {"soft_error", "incomplete", "challenge"}:
        return "incomplete"
    if kind == "valid":
        # Valid SERP but caller got zero after ranking/filter — treat as genuine.
        return "genuine_empty"
    return "unknown"
