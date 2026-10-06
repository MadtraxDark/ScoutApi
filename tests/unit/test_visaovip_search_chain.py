"""Unit tests: Visão VIP Strategy A→B chain + search error taxonomy (Task 10)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse

from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.matching.search_adapters.paraguay import (
    visaovip_action_strategy as _action_strategy,
)
from scout_api.modules.matching.search_candidate import SearchCandidate
from scout_api.modules.matching.store_search_service import (
    StoreSearchService,
    _dedup_candidates_by_product_id,
)

# ---------------------------------------------------------------------------
StrategyResult = _action_strategy.StrategyResult

# Helpers
# ---------------------------------------------------------------------------


def _candidate(product_id: str, title: str = "Produto") -> SearchCandidate:
    return SearchCandidate(
        url=f"https://www.visaovip.com/prod/cat/{product_id}/",
        title=title,
        product_id=product_id,
        metadata={"source": "visaovip-action"},
    )


def _make_empty_response(
    url: str = "https://www.visaovip.com/busca/termo/foo/",
) -> HtmlResponse:
    """Minimal scrapy Response (no /prod/ links → classify_empty_result returns
    incomplete)."""
    return HtmlResponse(
        url=url,
        body=b"<html><body>sem resultados aqui</body></html>",
        encoding="utf-8",
    )


def _make_genuine_empty_response(
    url: str = "https://www.visaovip.com/busca/termo/foo/",
) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        body=b"<html><body>Nenhum resultado encontrado</body></html>",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _dedup_candidates_by_product_id
# ---------------------------------------------------------------------------


def test_dedup_by_product_id_keeps_first_occurrence() -> None:
    cands = [
        _candidate("1", "A"),
        _candidate("2", "B"),
        _candidate("1", "C"),  # dup — dropped
    ]
    result = _dedup_candidates_by_product_id(cands, limit=10)
    assert len(result) == 2
    assert result[0].product_id == "1"
    assert result[0].title == "A"
    assert result[1].product_id == "2"


def test_dedup_by_product_id_respects_limit() -> None:
    cands = [_candidate(str(i)) for i in range(10)]
    result = _dedup_candidates_by_product_id(cands, limit=3)
    assert len(result) == 3


def test_dedup_by_product_id_skips_empty_product_id() -> None:
    cands = [
        SearchCandidate(url="https://x.com/a", title="A", product_id="", metadata={}),
        _candidate("1"),
    ]
    result = _dedup_candidates_by_product_id(cands, limit=10)
    assert len(result) == 1
    assert result[0].product_id == "1"


# ---------------------------------------------------------------------------
# Strategy A SUCCESS → return A candidates, skip B
# ---------------------------------------------------------------------------


def test_chain_strategy_a_success_skips_b() -> None:
    cands_a = [_candidate("42", "RTX 5070")]
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/rtx-5070/",
        method="GET",
        prefer_browser=True,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.SUCCESS, cands_a)

    fetcher_mock = MagicMock()
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = "abc" * 14  # 42-char fake id

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
    ):
        result = svc.search("visaovip", "RTX 5070")

    assert len(result) == 1
    assert result[0].product_id == "42"
    # Strategy B (fetcher) must NOT have been called
    fetcher_mock.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Strategy A NO_RESULTS → genuine empty (skip B)
# ---------------------------------------------------------------------------


def test_chain_strategy_a_no_results_genuine_empty() -> None:
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/xyz/",
        method="GET",
        prefer_browser=True,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.NO_RESULTS, [])

    fetcher_mock = MagicMock()
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = "abc" * 14

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
    ):
        result = svc.search("visaovip", "xyz absurd query")

    assert result == []
    fetcher_mock.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Strategy A UNAVAILABLE → fallback to B
# ---------------------------------------------------------------------------


def test_chain_strategy_a_unavailable_falls_back_to_b() -> None:
    cands_b = [_candidate("99", "Notebook")]
    _response_mock = _make_empty_response()
    # Override: classify as genuine_empty so B returns []
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/notebook/",
        method="GET",
        prefer_browser=False,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.UNAVAILABLE, None)
    adapter.parse_candidates.return_value = cands_b

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html><body><a href='/prod/cat/99/'>Notebook</a></body></html>",
        url="https://www.visaovip.com/busca/termo/notebook/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = "abc" * 14

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        result = svc.search("visaovip", "Notebook")

    # B was called
    fetcher_mock.fetch.assert_called_once()
    assert len(result) == 1
    assert result[0].product_id == "99"


# ---------------------------------------------------------------------------
# Strategy A BLOCKED → fallback to B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a_result",
    [StrategyResult.BLOCKED, StrategyResult.INVALID_RESPONSE, StrategyResult.ERROR],
)
def test_chain_strategy_a_error_falls_back_to_b(a_result: StrategyResult) -> None:
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/gpu/",
        method="GET",
        prefer_browser=False,
    )
    adapter.try_strategy_a.return_value = (a_result, None)
    adapter.parse_candidates.return_value = [_candidate("7")]

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html><body>ok</body></html>",
        url="https://www.visaovip.com/busca/termo/gpu/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = "abc" * 14

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        result = svc.search("visaovip", "GPU")

    fetcher_mock.fetch.assert_called_once()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Strategy A disabled (flag off) → goes straight to B
# ---------------------------------------------------------------------------


def test_chain_strategy_a_disabled_uses_b() -> None:
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/cpu/",
        method="GET",
        prefer_browser=False,
    )
    adapter.parse_candidates.return_value = [_candidate("3")]

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html><body>ok</body></html>",
        url="https://www.visaovip.com/busca/termo/cpu/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = False
    settings_mock.visaovip_search_action_id = ""
    settings_mock.store_capability_circuit_enabled = False

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        result = svc.search("visaovip", "CPU")

    # A was never called; B ran
    adapter.try_strategy_a.assert_not_called()
    fetcher_mock.fetch.assert_called_once()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# PENDING-018: auto-discover action ID when enabled and id empty
# ---------------------------------------------------------------------------


def test_chain_discovers_action_id_then_strategy_a_success() -> None:
    """Empty Settings id → Camoufox SERP once → chunk scan → A SUCCESS → no 2nd B."""
    from scout_api.modules.matching.search_adapters.paraguay import (
        visaovip_action_strategy as vv_action,
    )

    vv_action.reset_action_id_cache_for_tests()

    fake_id = "a" * 40
    _chunk_js = (
        'createServerReference("'
        + fake_id
        + '",b,void 0,c.findSourceMapURL,"searchProducts")'
    )
    # Hydrated SERP HTML (≥10 KB) with one chunk script tag
    pad = "x" * 10_000
    serp_html = (
        f"<html><head></head><body>{pad}"
        f'<script src="/_next/static/chunks/abcdef0123456789.js"></script>'
        f"</body></html>"
    )

    cands_a = [_candidate("99", "S25 Ultra")]
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/Samsung-Galaxy-S25-Ultra/",
        method="GET",
        prefer_browser=True,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.SUCCESS, cands_a)

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text=serp_html,
        url="https://www.visaovip.com/busca/termo/Samsung-Galaxy-S25-Ultra/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = ""  # force discovery
    settings_mock.store_capability_circuit_enabled = False

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.search_adapters.paraguay."
            "visaovip_action_strategy.discover_action_id_from_serp_html",
            return_value=fake_id,
        ) as discover_mock,
    ):
        result = svc.search("visaovip", "Samsung Galaxy S25 Ultra")

    discover_mock.assert_called()
    adapter.try_strategy_a.assert_called_once()
    assert adapter.try_strategy_a.call_args.kwargs["action_id"] == fake_id
    # MagicMock fetcher has no class-level browser_post → post_fn stays None
    assert adapter.try_strategy_a.call_args.kwargs.get("post_fn") is None
    # One discovery fetch; A succeeded so B does not re-fetch
    fetcher_mock.fetch.assert_called_once()
    assert len(result) == 1
    assert result[0].product_id == "99"
    assert vv_action.get_cached_action_id() == fake_id
    vv_action.reset_action_id_cache_for_tests()


def test_chain_wires_browser_post_when_fetcher_supports_it() -> None:
    """StoreAware/Camoufox-style fetcher → Strategy A gets post_fn."""
    from scout_api.modules.matching.search_adapters.paraguay import (
        visaovip_action_strategy as vv_action,
    )

    class _FetcherWithBrowserPost:
        def __init__(self) -> None:
            self.fetch_calls = 0
            self.post_calls = 0

        def fetch(self, url: str, **kwargs: object) -> MagicMock:
            self.fetch_calls += 1
            return MagicMock(
                text="<html></html>",
                url=url,
            )

        def browser_post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            data: bytes,
            timeout_ms: int | None = None,
        ) -> tuple[int, str]:
            self.post_calls += 1
            return 200, "unused"

    vv_action.reset_action_id_cache_for_tests()
    vv_action.set_cached_action_id("c" * 40)

    cands_a = [_candidate("99", "S25 Ultra")]
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/x/",
        method="GET",
        prefer_browser=True,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.SUCCESS, cands_a)

    fetcher = _FetcherWithBrowserPost()
    svc = StoreSearchService(fetcher=fetcher)  # type: ignore[arg-type]

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = ""
    settings_mock.store_capability_circuit_enabled = False

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
    ):
        result = svc.search("visaovip", "Samsung Galaxy S25 Ultra")

    assert adapter.try_strategy_a.call_args.kwargs["post_fn"] is not None
    assert len(result) == 1
    vv_action.reset_action_id_cache_for_tests()


def test_find_browser_post_walks_http_first_wrappers() -> None:
    from scout_api.modules.crawler.services.store_aware_fetcher import find_browser_post

    class _CamoufoxLike:
        def browser_post(self, *args: object, **kwargs: object) -> tuple[int, str]:
            return 200, "ok"

    class _Wrapper:
        def __init__(self, browser: object) -> None:
            self._browser = browser

    leaf = _CamoufoxLike()
    stack = _Wrapper(_Wrapper(_Wrapper(leaf)))
    resolved = find_browser_post(stack)
    assert resolved is not None
    assert resolved.__self__ is leaf  # type: ignore[attr-defined]
    assert find_browser_post(MagicMock()) is None


def test_chain_unavailable_invalidates_cache_and_falls_to_b() -> None:
    from scout_api.modules.matching.search_adapters.paraguay import (
        visaovip_action_strategy as vv_action,
    )

    vv_action.reset_action_id_cache_for_tests()
    vv_action.set_cached_action_id("b" * 40)

    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/board/",
        method="GET",
        prefer_browser=True,
    )
    adapter.try_strategy_a.return_value = (StrategyResult.UNAVAILABLE, None)
    adapter.parse_candidates.return_value = [_candidate("41749", "B650M")]
    adapter.classify_empty_result.return_value = "unknown"

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html><body><a href='/prod/x/y/41749/'>board</a></body></html>",
        url="https://www.visaovip.com/busca/termo/board/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = True
    settings_mock.visaovip_search_action_id = ""
    settings_mock.store_capability_circuit_enabled = False

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        result = svc.search("visaovip", "B650M")

    assert vv_action.get_cached_action_id() is None
    fetcher_mock.fetch.assert_called_once()
    assert len(result) == 1
    vv_action.reset_action_id_cache_for_tests()


def test_resolve_action_id_bootstrap_and_cache() -> None:
    from scout_api.modules.matching.search_adapters.paraguay import (
        visaovip_action_strategy as vv_action,
    )

    vv_action.reset_action_id_cache_for_tests()
    assert vv_action.resolve_action_id(bootstrap_id="c" * 40) == "c" * 40
    assert vv_action.get_cached_action_id() == "c" * 40
    # Cache hit without bootstrap
    assert vv_action.resolve_action_id() == "c" * 40
    vv_action.invalidate_cached_action_id(reason="test")
    assert vv_action.get_cached_action_id() is None
    vv_action.reset_action_id_cache_for_tests()


# ---------------------------------------------------------------------------
# Error taxonomy: WAF/challenge → UPSTREAM_WAF_BLOCKED
# ---------------------------------------------------------------------------


def test_serp_challenge_emits_upstream_waf_blocked() -> None:
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/gpu/",
        method="GET",
        prefer_browser=False,
    )

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html>challenge page</html>",
        url="https://www.visaovip.com/busca/termo/gpu/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = False
    settings_mock.visaovip_search_action_id = ""

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=True,  # simulate WAF/challenge
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
    ):
        with pytest.raises(RequestError) as exc_info:
            svc.search("visaovip", "GPU")

    assert exc_info.value.code == "UPSTREAM_WAF_BLOCKED"
    # Message must be human-readable, not raw snake_case
    assert (
        "WAF" in exc_info.value.args[0] or "bloqueada" in exc_info.value.args[0].lower()
    )


# ---------------------------------------------------------------------------
# Error taxonomy: incomplete SERP → SEARCH_INCOMPLETE_RESPONSE
# ---------------------------------------------------------------------------


def test_serp_incomplete_emits_search_incomplete_response() -> None:
    adapter = MagicMock()
    adapter.build_search_request.return_value = MagicMock(
        url="https://www.visaovip.com/busca/termo/mb/",
        method="GET",
        prefer_browser=False,
    )
    adapter.parse_candidates.return_value = []
    adapter.classify_empty_result.return_value = "incomplete"

    fetcher_mock = MagicMock()
    fetcher_mock.fetch.return_value = MagicMock(
        text="<html><body></body></html>",
        url="https://www.visaovip.com/busca/termo/mb/",
    )
    svc = StoreSearchService(fetcher=fetcher_mock)

    settings_mock = MagicMock()
    settings_mock.visaovip_search_action_enabled = False
    settings_mock.visaovip_search_action_id = ""

    with (
        patch(
            "scout_api.modules.matching.store_search_service.resolve_search_adapter",
            return_value=adapter,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.get_settings",
            return_value=settings_mock,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_challenge_page",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_amazon_robot_check",
            return_value=False,
        ),
        patch(
            "scout_api.modules.matching.store_search_service.is_auth_wall_page",
            return_value=False,
        ),
    ):
        with pytest.raises(RequestError) as exc_info:
            svc.search("visaovip", "Motherboard B650")

    assert exc_info.value.code == "SEARCH_INCOMPLETE_RESPONSE"
    # Message must be human-readable
    msg = exc_info.value.args[0].lower()
    assert "incompleta" in msg or "bloqueada" in msg


# ---------------------------------------------------------------------------
# PROXY_FALLBACK_ERROR_CODES includes new taxonomy codes
# ---------------------------------------------------------------------------


def test_proxy_fallback_codes_include_new_taxonomy() -> None:
    from scout_api.modules.crawler.core.exceptions import PROXY_FALLBACK_ERROR_CODES

    assert "UPSTREAM_WAF_BLOCKED" in PROXY_FALLBACK_ERROR_CODES
    assert "SEARCH_INCOMPLETE_RESPONSE" in PROXY_FALLBACK_ERROR_CODES
    # Legacy alias preserved
    assert "UPSTREAM_BLOCKED" in PROXY_FALLBACK_ERROR_CODES
