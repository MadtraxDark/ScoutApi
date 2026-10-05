"""KaBuM Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar
from urllib.parse import quote_plus, urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate


class KabumSearchAdapter:
    """Candidate discovery for kabum.com.br ``/busca/{query}``."""

    store_key: ClassVar[str] = "kabum"

    def build_search_request(self, query: str) -> SearchRequest:
        return SearchRequest(
            url=f"https://www.kabum.com.br/busca/{quote_plus(query.strip())}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        # Modern KaBuM SERP is Next.js: catalog cards live in __NEXT_DATA__
        # (anchors with /produto/ are often absent from the initial HTML).
        from_state = self._parse_search_next_data(response)
        if from_state:
            return from_state
        return self._parse_search_anchor_fallback(response)

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    def _parse_search_next_data(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for raw in response.css("script#__NEXT_DATA__::text").getall():
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                continue
            props = state.get("props", {}) if isinstance(state, dict) else {}
            page_props = props.get("pageProps", {}) if isinstance(props, dict) else {}
            data = page_props.get("data", {}) if isinstance(page_props, dict) else {}
            catalog = (
                data.get("catalogServer", {}) if isinstance(data, dict) else {}
            )
            rows = catalog.get("data") if isinstance(catalog, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = row.get("code")
                if code is None or str(code).strip() == "":
                    continue
                product_id = str(code).strip()
                slug = (
                    self._string(row.get("friendlyName"))
                    or self._string(row.get("name"))
                    or product_id
                )
                if slug:
                    slug = re.sub(r"\s+", "-", slug.strip().casefold())
                    slug = re.sub(r"[^a-z0-9\-]+", "", slug)
                path = (
                    f"/produto/{product_id}/{slug}"
                    if slug
                    else f"/produto/{product_id}"
                )
                absolute = urljoin(response.url, path)
                canonical = canonicalize_url(absolute)
                if canonical in seen:
                    continue
                seen.add(canonical)
                candidates.append(
                    SearchCandidate(
                        url=absolute,
                        title=self._string(row.get("name")),
                        product_id=product_id,
                        metadata={"source": "kabum-search-next-data"},
                    )
                )
                if len(candidates) >= 10:
                    return candidates
        return candidates

    def _parse_search_anchor_fallback(
        self, response: Response
    ) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        for href in response.css(
            "a.productLink::attr(href), "
            "a[href*='/produto/']::attr(href), "
            "main a[href*='/produto/']::attr(href)"
        ).getall():
            absolute = urljoin(response.url, href.strip())
            if "/produto/" not in absolute:
                continue
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            product_id = None
            match = re.search(r"/produto/(\d+)", absolute)
            if match:
                product_id = match.group(1)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    product_id=product_id,
                    metadata={"source": "kabum-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    @staticmethod
    def _string(value: Any) -> str | None:
        return str(value).strip() if value is not None and str(value).strip() else None
