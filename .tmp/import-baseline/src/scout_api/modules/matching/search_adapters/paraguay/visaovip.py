"""Visão VIP Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urljoin, urlparse

from scrapy.http import Response

from scout_api.modules.crawler.core.exceptions import ParseError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_adapters.paraguay import (
    visaovip_action_strategy as _vv_action,  # strategy A helpers
)
from scout_api.modules.matching.search_adapters.paraguay.visaovip_action_strategy import (  # noqa: E501
    StrategyResult,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_PRODUCT_PATH_ID = re.compile(r"/prod/.+/(\d+)/?$", re.IGNORECASE)
_SERP_TITLE_CUT = re.compile(
    r"\b(?:U\$|G\$|R\$|Código:|Codigo:)",
    re.IGNORECASE,
)
_SERP_CATEGORY_GLUE = re.compile(
    r"(?<=[a-z0-9/])\s+(?="
    r"Placas?\s|Notebooks?\s|Processadores?\s|Mem[oó]rias?\s|"
    r"SSDs?\s|HDs?\s|Smartphones?\s|Celulares?\s"
    r")",
    re.IGNORECASE,
)


class VisaoVipSearchAdapter:
    """Candidate discovery for visaovip.com `/busca/termo/{slug}/`."""

    store_key: ClassVar[str] = "visaovip"

    def build_search_request(self, query: str) -> SearchRequest:
        """Next.js term search: spaces → hyphens. Prefer Camoufox (HTTP 403/shell)."""
        slug = self._search_term_slug(query)
        if not slug:
            raise ParseError("Query de busca Visão VIP vazia")
        return SearchRequest(
            url=f"https://www.visaovip.com/busca/termo/{slug}/",
            method="GET",
            prefer_browser=True,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        """Extract PDP cards from hydrated SERP HTML (Camoufox settles RSC)."""
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for link in response.css("a[href*='/prod/']"):
            href = link.attrib.get("href") or link.css("::attr(href)").get()
            if not href or not str(href).strip():
                continue
            absolute = urljoin(response.url, str(href).strip())
            path = (urlparse(absolute).path or "").rstrip("/") + "/"
            match = _PRODUCT_PATH_ID.search(path)
            if not match:
                continue
            if re.search(r"/\d+/\d+\.(?:jpe?g|png|webp|gif)$", path, re.I):
                continue
            if re.search(r"/\d+/\d+/?$", path):
                continue
            product_id = match.group(1)
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            title_bits = [
                t.strip() for t in link.css("::text").getall() if t and t.strip()
            ]
            title = self._clean_serp_title(" ".join(title_bits))
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    title=title,
                    product_id=product_id,
                    metadata={"source": "visaovip-search"},
                )
            )
            if len(candidates) >= 20:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        """Empty hydrated SERP without /prod/ cards is incomplete, not NO_MATCH."""
        page_url = str(response.url or "")
        text = response.text or ""
        if "/busca/termo/" not in page_url.casefold():
            return "unknown"
        folded = text.casefold()
        genuine_empty = any(
            marker in folded
            for marker in (
                "nenhum resultado",
                "não encontramos",
                "nao encontramos",
                "no results",
                "sin resultados",
                "0 resultados",
            )
        )
        if genuine_empty:
            return "genuine_empty"
        if "/prod/" not in folded:
            return "incomplete"
        return "unknown"

    # ------------------------------------------------------------------
    # Strategy A — searchProducts Server Action (HTTP POST)
    # ------------------------------------------------------------------

    def try_strategy_a(
        self,
        query: str,
        *,
        action_id: str,
        enabled: bool = False,
        timeout: float = 20.0,
        post_fn: object | None = None,
    ) -> tuple[StrategyResult, list[SearchCandidate] | None]:
        """Attempt candidate discovery via the searchProducts Server Action.

        Args:
            query: Search query string.
            action_id: Fresh Next-Action header value (deploy-coupled;
                       must be discovered per session via browser intercept).
            enabled: Feature gate — must be True to attempt the POST.
                     Default False (flag VISAOVIP_SEARCH_ACTION_ENABLED).
            timeout: HTTP timeout in seconds (httpx fallback path).
            post_fn: Optional browser-backed POST ``(url, headers, data) ->
                     (status, text)`` so Cloudflare clearance cookies apply.

        Returns:
            (StrategyResult, candidates_or_None).
            UNAVAILABLE if disabled.
            INVALID_RESPONSE / BLOCKED → caller should fall back to Strategy B.
        """
        if not enabled:
            return StrategyResult.UNAVAILABLE, None

        slug = self._search_term_slug(query)
        if not slug:
            return StrategyResult.UNAVAILABLE, None

        return _vv_action.call_search_products(
            slug,
            action_id,
            timeout=timeout,
            post_fn=post_fn,
        )

    @staticmethod
    def _search_term_slug(query: str) -> str:
        text = (query or "").strip()
        if not text:
            return ""
        text = re.sub(r"[\s_/]+", "-", text)
        text = re.sub(r"[^\w\-]+", "-", text, flags=re.UNICODE)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text

    @staticmethod
    def _clean_serp_title(raw: str | None) -> str | None:
        if not raw:
            return None
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            return None
        cut = _SERP_TITLE_CUT.search(text)
        if cut and cut.start() > 8:
            text = text[: cut.start()].strip(" -/|")
        glue = _SERP_CATEGORY_GLUE.search(text)
        if glue and glue.start() > 8:
            text = text[: glue.start()].strip(" -/|")
        return text or None
