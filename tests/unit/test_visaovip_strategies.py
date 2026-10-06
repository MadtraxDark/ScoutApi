"""TDD — Visão VIP Strategy A (searchProducts Server Action).

Step 1: Failing tests for parse success, zero results, invalid contract,
action ID discovery, HTTP call, and adapter flag.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# Import paths validated before implementation exists (step 1 = RED).
from scout_api.modules.matching.search_adapters.paraguay.visaovip_action_strategy import (  # noqa: E501
    StrategyResult,
    call_search_products,
    discover_action_id_from_chunk_js,
    parse_rsc_response,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_BUILD_ID = "QAm14Voexwwlur451SrkG"


def _make_rsc_body(products: list[dict], total_count: int | None = None) -> str:
    """Build a minimal RSC response body matching the real Visão VIP format."""
    if total_count is None:
        total_count = len(products)
    payload = {
        "products": products,
        "facets": {"brands": [], "characteristics": []},
        "totalCount": total_count,
        "currentPage": 0,
        "totalPages": max(0, (total_count + 23) // 24),
    }
    return (
        f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
        f"1:{json.dumps(payload)}\n"
    )


_SAMPLE_PRODUCTS: list[dict] = [
    {
        "sku": 41749,
        "description": "Placa Mãe ASUS TUF Gaming B650M-E Wi-Fi Socket AM5 DDR5",
        "category": "Placas Mãe AMD",
        "imageUrl": "https://cdn.visaovip.com/img/prod/41749/thumb.jpg",
        "price": 130.00,
        "isAvailable": True,
        "isPromotion": False,
        "isGamer": False,
        "isHighlight": False,
    },
    {
        "sku": 54574,
        "description": "Placa Mãe ASUS ROG Strix B650E-E Gaming WiFi",
        "category": "Placas Mãe AMD",
        "imageUrl": "https://cdn.visaovip.com/img/prod/54574/thumb.jpg",
        "price": 180.00,
        "isAvailable": True,
        "isPromotion": False,
        "isGamer": False,
        "isHighlight": True,
    },
]


# ---------------------------------------------------------------------------
# StrategyResult enum
# ---------------------------------------------------------------------------


class TestStrategyResultEnum:
    def test_all_values_are_strings(self) -> None:
        for member in StrategyResult:
            assert isinstance(member.value, str)

    def test_expected_members_exist(self) -> None:
        assert StrategyResult.SUCCESS == "success"
        assert StrategyResult.NO_RESULTS == "no_results"
        assert StrategyResult.BLOCKED == "blocked"
        assert StrategyResult.UNAVAILABLE == "unavailable"
        assert StrategyResult.INVALID_RESPONSE == "invalid_response"
        assert StrategyResult.ERROR == "error"


# ---------------------------------------------------------------------------
# parse_rsc_response
# ---------------------------------------------------------------------------


class TestParseRscResponse:
    """Unit tests for RSC body parsing — pure function."""

    def test_success_with_two_products(self) -> None:
        body = _make_rsc_body(_SAMPLE_PRODUCTS)
        result, candidates, build_id = parse_rsc_response(body)
        assert result == StrategyResult.SUCCESS
        assert candidates is not None
        assert len(candidates) == 2
        assert candidates[0].product_id == "41749"
        assert "B650M-E" in (candidates[0].title or "")
        assert candidates[1].product_id == "54574"
        assert build_id == _BUILD_ID

    def test_candidate_url_ends_with_product_id(self) -> None:
        body = _make_rsc_body(_SAMPLE_PRODUCTS[:1])
        _, candidates, _ = parse_rsc_response(body)
        assert candidates
        url = candidates[0].url
        assert url.endswith("/41749/")
        assert "/prod/" in url

    def test_candidate_source_metadata(self) -> None:
        body = _make_rsc_body(_SAMPLE_PRODUCTS[:1])
        _, candidates, _ = parse_rsc_response(body)
        assert candidates
        assert candidates[0].metadata.get("source") == "visaovip-action"

    def test_zero_results_is_no_results_not_error(self) -> None:
        body = _make_rsc_body([])
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.NO_RESULTS
        # Must be empty list (not None) — fallback B must distinguish from INVALID
        assert candidates == []

    def test_server_error_digest_is_blocked(self) -> None:
        """RSC server error (often from rotated action ID) → BLOCKED."""
        body = (
            f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
            '1:E{"digest":"1080602043"}\n'
        )
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.BLOCKED
        assert candidates is None

    def test_missing_products_key_is_invalid_response(self) -> None:
        """Contract violation: response lacks 'products' key → INVALID_RESPONSE."""
        body = (
            f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
            '1:{"facets":{},"totalCount":0}\n'
        )
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.INVALID_RESPONSE
        assert candidates is None

    def test_products_not_a_list_is_invalid_response(self) -> None:
        body = (
            f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
            '1:{"products":null,"totalCount":0}\n'
        )
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.INVALID_RESPONSE
        assert candidates is None

    def test_malformed_json_is_invalid_response(self) -> None:
        body = (
            f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
            "1:{broken json here\n"
        )
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.INVALID_RESPONSE
        assert candidates is None

    def test_garbage_body_no_data_line_is_invalid_response(self) -> None:
        result, candidates, _ = parse_rsc_response("not valid rsc data\n!@#$\n")
        assert result == StrategyResult.INVALID_RESPONSE
        assert candidates is None

    def test_empty_body_is_unavailable(self) -> None:
        result, _, _ = parse_rsc_response("")
        assert result == StrategyResult.UNAVAILABLE

    def test_build_id_extracted(self) -> None:
        body = _make_rsc_body([])
        _, _, build_id = parse_rsc_response(body)
        assert build_id == _BUILD_ID

    def test_no_build_id_returns_none(self) -> None:
        body = '0:{}\n1:{"products":[],"totalCount":0}\n'
        _, _, build_id = parse_rsc_response(body)
        assert build_id is None

    def test_real_empty_body_fixture(self) -> None:
        """Matches action_empty_arr.body captured in Task 8 probe."""
        body = (
            '0:{"a":"$@1","f":"","b":"QAm14Voexwwlur451SrkG","q":"","i":false}\n'
            '1:{"products":[],"facets":{"brands":[],"characteristics":[],'
            '"categories":[],"promotion":[],"highlight":[],"stock":[],'
            '"gamer":[],"lowestPrice":"$undefined","highestPrice":"$undefined"},'
            '"totalCount":0,"currentPage":0,"totalPages":0}\n'
        )
        result, candidates, build_id = parse_rsc_response(body)
        assert result == StrategyResult.NO_RESULTS
        assert candidates == []
        assert build_id == "QAm14Voexwwlur451SrkG"

    def test_products_capped_at_20(self) -> None:
        """Response with >20 products must be capped to 20 candidates."""
        many = [
            {"sku": i, "description": f"Product {i}", "category": "Cat"}
            for i in range(1, 30)
        ]
        body = _make_rsc_body(many)
        result, candidates, _ = parse_rsc_response(body)
        assert result == StrategyResult.SUCCESS
        assert candidates is not None
        assert len(candidates) == 20


# ---------------------------------------------------------------------------
# discover_action_id_from_chunk_js
# ---------------------------------------------------------------------------


class TestDiscoverActionId:
    """Unit tests for chunk JS action ID discovery — pure function."""

    _SEARCH_PRODUCTS_CHUNK = (
        'createServerReference("7f674263c13a9d8d28d0768c8016b1791b8051502a",'
        'd.callServer,void 0,d.findSourceMapURL,"searchProducts")'
    )
    _SEARCH_FACETS_CHUNK = (
        'createServerReference("784f2ef5d5603dc817ded8e27c904518fc45af1b63",'
        'd.callServer,void 0,d.findSourceMapURL,"searchFacets")'
    )

    def test_extracts_search_products_id(self) -> None:
        action_id = discover_action_id_from_chunk_js(self._SEARCH_PRODUCTS_CHUNK)
        assert action_id == "7f674263c13a9d8d28d0768c8016b1791b8051502a"

    def test_does_not_extract_other_action_names(self) -> None:
        """Must only match 'searchProducts', not 'searchFacets' or other actions."""
        action_id = discover_action_id_from_chunk_js(self._SEARCH_FACETS_CHUNK)
        assert action_id is None

    def test_no_match_returns_none(self) -> None:
        action_id = discover_action_id_from_chunk_js("no action id here at all")
        assert action_id is None

    def test_real_chunk_serp3_fixture(self) -> None:
        """Validates against actual JS chunk captured in Task 8 probe."""
        # The exact string from chunk_serp3.body (Turbopack form)
        _id = "7f674263c13a9d8d28d0768c8016b1791b8051502a"
        chunk_snippet = (
            f'let p=(0,d.createServerReference)("{_id}",'
            'd.callServer,void 0,d.findSourceMapURL,"searchProducts")'
        )
        action_id = discover_action_id_from_chunk_js(chunk_snippet)
        assert action_id == _id


# ---------------------------------------------------------------------------
# call_search_products — mocked HTTP
# ---------------------------------------------------------------------------


def _make_mock_client(status_code: int, response_text: str) -> MagicMock:
    """Build a mock httpx.Client context manager."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = response_text

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    return mock_client


_HTTPX_CLIENT_PATH = (
    "scout_api.modules.matching.search_adapters"
    ".paraguay.visaovip_action_strategy.httpx.Client"
)


class TestCallSearchProducts:
    """Unit tests for call_search_products (mocked HTTP)."""

    def test_404_returns_unavailable(self) -> None:
        """HTTP 404 = action ID rotated → UNAVAILABLE (not ERROR, not BLOCKED)."""
        mock_c = _make_mock_client(404, "not found")
        with patch(_HTTPX_CLIENT_PATH, return_value=mock_c):
            result, candidates = call_search_products(
                "asus-tuf-gaming-b650m-e-wifi", "stale_id"
            )
        assert result == StrategyResult.UNAVAILABLE
        assert candidates is None

    def test_200_with_products_returns_success(self) -> None:
        body = _make_rsc_body(_SAMPLE_PRODUCTS)
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(200, body)):
            result, candidates = call_search_products(
                "asus-tuf-gaming-b650m-e-wifi", "fresh_id"
            )
        assert result == StrategyResult.SUCCESS
        assert candidates is not None
        assert len(candidates) == 2

    def test_200_empty_returns_no_results(self) -> None:
        body = _make_rsc_body([])
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(200, body)):
            result, candidates = call_search_products("samsung-galaxy-s25", "fresh_id")
        assert result == StrategyResult.NO_RESULTS
        assert candidates == []

    def test_200_server_error_digest_returns_blocked(self) -> None:
        body = (
            f'0:{{"a":"$@1","f":"","b":"{_BUILD_ID}","q":"","i":false}}\n'
            '1:E{"digest":"1080602043"}\n'
        )
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(200, body)):
            result, candidates = call_search_products(
                "ryzen-5800x3d", "stale_id_causes_server_error"
            )
        assert result == StrategyResult.BLOCKED
        assert candidates is None

    def test_500_returns_blocked(self) -> None:
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(500, "error")):
            result, candidates = call_search_products("query", "some_id")
        assert result == StrategyResult.BLOCKED
        assert candidates is None

    def test_403_returns_blocked(self) -> None:
        cf_html = "<html><title>Attention Required! | Cloudflare</title></html>"
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(403, cf_html)):
            result, candidates = call_search_products("query", "some_id")
        assert result == StrategyResult.BLOCKED
        assert candidates is None

    def test_post_fn_bypasses_httpx(self) -> None:
        """Camoufox browser_post path — no bare httpx Client."""
        body = _make_rsc_body(_SAMPLE_PRODUCTS)

        def _post_fn(url: str, headers: dict[str, str], data: bytes) -> tuple[int, str]:
            assert "Next-Action" in headers
            assert isinstance(data, (bytes, bytearray))
            return 200, body

        with patch(_HTTPX_CLIENT_PATH) as mock_client_cls:
            result, candidates = call_search_products(
                "asus-tuf-gaming-b650m-e-wifi",
                "fresh_id",
                post_fn=_post_fn,
            )
        mock_client_cls.assert_not_called()
        assert result == StrategyResult.SUCCESS
        assert candidates is not None
        assert len(candidates) == 2

    def test_network_exception_returns_error(self) -> None:
        import httpx as _httpx

        with patch(_HTTPX_CLIENT_PATH, side_effect=_httpx.ConnectError("timeout")):
            result, candidates = call_search_products("query", "some_id")
        assert result == StrategyResult.ERROR
        assert candidates is None

    def test_payload_uses_correct_slug_and_args(self) -> None:
        """Validates the POST payload arg order and content-type header."""
        body = _make_rsc_body([])
        mock_client = _make_mock_client(200, body)

        with patch(_HTTPX_CLIENT_PATH, return_value=mock_client):
            call_search_products("asus-tuf-b650m", "some_id")

        _, kwargs = mock_client.post.call_args
        content = kwargs.get("content") or mock_client.post.call_args[1].get("content")
        if content is None:
            # positional content arg
            pos_args = mock_client.post.call_args[0]
            content = pos_args[1] if len(pos_args) > 1 else b""
        if isinstance(content, bytes):
            args = json.loads(content.decode("utf-8"))
        else:
            args = json.loads(content)
        assert args[0] == "asus-tuf-b650m"  # searchTerm = slug
        assert args[1] == "termo"
        assert args[2] == []
        assert args[3] == "pt-BR"
        assert args[6] == "all"

        headers = (
            kwargs.get("headers") or mock_client.post.call_args[1].get("headers") or {}
        )
        assert "Next-Action" in headers
        assert headers["Next-Action"] == "some_id"
        assert headers.get("Content-Type") == "text/plain;charset=UTF-8"


# ---------------------------------------------------------------------------
# Adapter flag integration
# ---------------------------------------------------------------------------


class TestVisaoVipAdapterStrategyA:
    """Test try_strategy_a method on the adapter."""

    def test_adapter_has_try_strategy_a_method(self) -> None:
        from scout_api.modules.matching.search_adapters.paraguay.visaovip import (
            VisaoVipSearchAdapter,
        )

        adapter = VisaoVipSearchAdapter()
        assert hasattr(adapter, "try_strategy_a")

    def test_try_strategy_a_disabled_returns_unavailable(self) -> None:
        from scout_api.modules.matching.search_adapters.paraguay.visaovip import (
            VisaoVipSearchAdapter,
        )

        adapter = VisaoVipSearchAdapter()
        result, candidates = adapter.try_strategy_a(
            "B650M E WIFI",
            action_id="any_id",
            enabled=False,
        )
        assert result == StrategyResult.UNAVAILABLE
        assert candidates is None

    def test_try_strategy_a_enabled_calls_http(self) -> None:
        from scout_api.modules.matching.search_adapters.paraguay.visaovip import (
            VisaoVipSearchAdapter,
        )

        body = _make_rsc_body(_SAMPLE_PRODUCTS)
        adapter = VisaoVipSearchAdapter()

        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(200, body)):
            result, candidates = adapter.try_strategy_a(
                "B650M E WIFI",
                action_id="fresh_id",
                enabled=True,
            )
        assert result == StrategyResult.SUCCESS
        assert candidates is not None
        assert len(candidates) == 2

    def test_try_strategy_a_invalid_response_does_not_raise(self) -> None:
        """Invalid contract → INVALID_RESPONSE (fallback B trigger), never exception."""
        from scout_api.modules.matching.search_adapters.paraguay.visaovip import (
            VisaoVipSearchAdapter,
        )

        adapter = VisaoVipSearchAdapter()
        bad_body = "garbage body text"
        with patch(_HTTPX_CLIENT_PATH, return_value=_make_mock_client(200, bad_body)):
            result, candidates = adapter.try_strategy_a(
                "B650M E WIFI",
                action_id="id",
                enabled=True,
            )
        # Must be UNAVAILABLE or INVALID_RESPONSE — never SUCCESS or ERROR via exception
        assert result in (StrategyResult.UNAVAILABLE, StrategyResult.INVALID_RESPONSE)
