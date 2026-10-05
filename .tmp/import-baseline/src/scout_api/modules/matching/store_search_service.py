"""Live store SERP discovery via StoreSearchAdapter + shared HtmlFetcher."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from scout_api.core.config import get_settings
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.failure_domains import FailureDomain, log_failure
from scout_api.modules.crawler.core.store_capability_health import (
    CapabilityTrialToken,
    get_store_capability_circuit,
    is_store_search_trip_failure,
    store_capability_unavailable_error,
)
from scout_api.modules.crawler.core.store_capability_health import (
    reset_store_capability_circuits_for_tests as _reset_circuits,  # noqa: F401
)
from scout_api.modules.crawler.services.amazon_http_first_fetcher import (
    is_amazon_store_url,
)
from scout_api.modules.crawler.services.html_fetcher import (
    HtmlFetcher,
    is_amazon_robot_check,
    is_auth_wall_page,
    is_challenge_page,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    get_shared_html_fetcher,
)
from scout_api.modules.crawler.services.store_aware_fetcher import find_browser_post
from scout_api.modules.matching.identity import (
    enrich_candidate_title,
    rank_candidates_for_query,
)
from scout_api.modules.matching.search_adapters.base import SearchRequest
from scout_api.modules.matching.search_adapters.registry import (
    resolve_search_adapter,
    search_adapter_available,
)
from scout_api.modules.matching.search_candidate import SearchCandidate

logger = logging.getLogger(__name__)


def _log_search_candidates(
    store_key: str, query: str, candidates: list[SearchCandidate]
) -> None:
    """Log bounded, non-query-string candidate details for live diagnostics."""
    details = []
    for candidate in candidates:
        parts = urlsplit(candidate.url)
        safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        details.append(
            (
                candidate.product_id,
                (candidate.title or "")[:200],
                safe_url[:500],
            )
        )
    logger.info(
        "store_search_candidates store=%s query=%r count=%d candidates=%r",
        store_key,
        query,
        len(candidates),
        details,
    )


def _dedup_candidates_by_product_id(
    candidates: list[SearchCandidate],
    *,
    limit: int,
) -> list[SearchCandidate]:
    """Deduplicate SearchCandidates by product_id (first occurrence wins)."""
    seen: set[str] = set()
    result: list[SearchCandidate] = []
    for c in candidates:
        key = (c.product_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(c)
        if len(result) >= limit:
            break
    return result


class StoreSearchService:
    """Fetch and parse store search pages for product matching candidates."""

    def __init__(self, fetcher: HtmlFetcher | None = None) -> None:
        self._fetcher = fetcher or get_shared_html_fetcher()

    def search(
        self,
        store_key: str,
        query: str,
        *,
        limit: int = 5,
        on_browser_nav_used: Callable[[], object] | None = None,
    ) -> list[SearchCandidate]:
        adapter = resolve_search_adapter(store_key)
        request = adapter.build_search_request(query)
        fetch_url = request.url
        logger.info(
            "store_search_fetch store=%s query=%r method=%s prefer_browser=%s url=%s",
            store_key,
            query,
            request.method,
            request.prefer_browser,
            fetch_url,
            extra={
                "store": store_key,
                "capability": "search",
                "operation": "fetch_serp",
                "prefer_browser": request.prefer_browser,
                "method": request.method,
                "url": fetch_url,
            },
        )

        # ---------------------------------------------------------------
        # Store + capability circuit check (Phase 11)
        # ---------------------------------------------------------------
        settings = get_settings()
        trial_token: CapabilityTrialToken | None = None
        if settings.store_capability_circuit_enabled:
            circuit = get_store_capability_circuit(store_key, "search")
            if not circuit.allow():
                trial_token = circuit.claim_trial()
                if trial_token is None:
                    log_failure(
                        FailureDomain.STORE_CAPABILITY,
                        event="store_search_circuit_open_skip",
                        store=store_key,
                        capability="search",
                    )
                    raise store_capability_unavailable_error(
                        store_key, "search", url=fetch_url
                    )
                logger.info(
                    "store_search_circuit_probe_start",
                    extra={
                        "store": store_key,
                        "capability": "search",
                        "token_id": trial_token.token_id,
                    },
                )

        try:
            result = self._search_inner(
                store_key,
                query,
                limit=limit,
                fetch_url=fetch_url,
                request=request,
                on_browser_nav_used=on_browser_nav_used,
            )
        except BaseException as exc:
            if settings.store_capability_circuit_enabled:
                circuit = get_store_capability_circuit(store_key, "search")
                if is_store_search_trip_failure(exc):
                    if trial_token is not None:
                        circuit.complete_trial(trial_token, success=False)
                    else:
                        circuit.record_failure()
                    log_failure(
                        FailureDomain.STORE_CAPABILITY,
                        event="store_search_circuit_failure",
                        store=store_key,
                        capability="search",
                        exc=exc,
                        extra=circuit.snapshot(),
                    )
                elif trial_token is not None:
                    # Non-tripping error during probe — release trial without success.
                    circuit.complete_trial(trial_token, success=False)
            raise
        else:
            if settings.store_capability_circuit_enabled:
                circuit = get_store_capability_circuit(store_key, "search")
                if trial_token is not None:
                    circuit.complete_trial(trial_token, success=True)
                else:
                    circuit.record_success()
        return result

    def _search_inner(
        self,
        store_key: str,
        query: str,
        *,
        limit: int,
        fetch_url: str,
        request: SearchRequest,
        on_browser_nav_used: Callable[[], object] | None,
    ) -> list[SearchCandidate]:
        adapter = resolve_search_adapter(store_key)

        # ---------------------------------------------------------------
        # Strategy A→B chain (Visão VIP — adapters with try_strategy_a)
        # Action ID is deploy-coupled: Settings bootstrap OR process cache
        # OR discover from Camoufox-hydrated SERP HTML (chunk scan).
        # ---------------------------------------------------------------
        discovery_response = None
        if hasattr(adapter, "try_strategy_a"):
            settings = get_settings()
            if settings.visaovip_search_action_enabled:
                from scout_api.modules.matching.search_adapters.paraguay import (
                    visaovip_action_strategy as vv_action,
                )

                action_id = vv_action.resolve_action_id(
                    bootstrap_id=settings.visaovip_search_action_id or None,
                )
                if not action_id:
                    logger.info(
                        "visaovip_action_id_discovery_fetch",
                        extra={"store": store_key, "url": fetch_url},
                    )
                    discovery_response = self._fetcher.fetch(fetch_url)
                    if on_browser_nav_used is not None and request.prefer_browser:
                        on_browser_nav_used()
                    action_id = vv_action.resolve_action_id(
                        serp_html=discovery_response.text or "",
                    )
                    if not action_id:
                        logger.info(
                            "visaovip_action_id_discovery_miss",
                            extra={"store": store_key},
                        )

                if action_id:
                    post_fn: Callable[..., tuple[int, str]] | None = None
                    browser_post = find_browser_post(self._fetcher)
                    if browser_post is not None:

                        def post_fn(
                            post_url: str,
                            headers: dict[str, str],
                            data: bytes,
                        ) -> tuple[int, str]:
                            # Cloudflare clears bare httpx; reuse Camoufox session.
                            return cast(
                                tuple[int, str],
                                browser_post(post_url, headers=headers, data=data),
                            )

                    a_result, a_candidates = adapter.try_strategy_a(
                        query,
                        action_id=action_id,
                        enabled=True,
                        post_fn=post_fn,
                    )
                    logger.info(
                        "visaovip_strategy_a_result store=%s query=%r "
                        "result=%s candidates=%d",
                        store_key,
                        query,
                        str(a_result),
                        len(a_candidates or []),
                        extra={
                            "store": store_key,
                            "result": str(a_result),
                            "candidates": len(a_candidates or []),
                        },
                    )
                    if a_result == vv_action.StrategyResult.SUCCESS and a_candidates:
                        enriched_a = [enrich_candidate_title(c) for c in a_candidates]
                        ranked_a = rank_candidates_for_query(list(enriched_a), query)
                        selected_a = _dedup_candidates_by_product_id(
                            ranked_a, limit=limit
                        )
                        _log_search_candidates(store_key, query, selected_a)
                        return selected_a
                    if a_result == vv_action.StrategyResult.NO_RESULTS:
                        logger.info(
                            "visaovip_strategy_a_no_results store=%s query=%r",
                            store_key,
                            query,
                            extra={"store": store_key, "query": query},
                        )
                        return []
                    if a_result == vv_action.StrategyResult.UNAVAILABLE:
                        vv_action.invalidate_cached_action_id(reason="unavailable")
                    logger.info(
                        "visaovip_strategy_a_fallback_to_b",
                        extra={"store": store_key, "result": str(a_result)},
                    )

        # ---------------------------------------------------------------
        # Strategy B: browser SERP fetch (shared HtmlFetcher / Camoufox)
        # Reuse discovery SERP when we already paid for that navigation.
        # ---------------------------------------------------------------
        if discovery_response is not None:
            response = discovery_response
        else:
            response = self._fetcher.fetch(fetch_url)
            if on_browser_nav_used is not None and request.prefer_browser:
                on_browser_nav_used()

        # Opportunistic cache fill for later queries in this process.
        if hasattr(adapter, "try_strategy_a"):
            settings = get_settings()
            if settings.visaovip_search_action_enabled:
                from scout_api.modules.matching.search_adapters.paraguay import (
                    visaovip_action_strategy as vv_action,
                )

                if not vv_action.get_cached_action_id():
                    if vv_action.resolve_action_id(serp_html=response.text or ""):
                        logger.info(
                            "visaovip_action_id_learned_from_serp_b",
                            extra={"store": store_key},
                        )

        text = response.text or ""
        page_url = str(response.url or fetch_url)
        if is_challenge_page(text) or is_amazon_robot_check(text):
            raise RequestError(
                "Busca bloqueada por WAF/challenge na loja",
                code="UPSTREAM_WAF_BLOCKED",
                url=page_url,
            )
        if is_auth_wall_page(text, url=page_url):
            raise RequestError(
                "Busca bloqueada por parede de autenticação na loja",
                code="AUTH_REQUIRED",
                url=page_url,
            )
        try:
            candidates = adapter.parse_candidates(response)
        except NotImplementedError as exc:
            raise RequestError(
                str(exc),
                code="SEARCH_UNSUPPORTED",
                url=fetch_url,
            ) from exc
        except Exception as exc:
            logger.exception(
                "search_parse_failed",
                extra={
                    "store": store_key,
                    "capability": "search",
                    "url": fetch_url,
                },
            )
            raise ParseError(
                f"Falha ao interpretar resultados de busca: {exc}"
            ) from exc

        if not candidates:
            classification = adapter.classify_empty_result(response)
            if classification == "incomplete":
                raise RequestError(
                    "SERP incompleta ou bloqueada (sem resultados parseáveis)",
                    code="SEARCH_INCOMPLETE_RESPONSE",
                    url=page_url,
                )

        enriched = [enrich_candidate_title(c) for c in candidates]
        ranked = rank_candidates_for_query(list(enriched), query)

        seen: set[str] = set()
        selected: list[SearchCandidate] = []
        for candidate in ranked:
            asin = None
            if isinstance(candidate.metadata, dict):
                asin = candidate.metadata.get("asin") or candidate.product_id
            key = (
                f"{store_key}:{str(asin).upper()}"
                if asin and is_amazon_store_url(candidate.url)
                else candidate.url.strip()
            )
            if not key or key in seen:
                continue
            seen.add(key)
            selected.append(candidate)
            if len(selected) >= limit:
                break
        _log_search_candidates(store_key, query, selected)
        return selected

    @staticmethod
    def is_search_supported(store_key: str) -> bool:
        return search_adapter_available(store_key)
