"""Pichau Store Search adapter — SERP only (not PDP)."""

from __future__ import annotations

import json
import re
from typing import ClassVar
from urllib.parse import quote_plus, urljoin

from scrapy.http import Response

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.search_adapters.base import (
    EmptySearchClassification,
    SearchRequest,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

_NEXT_FLIGHT_PUSH = re.compile(
    r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)",
    re.DOTALL,
)


class PichauSearchAdapter:
    """Candidate discovery for pichau.com.br ``/search?q=…``."""

    store_key: ClassVar[str] = "pichau"

    def build_search_request(self, query: str) -> SearchRequest:
        return SearchRequest(
            url=f"https://www.pichau.com.br/search?q={quote_plus(query.strip())}",
            method="GET",
            prefer_browser=False,
        )

    def parse_candidates(self, response: Response) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        seen: set[str] = set()
        skip_segments = {
            "search",
            "favorites",
            "favoritos",
            "cart",
            "checkout",
            "customer",
            "account",
            "login",
            "cadastro",
            "wishlist",
            "blog",
            "central",
            "atendimento",
            "institucional",
            "categoria",
            "promocao",
            "promocoes",
            "politica-de-privacidade",
        }

        slugs: list[str] = []
        flight = self._flight_blob(response.text or "")
        if flight:
            slugs.extend(re.findall(r'"url_key"\s*:\s*"([^"]+)"', flight))
        # Also accept real anchors when present (rare on SSR SERP).
        for href in response.css("a[href]::attr(href)").getall():
            absolute = urljoin(response.url, (href or "").strip())
            path = absolute.split("?", 1)[0]
            parts = [p for p in path.split("/") if p and "pichau.com.br" not in p]
            if len(parts) == 1:
                slugs.append(parts[0])

        for slug in slugs:
            slug_clean = (slug or "").strip().strip("/")
            if not slug_clean:
                continue
            fold = slug_clean.casefold()
            if fold in skip_segments:
                continue
            if fold.count("-") < 2 or len(fold) < 16:
                continue
            absolute = f"https://www.pichau.com.br/{slug_clean}"
            canonical = canonicalize_url(absolute)
            if canonical in seen:
                continue
            seen.add(canonical)
            candidates.append(
                SearchCandidate(
                    url=absolute,
                    metadata={"source": "pichau-search"},
                )
            )
            if len(candidates) >= 10:
                break
        return candidates

    def classify_empty_result(self, response: Response) -> EmptySearchClassification:
        return "unknown"

    @classmethod
    def _flight_blob(cls, html: str) -> str:
        chunks: list[str] = []
        for match in _NEXT_FLIGHT_PUSH.finditer(html):
            chunks.append(cls._decode_js_string(match.group(1)))
        return "\n".join(chunks)

    @staticmethod
    def _decode_js_string(body: str) -> str:
        try:
            decoded = json.loads(f'"{body}"')
        except json.JSONDecodeError:
            return (
                body.replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\t", "\t")
                .replace(r"\\", "\\")
            )
        return decoded if isinstance(decoded, str) else body
