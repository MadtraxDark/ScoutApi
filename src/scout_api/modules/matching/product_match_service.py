"""Orchestrate cross-store product matching with optional persistence."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy.orm import Session

from scout_api.core.config import get_settings
from scout_api.core.performance import (
    DuplicateWorkTracker,
    OperationCategory,
    format_stage_summary,
    observe,
)
from scout_api.modules.crawler.core.browser_health import (
    is_browser_infrastructure_error,
)
from scout_api.modules.crawler.core.browser_scheduler import browser_queue_deadline
from scout_api.modules.crawler.core.exceptions import ParseError, RequestError
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.core.scrape_purpose import ScrapePurpose
from scout_api.modules.crawler.models.product import (
    ProductPriceItem,
    product_offer_from_price_item,
)
from scout_api.modules.crawler.services.product_scrape_service import (
    ProductScrapeService,
)
from scout_api.modules.crawler.stores import STORE_CONFIGS, store_display_name
from scout_api.modules.matching.attempt_budget import StoreAttemptBudget
from scout_api.modules.matching.eligibility import eligible_match_store_keys
from scout_api.modules.matching.embedding_evidence import (
    MatchEmbeddingEvaluator,
    apply_embedding_evidence,
    embedding_eligible,
)
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.gtin_learning import (
    TrustedGtin,
    identity_with_gtin,
    resolve_trusted_gtin,
)
from scout_api.modules.matching.identity import (
    ProductIdentity,
    build_search_queries,
    critical_identity_conflict,
    form_factor_conflict,
    identity_from_price_item,
    identity_reference_item,
    looks_like_accessory,
    looks_like_bundle,
    normalize_brand,
    normalize_gtin,
    prefer_identity_aligned_candidates,
    serp_candidate_text,
)
from scout_api.modules.matching.match_deadlines import (
    MonotonicDeadline,
    nested_deadline,
)
from scout_api.modules.matching.match_hang_constants import (
    FAILURE_CODE_RUN_WALL_TIMEOUT,
    FAILURE_CODE_STORE_WALL_TIMEOUT,
)
from scout_api.modules.matching.match_progress import (
    GLOBAL_MATCH_PROGRESS,
    MatchProgressPhase,
    MatchProgressTracker,
)
from scout_api.modules.matching.match_run_staging import stage_hit
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import (
    MatchHit,
    MatchProgressEvent,
    MatchRequest,
    MatchResponse,
    MatchStoreError,
)
from scout_api.modules.matching.search_candidate import SearchCandidate
from scout_api.modules.matching.store_search_order import (
    order_stores_for_match,
    split_stores_for_match_waves,
)
from scout_api.modules.matching.store_search_service import StoreSearchService

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[MatchProgressEvent], None]


@dataclass(frozen=True)
class MatchStoreOutcome:
    """Observable per-store result for durable MatchRun logging."""

    store: str
    display_name: str
    status: str  # match | no_match | error
    duration_ms: int
    queries: tuple[str, ...]
    candidates_found: int
    candidates_evaluated: int
    search_duration_ms: int
    candidate_fetch_duration_ms: int
    search_locale: str | None = None
    matched_decision: str | None = None
    matched_payload: dict[str, object] | None = None
    matched_url: str | None = None
    matched_title: str | None = None
    matched_price: Decimal | None = None
    matched_currency: str | None = None
    matched_confidence: Decimal | None = None
    matched_reasons: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    candidates: tuple[dict[str, object], ...] = ()


StoreOutcomeCallback = Callable[[MatchStoreOutcome], None]

# Fallback when the spider registry has no eligible match stores.
MVP_SEARCH_STORES = (
    "kabum",
    "bestbuy",
    "nissei",
    "shoppingchina",
    "amazon_br",
    "amazon_us",
    "magazineluiza",
    "aliexpress",
    "pichau",
    "terabyteshop",
)

_MAX_RATE_LIMIT_WAIT_SECONDS = 20.0
# Cap empty SERP retries so blocked/expensive stores (Shopee) cannot burn
# N queries × multi-minute browser sessions when the first searches return [].
# Identifier-only misses (bare MPN/GTIN) do not count — the next commercial
# series query may still recover the SKU (e.g. CFI-2114B vs CFI-2115B on SC).
_MAX_EMPTY_SEARCH_QUERIES = 2


def _normalize_query_key(query: str) -> str:
    """Collapse whitespace/case so near-duplicate progressive queries are skipped."""
    return " ".join((query or "").casefold().split())


def _is_identifier_only_query(query: str) -> bool:
    """True for bare GTIN/MPN queries that often miss across store revisions."""
    text = (query or "").strip()
    if not text:
        return False
    if " " not in text:
        return len(text) >= 6
    parts = text.split()
    if len(parts) == 2:
        # brand + manufacturer PN (including MSI-style board / GPU codes)
        from scout_api.modules.matching.identity import looks_like_mpn, normalize_mpn

        second = normalize_mpn(parts[1]) or parts[1]
        compact = re.sub(r"[^a-z0-9]", "", parts[1].casefold())
        if looks_like_mpn(second) or len(compact) >= 8:
            return True
        if parts[0].casefold() in {
            "sony",
            "apple",
            "samsung",
            "kingston",
            "corsair",
            "msi",
            "asus",
            "gigabyte",
        }:
            return len(parts[1]) >= 6
    return False


def _store_label(store_key: str) -> str:
    return store_display_name(store_key)


def _store_error(store_key: str, *, code: str, message: str) -> MatchStoreError:
    return MatchStoreError(
        store=store_key,
        store_display_name=_store_label(store_key),
        code=code,
        message=message,
    )


def _serp_title_reject_reason(
    reference: ProductIdentity,
    *,
    title: str | None,
) -> str | None:
    """Cheap reject from SERP title before spending a full PDP scrape.

    Missing title → unknown (do not reject). Only clear conflicts skip scrape.
    """
    if not (title or "").strip():
        return None
    title_text = title.strip() if title else ""
    if looks_like_accessory(title_text, reference_title=reference.title):
        return "accessory_reject"
    if looks_like_bundle(title_text, reference_title=reference.title):
        return "bundle_reject"
    from scout_api.modules.matching.identity import condition_conflict

    condition = condition_conflict(reference.title, title_text)
    if condition:
        return condition
    form = form_factor_conflict(reference.title, title_text)
    if form:
        return form
    probe = identity_from_price_item(identity_reference_item(title_text))
    critical = critical_identity_conflict(reference, probe)
    if critical:
        return critical
    ref_brand = normalize_brand(reference.brand) if reference.brand else None
    cand_brand = probe.brand
    if ref_brand and cand_brand and ref_brand != cand_brand:
        if ref_brand not in cand_brand and cand_brand not in ref_brand:
            return f"brand_mismatch:{ref_brand}!={cand_brand}"
    return None


class ProductMatchService:
    def __init__(
        self,
        *,
        scrape_service: ProductScrapeService | None = None,
        search_service: StoreSearchService | None = None,
        engine: MatchingEngine | None = None,
        session: Session | None = None,
        embedding_evaluator: MatchEmbeddingEvaluator | None = None,
        embedding_mode: str = "off",
        embedding_active_threshold: float | None = None,
    ) -> None:
        self._scrape = scrape_service or ProductScrapeService()
        self._search = search_service or StoreSearchService()
        self._engine = engine or MatchingEngine()
        self._session = session
        self._embedding_evaluator = embedding_evaluator
        self._embedding_mode = embedding_mode
        self._embedding_active_threshold = embedding_active_threshold

    def match(
        self,
        request: MatchRequest,
        *,
        on_progress: ProgressCallback | None = None,
        on_store_outcome: StoreOutcomeCallback | None = None,
        restored_matches: list[MatchHit] | None = None,
        target_stores: list[str] | None = None,
        on_targets_resolved: Callable[[list[str], ProductPriceItem], set[str] | None]
        | None = None,
        on_store_started: Callable[[str], None] | None = None,
        before_persist: Callable[[], None] | None = None,
        skip_stores: frozenset[str] | set[str] | None = None,
        run_deadline: MonotonicDeadline | None = None,
        progress_tracker: MatchProgressTracker | None = None,
        run_id: str | None = None,
    ) -> MatchResponse:
        """Match from a live reference URL (scrape → search → score)."""
        if on_progress is not None:
            on_progress(
                MatchProgressEvent(
                    type="search_progress",
                    stage="reference",
                    status="running",
                    message="Coletando produto de referência",
                    sequence=0,
                )
            )
        tracker = progress_tracker or GLOBAL_MATCH_PROGRESS
        if run_id is not None:
            tracker.mark_progress(
                run_id=run_id,
                phase=MatchProgressPhase.SEARCHING,
            )
        reference = self._scrape.scrape(
            str(request.reference_url),
            include_images=request.include_images,
            purpose=ScrapePurpose.MATCH_REFERENCE,
        )
        return self.match_from_item(
            reference,
            stores=request.stores,
            include_review=request.include_review,
            persist=request.persist,
            include_images=request.include_images,
            max_candidates_per_store=request.max_candidates_per_store,
            canonical_product_id=request.canonical_product_id,
            clear_reference_price=False,
            on_progress=on_progress,
            on_store_outcome=on_store_outcome,
            restored_matches=restored_matches,
            target_stores=target_stores,
            on_targets_resolved=on_targets_resolved,
            on_store_started=on_store_started,
            before_persist=before_persist,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=progress_tracker,
            run_id=run_id,
        )

    def match_from_item(
        self,
        reference: ProductPriceItem,
        *,
        stores: list[str] | None = None,
        include_review: bool = False,
        persist: bool = False,
        include_images: bool = False,
        max_candidates_per_store: int = 5,
        canonical_product_id: UUID | None = None,
        clear_reference_price: bool = True,
        on_progress: ProgressCallback | None = None,
        on_store_outcome: StoreOutcomeCallback | None = None,
        restored_matches: list[MatchHit] | None = None,
        target_stores: list[str] | None = None,
        on_targets_resolved: Callable[[list[str], ProductPriceItem], set[str] | None]
        | None = None,
        on_store_started: Callable[[str], None] | None = None,
        before_persist: Callable[[], None] | None = None,
        skip_stores: frozenset[str] | set[str] | None = None,
        run_deadline: MonotonicDeadline | None = None,
        progress_tracker: MatchProgressTracker | None = None,
        run_id: str | None = None,
    ) -> MatchResponse:
        """Match using an already-normalized reference item (no reference scrape).

        Intended for identity-only discovery: brand/model(/variant) without
        feeding known store URLs into Search. When ``clear_reference_price`` is
        true (default), the reference price is ignored so a synthetic identity
        placeholder cannot trigger extreme-price ``review``/reject gates.
        """
        ref_identity = identity_from_price_item(reference)
        if clear_reference_price:
            ref_identity = replace(ref_identity, price=None)
        return self._match_with_reference(
            reference,
            ref_identity,
            stores=stores,
            include_review=include_review,
            persist=persist,
            include_images=include_images,
            max_candidates_per_store=max_candidates_per_store,
            canonical_product_id=canonical_product_id,
            on_progress=on_progress,
            on_store_outcome=on_store_outcome,
            restored_matches=restored_matches,
            target_stores=target_stores,
            on_targets_resolved=on_targets_resolved,
            on_store_started=on_store_started,
            before_persist=before_persist,
            skip_stores=skip_stores,
            run_deadline=run_deadline,
            progress_tracker=progress_tracker,
            run_id=run_id,
        )

    def _match_with_reference(
        self,
        reference: ProductPriceItem,
        ref_identity: ProductIdentity,
        *,
        stores: list[str] | None,
        include_review: bool,
        persist: bool,
        include_images: bool,
        max_candidates_per_store: int,
        canonical_product_id: UUID | None = None,
        on_progress: ProgressCallback | None = None,
        on_store_outcome: StoreOutcomeCallback | None = None,
        restored_matches: list[MatchHit] | None = None,
        target_stores: list[str] | None = None,
        on_targets_resolved: Callable[[list[str], ProductPriceItem], set[str] | None]
        | None = None,
        on_store_started: Callable[[str], None] | None = None,
        before_persist: Callable[[], None] | None = None,
        skip_stores: frozenset[str] | set[str] | None = None,
        run_deadline: MonotonicDeadline | None = None,
        progress_tracker: MatchProgressTracker | None = None,
        run_id: str | None = None,
    ) -> MatchResponse:
        # Product Match never needs gallery bytes — ignore caller flag for cost.
        include_images = False
        settings = get_settings()
        tracker = progress_tracker or GLOBAL_MATCH_PROGRESS
        effective_run_id = run_id or "sync-match"
        if run_deadline is None:
            run_deadline = MonotonicDeadline.start(
                timeout_seconds=float(settings.match_run_wall_timeout_seconds)
            )

        def mark(phase: MatchProgressPhase, *, store: str | None = None) -> None:
            tracker.mark_progress(run_id=effective_run_id, phase=phase, store=store)

        def ensure_run_budget() -> None:
            if run_deadline.expired():
                logger.warning(
                    "match_run_wall_timeout",
                    extra={
                        "run_id": effective_run_id,
                        "timeout_seconds": run_deadline.timeout_seconds,
                    },
                )
                raise RequestError(
                    "Product Match excedeu o tempo máximo da execução.",
                    code=FAILURE_CODE_RUN_WALL_TIMEOUT,
                    retryable=False,
                )

        target_stores = (
            target_stores
            if target_stores is not None
            else self._resolve_stores(
                stores,
                reference,
                reference_has_gtin=bool(ref_identity.gtin),
            )
        )
        resolved_skip_stores = set(skip_stores or ())
        if on_targets_resolved is not None:
            additional_skip_stores = on_targets_resolved(target_stores, reference)
            if additional_skip_stores:
                resolved_skip_stores.update(additional_skip_stores)
        if resolved_skip_stores:
            skip = {
                s.strip().lower() for s in resolved_skip_stores if s and str(s).strip()
            }
            if skip:
                target_stores = [s for s in target_stores if s not in skip]

        matches: list[MatchHit] = list(restored_matches or [])
        unmatched: list[str] = []
        errors: list[MatchStoreError] = []
        best_by_store: dict[str, MatchHit] = {}
        learned: TrustedGtin | None = None
        if ref_identity.gtin:
            learned = TrustedGtin(gtin=ref_identity.gtin, source="reference")
        elif matches:
            learned = resolve_trusted_gtin(ref_identity, matches)

        seq = 0
        # Request-scoped caches (one Match execution).
        search_cache: dict[tuple[str, str, int], list[SearchCandidate]] = {}
        scrape_cache: dict[str, ProductPriceItem] = {}

        match_t0 = time.perf_counter()
        store_stage_ms: dict[str, dict[str, object]] = {}
        dup_tracker = DuplicateWorkTracker()
        state_lock = threading.Lock()
        concurrency = max(1, int(get_settings().match_store_concurrency))

        def emit(**kwargs: object) -> None:
            nonlocal seq
            if on_progress is None:
                return
            with state_lock:
                seq += 1
                payload = dict(kwargs)
                payload.setdefault("sequence", seq)
            on_progress(MatchProgressEvent.model_validate(payload))

        def process_one(store_key: str) -> None:
            nonlocal ref_identity, learned
            ensure_run_budget()
            display_name = _store_label(store_key)
            if on_store_started is not None:
                on_store_started(store_key)
            with state_lock:
                local_identity = ref_identity
            store_config = STORE_CONFIGS.get(store_key)
            query_locale = store_config.query_locale if store_config else None
            local_queries = build_search_queries(local_identity, locale=query_locale)
            logger.info(
                "match_store_search_locale store=%s search_locale=%s queries=%r",
                store_key,
                query_locale,
                local_queries[:5],
            )
            # --- Phase 3: per-store attempt budget ---
            settings = get_settings()
            budget = StoreAttemptBudget(
                queries_budget=settings.match_search_query_budget,
                external_attempt_budget=settings.match_external_attempt_budget,
                browser_navigation_budget=settings.match_browser_navigation_budget,
            )
            store_deadline = nested_deadline(
                run_deadline,
                timeout_seconds=float(settings.match_store_wall_timeout_seconds),
            )
            mark(MatchProgressPhase.STARTING_STORE, store=store_key)
            emit(
                type="store_started",
                store=store_key,
                display_name=display_name,
                stage="store_started",
                status="running",
                message=f"Iniciando busca em {display_name}",
            )
            if not self._search.is_search_supported(store_key):
                err = _store_error(
                    store_key,
                    code="SEARCH_UNSUPPORTED",
                    message=f"Busca ao vivo não disponível para {display_name}",
                )
                # SEARCH_UNSUPPORTED is ERROR, never silent NO_MATCH/unmatched.
                with state_lock:
                    errors.append(err)
                emit(
                    type="error",
                    store=store_key,
                    display_name=display_name,
                    stage="search_unsupported",
                    status="error",
                    message=err.message,
                )
                if on_store_outcome is not None:
                    on_store_outcome(
                        MatchStoreOutcome(
                            store=store_key,
                            display_name=display_name,
                            status="error",
                            duration_ms=0,
                            queries=(),
                            search_locale=query_locale,
                            candidates_found=0,
                            candidates_evaluated=0,
                            search_duration_ms=0,
                            candidate_fetch_duration_ms=0,
                            error_code=err.code,
                            error_message=err.message,
                        )
                    )
                return

            store_t0 = time.perf_counter()
            store_matched = False
            last_error: MatchStoreError | None = None
            empty_searches = 0
            candidates_seen = 0
            scrape_failures = 0
            title_rejects = 0
            scrapes_done = 0
            queries_skipped = 0
            duplicate_skips = 0
            search_ms_total = 0.0
            scrape_ms_total = 0.0
            last_scrape_error: MatchStoreError | None = None
            seen_query_keys: set[str] = set()
            executed_queries: list[str] = []
            candidate_logs: list[dict[str, object]] = []

            def store_wall_timed_out() -> bool:
                if not store_deadline.expired():
                    remaining = store_deadline.remaining_seconds()
                    if remaining is None or remaining > 0.05:
                        return False
                elapsed_ms = int(round((time.perf_counter() - store_t0) * 1000))
                logger.warning(
                    "match_store_wall_timeout",
                    extra={
                        "store": store_key,
                        "run_id": effective_run_id,
                        "timeout_seconds": store_deadline.timeout_seconds,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                err = _store_error(
                    store_key,
                    code=FAILURE_CODE_STORE_WALL_TIMEOUT,
                    message=(
                        f"Busca em {display_name} excedeu o tempo limite da loja."
                    ),
                )
                with state_lock:
                    errors.append(err)
                emit(
                    type="error",
                    store=store_key,
                    display_name=display_name,
                    stage="store_wall_timeout",
                    status="error",
                    message=err.message,
                )
                mark(MatchProgressPhase.FINALIZING_STORE, store=store_key)
                if on_store_outcome is not None:
                    on_store_outcome(
                        MatchStoreOutcome(
                            store=store_key,
                            display_name=display_name,
                            status="error",
                            duration_ms=elapsed_ms,
                            queries=tuple(executed_queries),
                            search_locale=query_locale,
                            candidates_found=candidates_seen,
                            candidates_evaluated=scrapes_done,
                            search_duration_ms=int(round(search_ms_total)),
                            candidate_fetch_duration_ms=int(round(scrape_ms_total)),
                            error_code=err.code,
                            error_message=err.message,
                            candidates=tuple(candidate_logs),
                        )
                    )
                return True

            for query in local_queries:
                if store_wall_timed_out():
                    return
                ensure_run_budget()
                qkey = _normalize_query_key(query)
                if qkey and qkey in seen_query_keys:
                    queries_skipped += 1
                    logger.debug(
                        "match_query_dedup_skip",
                        extra={"store": store_key, "query": query},
                    )
                    continue
                if qkey:
                    seen_query_keys.add(qkey)
                # --- Phase 3: query budget gate (after dedup, so deduped
                # queries do not consume the budget) ---
                if not budget.begin_query():
                    logger.info(
                        "match_query_budget_cap",
                        extra={
                            "store": store_key,
                            "queries_used": budget.queries_used,
                            "queries_budget": budget.queries_budget,
                        },
                    )
                    break
                emit(
                    type="searching",
                    store=store_key,
                    display_name=display_name,
                    stage="searching",
                    status="running",
                    message=f"Buscando: {query}",
                )
                mark(MatchProgressPhase.SEARCHING, store=store_key)
                executed_queries.append(query)
                try:
                    search_t0 = time.perf_counter()
                    cache_key = (store_key, qkey, max_candidates_per_store)
                    with state_lock:
                        cached_candidates = search_cache.get(cache_key)
                    if cached_candidates is not None:
                        candidates = list(cached_candidates)
                        budget.skip_cached()
                    else:
                        # --- Phase 3: external attempt gate for SERP fetch ---
                        if not budget.record_external():
                            logger.info(
                                "match_external_budget_cap",
                                extra={"store": store_key, "phase": "serp"},
                            )
                            break
                        # Browser queue waits are bounded by this store's wall
                        # budget. HTTP-first work remains concurrent; only a
                        # caller that falls back to C1 browser uses this cap.
                        with browser_queue_deadline(store_deadline.deadline_monotonic):
                            candidates = self._search.search(
                                store_key,
                                query,
                                limit=max_candidates_per_store,
                                on_browser_nav_used=budget.record_browser_nav,
                            )
                        with state_lock:
                            search_cache[cache_key] = list(candidates)
                    search_ms = (time.perf_counter() - search_t0) * 1000
                    search_ms_total += search_ms
                    observe(
                        "product_search",
                        search_ms,
                        category=OperationCategory.PRODUCT_SEARCH,
                        stage=store_key,
                        context={
                            "query_len": len(query),
                            "candidates": len(candidates),
                            "cache_hit": cached_candidates is not None,
                        },
                    )
                except RequestError as exc:
                    if store_deadline.expired():
                        store_wall_timed_out()
                        return
                    last_error = _store_error(
                        store_key,
                        code=exc.code,
                        message=str(exc),
                    )
                    # SEARCH_UNSUPPORTED, WAF/incomplete SERP and structural
                    # browser infra must not keep spending queries (ERROR,
                    # never silent NO_MATCH). WAF/SERP codes indicate the
                    # entire SERP is blocked — retrying other queries won't
                    # help.
                    if (
                        exc.code == "SEARCH_UNSUPPORTED"
                        or exc.code
                        in {
                            "UPSTREAM_WAF_BLOCKED",
                            "UPSTREAM_BLOCKED",
                            "SEARCH_INCOMPLETE_RESPONSE",
                            "SEARCH_PARSE_ERROR",
                        }
                        or is_browser_infrastructure_error(exc.code)
                    ):
                        break
                    continue
                except ParseError as exc:
                    last_error = _store_error(
                        store_key,
                        code="PARSE_ERROR",
                        message=str(exc),
                    )
                    continue

                if not candidates:
                    if not _is_identifier_only_query(query):
                        empty_searches += 1
                    if empty_searches >= _MAX_EMPTY_SEARCH_QUERIES:
                        logger.info(
                            "match_empty_search_cap",
                            extra={
                                "store": store_key,
                                "empty_searches": empty_searches,
                            },
                        )
                        break
                    continue

                empty_searches = 0
                candidates = prefer_identity_aligned_candidates(
                    local_identity, candidates
                )
                mark(MatchProgressPhase.EVALUATING, store=store_key)
                emit(
                    type="candidates_found",
                    store=store_key,
                    display_name=display_name,
                    stage="candidates_found",
                    status="running",
                    message=f"{len(candidates)} candidatos encontrados",
                    candidate_count=len(candidates),
                )
                logger.debug(
                    "match_store_search",
                    extra={
                        "store": store_key,
                        "query": query,
                        "candidates": len(candidates),
                        "search_ms": round(search_ms, 1),
                    },
                )
                _budget_stop_candidates = False
                for candidate in candidates:
                    if store_wall_timed_out():
                        return
                    ensure_run_budget()
                    if scrapes_done >= max_candidates_per_store:
                        logger.info(
                            "match_scrape_budget_cap",
                            extra={
                                "store": store_key,
                                "scrapes": scrapes_done,
                                "budget": max_candidates_per_store,
                            },
                        )
                        break
                    if canonicalize_url(candidate.url) == canonicalize_url(
                        reference.canonical_url
                    ):
                        continue
                    candidates_seen += 1
                    reject = _serp_title_reject_reason(
                        local_identity, title=serp_candidate_text(candidate)
                    )
                    if reject is not None:
                        title_rejects += 1
                        logger.info(
                            "match_serp_title_reject store=%s query=%r "
                            "reason=%s title=%r",
                            store_key,
                            query,
                            reject,
                            (candidate.title or "")[:120],
                            extra={
                                "store": store_key,
                                "reason": reject,
                                "title": (candidate.title or "")[:120],
                            },
                        )
                        continue
                    canon = canonicalize_url(candidate.url)
                    with state_lock:
                        dup_count = dup_tracker.record("scrape_url", canon)
                        cached_product = scrape_cache.get(canon)
                    product: ProductPriceItem | None
                    scrape_error: MatchStoreError | None
                    if cached_product is not None:
                        duplicate_skips += 1
                        product = cached_product
                        scrape_error = None
                        scrape_ms = 0.0
                        budget.skip_cached()
                    else:
                        # --- Phase 3: external attempt gate for candidate scrape ---
                        if not budget.record_external():
                            logger.info(
                                "match_external_budget_cap",
                                extra={"store": store_key, "phase": "candidate_scrape"},
                            )
                            _budget_stop_candidates = True
                            break
                        emit(
                            type="scraping_candidate",
                            store=store_key,
                            display_name=display_name,
                            stage="scraping_candidate",
                            status="running",
                            message="Avaliando candidato",
                        )
                        mark(MatchProgressPhase.FETCHING_CANDIDATE, store=store_key)
                        scrape_t0 = time.perf_counter()
                        with browser_queue_deadline(store_deadline.deadline_monotonic):
                            product, scrape_error = self._scrape_candidate(
                                candidate.url,
                                store_key=store_key,
                                include_images=include_images,
                            )
                        scrapes_done += 1
                        scrape_ms = (time.perf_counter() - scrape_t0) * 1000
                        scrape_ms_total += scrape_ms
                        if store_deadline.expired():
                            store_wall_timed_out()
                            return
                        if product is not None:
                            with state_lock:
                                scrape_cache[canon] = product
                    observe(
                        "product_scrape",
                        scrape_ms,
                        category=OperationCategory.CRAWLER,
                        stage=store_key,
                        context={
                            "duplicate_scrape": dup_count > 1,
                            "cache_hit": cached_product is not None,
                            "ok": product is not None,
                        },
                    )
                    if product is None:
                        scrape_failures += 1
                        if scrape_error is not None:
                            last_scrape_error = scrape_error
                            # Structural browser failure: stop this store now
                            # (do not burn remaining SERP candidates × launch).
                            if is_browser_infrastructure_error(scrape_error.code):
                                last_error = scrape_error
                                break
                        continue

                    candidate_identity = identity_from_price_item(product)
                    score = self._engine.score(local_identity, candidate_identity)
                    embedding_evidence: dict[str, str | float | int | None] | None = (
                        None
                    )
                    if self._embedding_evaluator is not None and embedding_eligible(
                        score
                    ):
                        evidence = self._embedding_evaluator.evaluate(
                            local_identity,
                            candidate_identity,
                            deadline=store_deadline,
                        )
                        embedding_evidence = evidence.as_json()
                        score = apply_embedding_evidence(
                            score,
                            evidence,
                            mode=self._embedding_mode,
                            active_threshold=self._embedding_active_threshold,
                            reference=local_identity,
                            candidate=candidate_identity,
                        )
                    candidate_url_parts = urlsplit(
                        (product.url or product.canonical_url).strip()
                    )
                    candidate_log_url = urlunsplit(
                        (
                            candidate_url_parts.scheme,
                            candidate_url_parts.netloc,
                            candidate_url_parts.path,
                            "",
                            "",
                        )
                    )[:500]
                    logger.info(
                        "match_candidate_decision store=%s query=%r title=%r url=%s "
                        "category=%r model=%r category_attrs=%r mpn=%r "
                        "decision=%s confidence=%s "
                        "reasons=%r",
                        store_key,
                        query,
                        (product.title or "")[:200],
                        candidate_log_url,
                        candidate_identity.category,
                        candidate_identity.model,
                        candidate_identity.category_attrs,
                        candidate_identity.mpn_display or candidate_identity.mpn,
                        score.decision,
                        score.confidence,
                        [f"{r.code}:{r.detail}" for r in score.reasons[:8]],
                    )
                    if len(candidate_logs) < 40:
                        candidate_logs.append(
                            {
                                "title": product.title,
                                "url": product.url or product.canonical_url,
                                "store_product_id": product.product_id,
                                "decision": score.decision,
                                "confidence": score.confidence,
                                "reasons": [
                                    f"{r.code}:{r.detail}" for r in score.reasons[:8]
                                ],
                                "duration_ms": int(round(scrape_ms)),
                                "embedding_evidence": embedding_evidence,
                            }
                        )
                    if score.decision == "reject":
                        continue
                    if score.decision == "review" and not include_review:
                        continue

                    hit = MatchHit(
                        store=store_key,
                        store_display_name=display_name,
                        country=product.country,
                        decision=score.decision,
                        confidence=score.confidence,
                        reasons=list(score.reasons),
                        product=product,
                        search_query=query,
                    )
                    with state_lock:
                        existing = best_by_store.get(store_key)
                        if existing is None or hit.confidence > existing.confidence:
                            best_by_store[store_key] = hit
                    if score.decision == "auto_match":
                        store_matched = True
                        with state_lock:
                            learned = self._maybe_learn_gtin(
                                learned, local_identity, hit
                            )
                            if learned and not ref_identity.gtin:
                                ref_identity = identity_with_gtin(
                                    ref_identity, learned.gtin
                                )
                                local_identity = ref_identity
                                local_queries = build_search_queries(
                                    local_identity, locale=query_locale
                                )
                                logger.info(
                                    "trusted_gtin_learned",
                                    extra={
                                        "gtin": learned.gtin,
                                        "source": learned.source,
                                    },
                                )
                        logger.debug(
                            "match_auto_match_early_stop",
                            extra={
                                "store": store_key,
                                "query": query,
                                "scrape_ms": round(scrape_ms, 1),
                                "confidence": str(hit.confidence),
                            },
                        )
                        break

                if store_matched:
                    break
                if _budget_stop_candidates:
                    break
                if scrapes_done >= max_candidates_per_store:
                    break
                if last_error is not None and is_browser_infrastructure_error(
                    last_error.code
                ):
                    break

            store_elapsed_ms = (time.perf_counter() - store_t0) * 1000
            store_timing = {
                "store": store_key,
                "elapsed_ms": round(store_elapsed_ms, 1),
                "search_ms": round(search_ms_total, 1),
                "scrape_ms": round(scrape_ms_total, 1),
                "candidates_seen": candidates_seen,
                "title_rejects": title_rejects,
                "scrapes": scrapes_done,
                "matched": store_matched,
                "queries_skipped": queries_skipped,
                "duplicate_skips": duplicate_skips,
                # Phase 3: attempt budget observability
                "queries_used": budget.queries_used,
                "queries_budget": budget.queries_budget,
                "external_attempts": budget.external_attempts,
                "external_attempt_budget": budget.external_attempt_budget,
                "browser_navigations": budget.browser_navigations,
                "browser_navigation_budget": budget.browser_navigation_budget,
                "stopped_reason": budget.stopped_reason,
            }
            with state_lock:
                store_stage_ms[store_key] = store_timing
            logger.info("match_store_timing", extra=store_timing)
            logger.info(
                "match_store_summary store=%s elapsed_ms=%.1f search_ms=%.1f "
                "scrape_ms=%.1f queries=%r candidates_seen=%d title_rejects=%d "
                "scrapes=%d matched=%s queries_skipped=%d stopped_reason=%s",
                store_key,
                store_timing["elapsed_ms"],
                store_timing["search_ms"],
                store_timing["scrape_ms"],
                executed_queries,
                candidates_seen,
                title_rejects,
                scrapes_done,
                store_matched,
                queries_skipped,
                budget.stopped_reason,
            )
            observe(
                "product_match_store",
                store_elapsed_ms,
                category=OperationCategory.PRODUCT_MATCH,
                stage=store_key,
                context=store_timing,
            )

            if store_key in best_by_store:
                with state_lock:
                    matches.append(best_by_store[store_key])
                hit = best_by_store[store_key]
                mark(MatchProgressPhase.MATCHING, store=store_key)
                emit(
                    type="matched",
                    store=store_key,
                    display_name=display_name,
                    stage="matched",
                    status="success",
                    message=f"Match em {display_name}",
                )
                if on_store_outcome is not None:
                    offer = hit.product
                    price = None
                    currency = None
                    if offer is not None:
                        price = offer.pix_price or offer.price or offer.original_price
                        currency = offer.currency
                    on_store_outcome(
                        MatchStoreOutcome(
                            store=store_key,
                            display_name=display_name,
                            status="match",
                            matched_decision=hit.decision,
                            matched_payload=stage_hit(hit),
                            duration_ms=int(round(store_elapsed_ms)),
                            queries=tuple(executed_queries),
                            search_locale=query_locale,
                            candidates_found=candidates_seen,
                            candidates_evaluated=scrapes_done,
                            search_duration_ms=int(round(search_ms_total)),
                            candidate_fetch_duration_ms=int(round(scrape_ms_total)),
                            matched_url=(
                                offer.url or offer.canonical_url if offer else None
                            ),
                            matched_title=offer.title if offer else None,
                            matched_price=price,
                            matched_currency=currency,
                            matched_confidence=hit.confidence,
                            matched_reasons=tuple(
                                f"{r.code}:{r.detail}" for r in hit.reasons[:12]
                            ),
                            candidates=tuple(candidate_logs),
                        )
                    )
            else:
                with state_lock:
                    unmatched.append(store_key)
                # Prefer search-level errors; otherwise surface scrape failures
                # so UPSTREAM_BLOCKED / RATE_LIMITED never look like NO_MATCH.
                if last_error is not None:
                    with state_lock:
                        errors.append(last_error)
                    emit(
                        type="error",
                        store=store_key,
                        display_name=display_name,
                        stage="error",
                        status="error",
                        message=last_error.message,
                    )
                    if on_store_outcome is not None:
                        on_store_outcome(
                            MatchStoreOutcome(
                                store=store_key,
                                display_name=display_name,
                                status="error",
                                duration_ms=int(round(store_elapsed_ms)),
                                queries=tuple(executed_queries),
                                search_locale=query_locale,
                                candidates_found=candidates_seen,
                                candidates_evaluated=scrapes_done,
                                search_duration_ms=int(round(search_ms_total)),
                                candidate_fetch_duration_ms=int(round(scrape_ms_total)),
                                error_code=last_error.code,
                                error_message=last_error.message,
                                candidates=tuple(candidate_logs),
                            )
                        )
                elif (
                    scrapes_done > 0
                    and scrape_failures >= scrapes_done
                    and last_scrape_error is not None
                ):
                    with state_lock:
                        errors.append(last_scrape_error)
                    emit(
                        type="error",
                        store=store_key,
                        display_name=display_name,
                        stage="error",
                        status="error",
                        message=last_scrape_error.message,
                    )
                    if on_store_outcome is not None:
                        on_store_outcome(
                            MatchStoreOutcome(
                                store=store_key,
                                display_name=display_name,
                                status="error",
                                duration_ms=int(round(store_elapsed_ms)),
                                queries=tuple(executed_queries),
                                search_locale=query_locale,
                                candidates_found=candidates_seen,
                                candidates_evaluated=scrapes_done,
                                search_duration_ms=int(round(search_ms_total)),
                                candidate_fetch_duration_ms=int(round(scrape_ms_total)),
                                error_code=last_scrape_error.code,
                                error_message=last_scrape_error.message,
                                candidates=tuple(candidate_logs),
                            )
                        )
                else:
                    emit(
                        type="no_match",
                        store=store_key,
                        display_name=display_name,
                        stage="no_match",
                        status="no_result",
                        message=f"Sem correspondência em {display_name}",
                    )
                    if on_store_outcome is not None:
                        on_store_outcome(
                            MatchStoreOutcome(
                                store=store_key,
                                display_name=display_name,
                                status="no_match",
                                duration_ms=int(round(store_elapsed_ms)),
                                queries=tuple(executed_queries),
                                search_locale=query_locale,
                                candidates_found=candidates_seen,
                                candidates_evaluated=scrapes_done,
                                search_duration_ms=int(round(search_ms_total)),
                                candidate_fetch_duration_ms=int(round(scrape_ms_total)),
                                candidates=tuple(candidate_logs),
                            )
                        )

        def run_stores(store_list: list[str], *, parallel: bool) -> None:
            if not store_list:
                return
            ensure_run_budget()
            if not parallel or len(store_list) <= 1 or concurrency <= 1:
                for key in store_list:
                    process_one(key)
                return
            logger.info(
                "match_store_wave_parallel",
                extra={"workers": concurrency, "stores": len(store_list)},
            )
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(process_one, key) for key in store_list]
                for fut in as_completed(futures):
                    fut.result()

        wave1, wave2, wave3 = split_stores_for_match_waves(target_stores)
        logger.info(
            "match_store_waves",
            extra={
                "wave1": wave1,
                "wave2": wave2,
                "wave3": wave3,
                "concurrency": concurrency,
            },
        )
        # Wave 1 serial: GTIN + dominant locale (barcode learning, no thrash).
        run_stores(wave1, parallel=False)
        # Wave 2 parallel: same locale remainder (Camoufox owner-thread serializes).
        run_stores(wave2, parallel=True)
        # Wave 3 serial: other locales last (es-PY / en-US) after pt-BR cluster.
        run_stores(wave3, parallel=False)

        match_elapsed_ms = (time.perf_counter() - match_t0) * 1000
        total_extra = {
            "elapsed_ms": round(match_elapsed_ms, 1),
            "stores": len(target_stores),
            "matches": len(matches),
            "concurrency": concurrency,
            "search_cache_entries": len(search_cache),
            "scrape_cache_entries": len(scrape_cache),
            "stores_timing": {
                key: {
                    "elapsed_ms": val["elapsed_ms"],
                    "search_ms": val["search_ms"],
                    "scrape_ms": val["scrape_ms"],
                    "scrapes": val["scrapes"],
                    "matched": val["matched"],
                    "title_rejects": val.get("title_rejects"),
                    "queries_skipped": val.get("queries_skipped"),
                    "duplicate_skips": val.get("duplicate_skips"),
                }
                for key, val in store_stage_ms.items()
            },
        }
        logger.info("match_total_timing", extra=total_extra)
        summary = format_stage_summary(
            "Product Match",
            match_elapsed_ms,
            {
                store: {
                    "elapsed_ms": timing["elapsed_ms"],
                    "search_ms": timing["search_ms"],
                    "scrape_ms": timing["scrape_ms"],
                    "scrapes": timing["scrapes"],
                    "matched": timing["matched"],
                }
                for store, timing in store_stage_ms.items()
            },
        )
        logger.info("match_timing_summary\n%s", summary)
        observe(
            "product_match",
            match_elapsed_ms,
            category=OperationCategory.PRODUCT_MATCH,
            stage="total",
            context=total_extra,
        )
        dup_tracker.observe_if_repeated(
            operation="product_match_duplicate_scrape",
            category=OperationCategory.PRODUCT_MATCH,
        )

        # Final consensus across all auto-matches (guards mid-flight conflicts).
        trusted = resolve_trusted_gtin(ref_identity, matches)
        if trusted is None and learned and learned.source == "reference":
            trusted = learned
        else:
            learned = trusted

        if learned and learned.source != "reference" and not reference.gtin:
            reference = reference.model_copy(update={"gtin": learned.gtin})

        canonical_id = None
        if persist:
            if self._session is None:
                raise RequestError(
                    "Persistência requer DATABASE_URL / sessão SQLAlchemy",
                    code="DATABASE_UNAVAILABLE",
                )
            if before_persist is not None:
                before_persist()
            canonical_id = self._persist(
                reference,
                matches,
                learned,
                canonical_product_id=canonical_product_id,
            )

        response = MatchResponse(
            canonical_product_id=canonical_id,
            reference=reference,
            matches=matches,
            unmatched_stores=unmatched,
            errors=errors,
            discovered_gtin=learned.gtin if learned else None,
            gtin_source=learned.source if learned else None,
        )
        emit(
            type="completed",
            stage="completed",
            status="success",
            message="Product Match concluído",
            result=response,
        )
        return response

    @staticmethod
    def _maybe_learn_gtin(
        current: TrustedGtin | None,
        reference_identity: object,
        hit: MatchHit,
    ) -> TrustedGtin | None:
        """Adopt a candidate GTIN mid-flight only under safe auto_match rules."""
        assert isinstance(reference_identity, ProductIdentity)
        cand = normalize_gtin(hit.product.gtin)
        if not cand or hit.decision != "auto_match":
            return current
        probe = resolve_trusted_gtin(reference_identity, [hit])
        if probe is None:
            return current
        if current is None:
            return TrustedGtin(gtin=cand, source=f"auto_match:{hit.store}")
        if current.gtin == cand:
            return current
        # Conflict with a previously learned barcode → drop learning.
        logger.warning(
            "trusted_gtin_conflict",
            extra={"previous": current.gtin, "candidate": cand},
        )
        return current if current.source == "reference" else None

    def _scrape_candidate(
        self,
        url: str,
        *,
        store_key: str,
        include_images: bool,
    ) -> tuple[ProductPriceItem | None, MatchStoreError | None]:
        """Scrape a SERP candidate; wait once on domain RATE_LIMITED."""
        try:
            return self._scrape.scrape(
                url,
                include_images=include_images,
                purpose=ScrapePurpose.MATCH_CANDIDATE,
            ), None
        except ParseError as exc:
            logger.info(
                "match_candidate_scrape_failed",
                extra={"store": store_key, "url": url, "error": str(exc)},
            )
            return None, _store_error(
                store_key,
                code="PARSE_ERROR",
                message=str(exc),
            )
        except RequestError as exc:
            if exc.code != "RATE_LIMITED":
                logger.info(
                    "match_candidate_scrape_failed",
                    extra={
                        "store": store_key,
                        "url": url,
                        "error": str(exc),
                        "code": exc.code,
                    },
                )
                return None, _store_error(
                    store_key,
                    code=exc.code,
                    message=str(exc),
                )
            wait = float(exc.retry_after or 15)
            wait = min(max(wait, 0.5), _MAX_RATE_LIMIT_WAIT_SECONDS)
            logger.info(
                "match_candidate_rate_limited_retry",
                extra={"store": store_key, "url": url, "wait_s": wait},
            )
            time.sleep(wait)
            try:
                return self._scrape.scrape(
                    url,
                    include_images=include_images,
                    purpose=ScrapePurpose.MATCH_CANDIDATE,
                ), None
            except RequestError as retry_exc:
                logger.info(
                    "match_candidate_scrape_failed",
                    extra={
                        "store": store_key,
                        "url": url,
                        "error": str(retry_exc),
                    },
                )
                return None, _store_error(
                    store_key,
                    code=retry_exc.code,
                    message=str(retry_exc),
                )
            except ParseError as retry_exc:
                logger.info(
                    "match_candidate_scrape_failed",
                    extra={
                        "store": store_key,
                        "url": url,
                        "error": str(retry_exc),
                    },
                )
                return None, _store_error(
                    store_key,
                    code="PARSE_ERROR",
                    message=str(retry_exc),
                )

    def _persist(
        self,
        reference: ProductPriceItem,
        matches: list[MatchHit],
        learned: TrustedGtin | None,
        *,
        canonical_product_id: UUID | None = None,
    ) -> UUID:
        assert self._session is not None
        repo = MatchingRepository(self._session)
        ref_identity = identity_from_price_item(reference)
        if learned and not ref_identity.gtin:
            ref_identity = identity_with_gtin(ref_identity, learned.gtin)

        if canonical_product_id is not None:
            canonical = repo.get_canonical(canonical_product_id)
            if canonical is None:
                raise RequestError(
                    "canonical_product_id não encontrado",
                    code="NOT_FOUND",
                )
            # Enrich existing product identity without creating a duplicate.
            if ref_identity.brand and not canonical.brand:
                canonical.brand = ref_identity.brand
            if ref_identity.model and not canonical.model:
                canonical.model = ref_identity.model
            if not canonical.title and reference.title:
                canonical.title = reference.title[:512]
            self._session.flush()
        else:
            canonical = repo.upsert_canonical_from_identity(
                ref_identity,
                title=reference.title,
                attributes=dict(ref_identity.variant_attrs),
            )
        if learned:
            created = repo.ensure_gtin_identifier(
                canonical, learned.gtin, source=learned.source
            )
            if created:
                logger.info(
                    "canonical_gtin_persisted",
                    extra={"gtin": learned.gtin, "source": learned.source},
                )

        allow_reparent = canonical_product_id is not None
        identity_only_reference = bool((reference.metadata or {}).get("identity_only"))
        if not identity_only_reference:
            ref_listing = repo.upsert_listing(
                canonical=canonical,
                item=reference,
                decision="auto_match",
                confidence=Decimal("1.0000"),
                status="active",
                allow_reparent=allow_reparent,
            )
            ref_offer = product_offer_from_price_item(reference)
            if repo.latest_snapshot(ref_listing.id) is None:
                repo.append_snapshot_from_offer(ref_listing, ref_offer)
                repo.append_event(
                    ref_listing, "offer_created", after={"url": reference.url}
                )
                from scout_api.modules.monitoring.hooks import (
                    initialize_listing_schedule,
                )

                initialize_listing_schedule(
                    ref_listing, checked_at=ref_offer.scraped_at or datetime.now(UTC)
                )
        if learned and learned.source != "reference" and not identity_only_reference:
            repo.append_event(
                ref_listing,
                "gtin_learned",
                after={"gtin": learned.gtin, "source": learned.source},
            )

        for hit in matches:
            if hit.decision == "reject":
                continue
            status = "review" if hit.decision == "review" else "active"
            listing = repo.upsert_listing(
                canonical=canonical,
                item=hit.product,
                decision=hit.decision,
                confidence=hit.confidence,
                status=status,
                allow_reparent=allow_reparent,
            )
            hit.listing_id = listing.id
            offer = product_offer_from_price_item(hit.product)
            repo.append_snapshot_from_offer(listing, offer)
            repo.append_event(
                listing,
                "offer_created",
                after={"url": hit.product.url, "decision": hit.decision},
            )
            from scout_api.modules.monitoring.hooks import initialize_listing_schedule

            initialize_listing_schedule(
                listing, checked_at=offer.scraped_at or datetime.now(UTC)
            )
        self._session.flush()
        return canonical.id

    @staticmethod
    def _resolve_stores(
        requested: list[str] | None,
        reference: ProductPriceItem,
        *,
        reference_has_gtin: bool = False,
    ) -> list[str]:
        # Eligible = registered ∩ implemented ∩ match_enabled ∩ search adapter.
        # Temporarily disabled stores (match_enabled=False) are omitted entirely
        # — never ERROR / NO_MATCH for that operation.
        available = list(eligible_match_store_keys()) or list(MVP_SEARCH_STORES)
        if requested:
            selected = [s.strip().lower() for s in requested if s.strip()]
            eligible = set(available)
            selected = [s for s in selected if s in eligible]
        else:
            selected = list(available)
        # "Outras lojas": never re-search the reference store itself.
        # Spiders may emit store="amazon" while catalog keys are amazon_br/us.
        ref_store = (reference.store or "").strip().lower()
        ref_country = (reference.country or "").strip().upper()
        if ref_store:
            filtered: list[str] = []
            for key in selected:
                if key == ref_store:
                    continue
                cfg = STORE_CONFIGS.get(key)
                if (
                    cfg is not None
                    and cfg.key == ref_store
                    and (not ref_country or cfg.country.upper() == ref_country)
                ):
                    continue
                filtered.append(key)
            selected = filtered
        return order_stores_for_match(
            selected,
            reference_store=reference.store,
            reference_has_gtin=reference_has_gtin,
        )
