"""Smartphone query budget regression using a real descriptive catalog title."""

import pytest

from scout_api.modules.crawler.stores import STORE_CONFIGS
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
    looks_like_accessory,
    normalize_variant_value,
)


def test_smartphone_strong_variant_query_fits_store_budget() -> None:
    title = (
        "iPhone 18 Pro Apple 512GB, Câmera de 48MP, A20 Pro, "
        'Tela 6.3" Super Retina XDR, Bordô'
    )
    identity = identity_from_price_item(identity_reference_item(title))

    assert identity.model == "iphone18pro"
    assert identity.variant_attrs == {"storage": "512gb", "color": "bordo"}
    assert (
        normalize_variant_value("color", identity.variant_attrs["color"]) == "burgundy"
    )

    for store_key, expected_color in (("kabum", "bordo"), ("bestbuy", "burgundy")):
        locale = STORE_CONFIGS[store_key].query_locale
        queries = build_search_queries(identity, locale=locale)[:5]
        assert all(
            term in queries[0] for term in ("iphone 18 pro", "512gb", expected_color)
        ), (store_key, queries)
        assert len(queries[0].split()) <= 7
        assert any(
            all(term in query for term in ("iphone 18 pro", "512gb", expected_color))
            for query in queries
        ), (store_key, queries)
        assert any("iphone 18 pro" in query and "512gb" in query for query in queries)


@pytest.mark.parametrize(
    ("candidate_title", "expected"),
    [
        ("Apple iPhone 18 Pro 512GB Bordô", "auto_match"),
        ("iPhone 18 Pro Apple 512 GB Burgundy", "auto_match"),
        ("Apple iPhone 18 Pro Max 512GB Bordô", "reject"),
        ("Apple iPhone 18 Pro 256GB Bordô", "reject"),
        ("Apple iPhone 18 Pro 1TB Bordô", "reject"),
        ("Apple iPhone 18 Pro 512GB Preto", "reject"),
        ("Apple iPhone 17 Pro 512GB Bordô", "reject"),
        ("Apple iPhone 18 512GB Bordô", "reject"),
        ("Open Box Apple iPhone 18 Pro 512GB Burgundy", "reject"),
        ("Used Apple iPhone 18 Pro 512GB Burgundy", "reject"),
    ],
)
def test_smartphone_variant_hard_gates(candidate_title: str, expected: str) -> None:
    reference = identity_from_price_item(
        identity_reference_item("Apple iPhone 18 Pro 512GB Bordô")
    )
    candidate_item = identity_reference_item(candidate_title).model_copy(
        update={"store": "candidate", "product_id": "candidate-1"}
    )
    candidate = identity_from_price_item(candidate_item)
    score = MatchingEngine().score(reference, candidate)
    assert score.decision == expected, (candidate_title, score)


@pytest.mark.parametrize(
    ("candidate_title", "expected"),
    [
        ("Apple iPhone 18 Pro 512GB", "auto_match"),
        ("Apple iPhone 18 Pro 512GB Burgundy AT&T", "review"),
        ("Apple iPhone 18 Pro 512GB Burgundy Unlocked", "auto_match"),
        ("Renewed Apple iPhone 18 Pro 512GB Burgundy", "auto_match"),
    ],
)
def test_smartphone_unknown_and_commercial_dimensions(
    candidate_title: str, expected: str
) -> None:
    reference = identity_from_price_item(
        identity_reference_item("Apple iPhone 18 Pro 512GB Bordô")
    )
    candidate_item = identity_reference_item(candidate_title).model_copy(
        update={"store": "candidate", "product_id": "candidate-1"}
    )
    candidate = identity_from_price_item(candidate_item)

    assert MatchingEngine().score(reference, candidate).decision == expected


def test_spanish_burgundy_variant_from_selected_pdp_matches() -> None:
    reference = identity_from_price_item(
        identity_reference_item("Apple iPhone 18 Pro 512GB Bordô")
    )
    candidate_item = identity_reference_item(
        "Apple iPhone 18 Pro 512GB Burgundy"
    ).model_copy(
        update={
            "store": "candidate",
            "product_id": "candidate-1",
            "variant": "Color: Borgoña",
        }
    )
    candidate = identity_from_price_item(candidate_item)

    assert normalize_variant_value("color", candidate.variant_attrs["color"]) == (
        "burgundy"
    )
    assert MatchingEngine().score(reference, candidate).decision == "auto_match"


def test_english_screen_protector_is_accessory() -> None:
    assert looks_like_accessory(
        "TORRAS Screen Protector for iPhone 18 Pro",
        reference_title="Apple iPhone 18 Pro 512GB Bordô",
    )
