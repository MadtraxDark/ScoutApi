"""Color identity extraction / Nissei title-position regressions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    COLOR_CANONICAL,
    COLOR_FAMILY,
    _color_label_from_title,
    extract_connectivity_from_title,
    extract_market_qualifier_from_title,
    identity_from_price_item,
    identity_reference_item,
    normalize_variant_value,
    resolve_model,
    variants_equal,
)


def _phone(
    title: str,
    *,
    brand: str = "Apple",
    model: str | None = "iPhone 17",
    color: str | None = None,
    storage: str = "256 GB",
) -> ProductPriceItem:
    attrs: dict[str, str] = {"storage": storage}
    parts = [f"storage: {storage}"]
    if color:
        attrs["color"] = color
        parts.insert(0, f"color: {color}")
    return ProductPriceItem(
        store="nissei",
        country="PY",
        product_id="nissei-color",
        title=title,
        brand=brand,
        model=model,
        variant="; ".join(parts),
        url="https://nissei.com/br/x",
        canonical_url="https://nissei.com/br/x",
        currency="USD",
        price=Decimal("990"),
        available=True,
        availability="available",
        metadata={"variant": attrs, "category": "smartphone"},
    )


def _ref_sage() -> object:
    item = identity_reference_item(
        "Apple iPhone 17 256GB Sage",
        brand="Apple",
        model="iPhone 17",
        category="smartphone",
    )
    item = item.model_copy(
        update={
            "variant": "color: Sage; storage: 256 GB",
            "metadata": {
                "variant": {"color": "Sage", "storage": "256 GB"},
                "category": "smartphone",
            },
        }
    )
    return identity_from_price_item(item)


@pytest.mark.parametrize(
    "title",
    [
        "Apple iPhone 17 256GB Sage",
        "Apple iPhone 17 Sage 256GB",
        "Sage Apple iPhone 17 256GB",
        "Apple iPhone 17 MG6C4VC/A A3519 256GB / eSIM - Sage",
        "Apple iPhone 17 256GB / eSIM - Sage (americano)",
    ],
)
def test_nissei_title_positions_yield_same_color_identity(title: str) -> None:
    assert _color_label_from_title(title) == "sage"
    ident = identity_from_price_item(_phone(title, color=None, model="iPhone 17 Sage"))
    assert ident.model == "iphone17"
    assert normalize_variant_value("color", ident.variant_attrs["color"]) == "sage"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("Sage", "sage"),
        ("Burgundy", "burgundy"),
        ("vinho", "burgundy"),
        ("bordô", "burgundy"),
        ("Black", "black"),
        ("Preto", "black"),
        ("Negro", "black"),
        ("White", "white"),
        ("Titanium Black", "black"),
        ("Midnight Black", "midnight black"),
        ("Alpine Green", "alpine green"),
        ("Desert Titanium", "desert titanium"),
    ],
)
def test_commercial_color_canonicalization(raw: str, canonical: str) -> None:
    assert normalize_variant_value("color", raw) == canonical
    assert fold_in_gazetteer(raw)


def fold_in_gazetteer(raw: str) -> bool:
    from scout_api.modules.matching.identity import fold_text

    label = _color_label_from_title(raw)
    return label is not None or fold_text(raw) in COLOR_CANONICAL


def test_sage_family_hint_is_not_basic_green_collapse() -> None:
    assert normalize_variant_value("color", "Sage") == "sage"
    assert COLOR_FAMILY.get("sage") == "green"
    assert not variants_equal("color", "Sage", "Green")


def test_burgundy_aliases_and_conflict_with_sage() -> None:
    assert variants_equal("color", "Burgundy", "vinho")
    assert not variants_equal("color", "Sage", "Burgundy")
    score = MatchingEngine().score(
        _ref_sage(),  # type: ignore[arg-type]
        identity_from_price_item(
            _phone(
                "Apple iPhone 17 256GB / eSIM - Burgundy",
                color="Burgundy",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "variant_mismatch" for r in score.reasons)


def test_sage_polluted_model_auto_matches() -> None:
    score = MatchingEngine().score(
        _ref_sage(),  # type: ignore[arg-type]
        identity_from_price_item(
            _phone(
                "Apple iPhone 17 MG6C4VC/A A3519 256GB / eSIM - Sage",
                model="iPhone 17 Sage",
                color="Sage",
            )
        ),
    )
    assert score.decision == "auto_match"
    assert score.confidence >= Decimal("0.92")


def test_missing_color_is_neutral() -> None:
    score = MatchingEngine().score(
        _ref_sage(),  # type: ignore[arg-type]
        identity_from_price_item(
            _phone("Apple iPhone 17 A3519 Dual 256GB", color=None)
        ),
    )
    assert score.decision == "auto_match"


def test_storage_conflict_still_rejects() -> None:
    score = MatchingEngine().score(
        _ref_sage(),  # type: ignore[arg-type]
        identity_from_price_item(
            _phone(
                "Apple iPhone 17 512GB Sage",
                color="Sage",
                storage="512 GB",
            )
        ),
    )
    assert score.decision == "reject"


def test_pro_vs_base_still_rejects() -> None:
    score = MatchingEngine().score(
        _ref_sage(),  # type: ignore[arg-type]
        identity_from_price_item(
            _phone(
                "Apple iPhone 17 Pro 256GB Sage",
                model="iPhone 17 Pro",
                color="Sage",
            )
        ),
    )
    assert score.decision == "reject"


def test_model_number_and_mpn_stripped_from_model() -> None:
    assert resolve_model("iPhone 17 A3519", "Apple iPhone 17 A3519") == "iphone17"
    assert (
        resolve_model("iPhone 17 MG6C4VC/A", "Apple iPhone 17 MG6C4VC/A Sage")
        == "iphone17"
    )


def test_americano_and_esim_extracted_without_polluting_model() -> None:
    title = "Apple iPhone 17 256GB / eSIM - Sage (americano)"
    ident = identity_from_price_item(_phone(title, model="iPhone 17", color=None))
    assert ident.model == "iphone17"
    assert extract_connectivity_from_title(title) == "esim"
    assert extract_market_qualifier_from_title(title) == "americano"
    assert ident.connectivity == "esim"
    assert ident.market_variant == "americano"


def test_samsung_color_at_end_generic() -> None:
    title = "Samsung Galaxy S25 Ultra 256GB - Titanium Black"
    ident = identity_from_price_item(
        ProductPriceItem(
            store="nissei",
            country="PY",
            product_id="s25",
            title=title,
            brand="Samsung",
            model="Galaxy S25 Ultra Titanium Black",
            url="https://nissei.com/br/s",
            canonical_url="https://nissei.com/br/s",
            currency="USD",
            price=Decimal("1200"),
            available=True,
            availability="available",
            metadata={"category": "smartphone"},
        )
    )
    assert ident.model == "galaxys25ultra"
    assert normalize_variant_value("color", ident.variant_attrs["color"]) == "black"


def test_motorola_multiword_color() -> None:
    title = "Motorola Edge 50 Ultra 512GB - Midnight Black"
    assert _color_label_from_title(title) == "midnight black"
    assert normalize_variant_value("color", "Midnight Black") == "midnight black"


def test_prefer_identity_aligned_candidates_boosts_matching_color() -> None:
    from scout_api.modules.matching.identity import (
        ProductIdentity,
        prefer_identity_aligned_candidates,
    )
    from scout_api.modules.matching.search_candidate import SearchCandidate

    identity = ProductIdentity(
        gtin=None,
        brand="apple",
        model="iphone18promax",
        title="iPhone 18 Pro Max Preto",
        title_normalized="iphone 18 pro max preto",
        variant_attrs={"color": "preto", "storage": "2tb"},
    )
    glacial = SearchCandidate(
        url="https://www.amazon.com.br/dp/B0HJB92JX5",
        title="Apple iPhone 18 Pro Max 2 TB — Glacial",
        product_id="B0HJB92JX5",
    )
    preto = SearchCandidate(
        url="https://www.amazon.com.br/dp/B0HJBCQ9B7",
        title="Apple iPhone 18 Pro Max 2 TB — Preto",
        product_id="B0HJBCQ9B7",
    )
    ranked = prefer_identity_aligned_candidates(identity, [glacial, preto])
    assert ranked[0].product_id == "B0HJBCQ9B7"


def test_glacial_aliases_to_glacier() -> None:
    assert normalize_variant_value("color", "Glacial") == "glacier"
    assert normalize_variant_value("color", "glaciar") == "glacier"
    assert COLOR_CANONICAL["glacial"] == "glacier"
    assert COLOR_FAMILY["glacier"] == "blue"
    assert not variants_equal("color", "preto", "glacial")


def test_preto_vs_glacial_is_hard_reject_not_review() -> None:
    """Amazon BR sibling Glacial must not land as 'Requer revisão' vs Preto."""
    ref_item = identity_reference_item(
        'iPhone 18 Pro Max Apple 2TB, Câmera de 48MP, A20 Pro, '
        'Tela 6.9" Super Retina XDR, Preto',
        brand="Apple",
        model="iPhone 18 Pro Max",
        category="smartphone",
    ).model_copy(
        update={
            "price": Decimal("21000.00"),
            "currency": "BRL",
            "variant": "color: Preto; storage: 2 TB",
            "metadata": {
                "variant": {"color": "Preto", "storage": "2 TB"},
                "category": "smartphone",
            },
        }
    )
    ref = identity_from_price_item(ref_item)
    glacial = identity_from_price_item(
        ProductPriceItem(
            store="amazon",
            country="BR",
            product_id="B0HJB92JX5",
            title="Apple iPhone 18 Pro Max 2 TB — Glacial",
            brand="Apple",
            model="iPhone 18 Pro Max",
            url="https://www.amazon.com.br/dp/B0HJB92JX5",
            canonical_url="https://www.amazon.com.br/dp/B0HJB92JX5",
            currency="BRL",
            price=Decimal("19799.10"),
            available=True,
            availability="available",
            metadata={
                "variant": {"color": "Glacial", "storage": "2 TB"},
                "category": "smartphone",
            },
        )
    )
    preto = identity_from_price_item(
        ProductPriceItem(
            store="amazon",
            country="BR",
            product_id="B0HJBCQ9B7",
            title="Apple iPhone 18 Pro Max 2 TB — Preto",
            brand="Apple",
            model="iPhone 18 Pro Max",
            url="https://www.amazon.com.br/dp/B0HJBCQ9B7",
            canonical_url="https://www.amazon.com.br/dp/B0HJBCQ9B7",
            currency="BRL",
            price=Decimal("21999.00"),
            available=True,
            availability="available",
            metadata={
                "variant": {"color": "Preto", "storage": "2 TB"},
                "category": "smartphone",
            },
        )
    )
    engine = MatchingEngine()
    glacial_score = engine.score(ref, glacial)
    assert glacial_score.decision == "reject"
    assert any(
        "color" in (r.detail or "") and r.code == "variant_mismatch"
        for r in glacial_score.reasons
    )
    preto_score = engine.score(ref, preto)
    assert preto_score.decision == "auto_match"
