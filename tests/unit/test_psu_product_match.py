from __future__ import annotations

from decimal import Decimal

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    ProductIdentity,
    build_search_queries,
    extract_all_mpn_forms,
    identity_from_price_item,
)

SOURCE_TITLE = "Fonte MSI MAG A650BNL, 650W, 80 Plus Bronze, Preto, 306-7ZPAX39-HH9"


def _item(
    title: str,
    *,
    brand: str | None = "MSI",
    model: str | None = None,
    specs: dict | None = None,
):
    return ProductPriceItem(
        store="test",
        country="BR",
        product_id="1",
        title=title,
        brand=brand,
        model=model,
        url="https://example.com/product",
        canonical_url="https://example.com/product",
        currency="BRL",
        price=Decimal("400.00"),
        available=True,
        metadata={"specifications": specs or {}},
    )


def _identity(
    title: str, model: str | None = None, *, brand: str | None = "MSI"
) -> ProductIdentity:
    return identity_from_price_item(_item(title, model=model, brand=brand))


def test_psu_identity_preserves_model_mpn_and_structured_attributes() -> None:
    identity = _identity(SOURCE_TITLE)

    assert identity.category == "psu"
    assert identity.model and "a650bnl" in identity.model
    assert identity.mpn == "3067zpax39hh9"
    assert identity.category_attrs["wattage"] == "650 w"
    assert identity.category_attrs["efficiency"] == "80 plus bronze"

    pdp_identity = identity_from_price_item(
        _item(
            "Fonte MSI MAG A650BNL, 650W, 80 Plus Bronze, Preto",
            specs={
                "Potência nominal": "650",
                "Certificação de eficiência": "80 PLUS Bronze",
            },
        )
    )
    assert pdp_identity.category_attrs["wattage"] == "650 w"
    assert MatchingEngine().score(identity, pdp_identity).decision == "auto_match"

    from scout_api.modules.crawler.utils.product_attributes import (
        resolve_product_identity,
    )

    resolved = resolve_product_identity(
        specifications={"model": "MAG A650BNL"},
        title="Fonte ATX 650W 80 Plus Bronze MSI MAG A650BNL",
        category="psu",
    )
    assert resolved.value("model") == "MAG A650BNL"


def test_psu_mpn_query_and_progressive_discovery_queries() -> None:
    identity = _identity(SOURCE_TITLE)
    queries = build_search_queries(identity, locale="pt-BR")

    assert queries[0].startswith("fonte msi mag a650bnl")
    assert any("msi mag a650bnl" in query.casefold() for query in queries)
    assert any(query == "306-7ZPAX39-HH9" for query in queries)
    assert "a650bnl" in queries[1].casefold()
    assert len(queries) <= 5


def test_psu_matching_accepts_same_model_without_title_mpn() -> None:
    source = _identity(SOURCE_TITLE)
    candidate = _identity("Fonte ATX 650W 80 Plus Bronze MSI MAG A650BNL")
    score = MatchingEngine().score(source, candidate)

    assert score.decision == "auto_match"


def test_psu_category_prefix_is_not_misread_as_candidate_brand() -> None:
    candidate = identity_from_price_item(
        _item(
            "fonte msi mag a650bnl 650w 80 plus bronze preto",
            brand=None,
            model=None,
        )
    )
    assert candidate.brand == "msi"
    assert candidate.model == "a650bnl"

    kabum_title = "Fonte De Alimentacao Msi Mag A650bnl White 650w 80 Plus Bronze"
    kabum_candidate = identity_from_price_item(
        _item(kabum_title, brand="Fonte", model=None)
    )
    assert kabum_candidate.brand == "msi"
    assert kabum_candidate.model == "a650bnl"


def test_psu_model_and_wattage_conflicts_are_hard_rejections() -> None:
    source = _identity(SOURCE_TITLE)
    for title in (
        "Fonte MSI MAG A650BN 650W 80 Plus Bronze",
        "Fonte MSI MAG A650GL 650W 80 Plus Bronze",
        "Fonte MSI MAG A750BNL 750W 80 Plus Bronze",
        "Fonte MSI MAG A650BNL 750W 80 Plus Bronze",
        "Fonte MSI MAG A650BNL 650W 80 Plus Gold",
    ):
        assert MatchingEngine().score(source, _identity(title)).decision == "reject"


def test_psu_missing_attributes_are_unknown_and_efficiency_is_normalized() -> None:
    source = _identity(SOURCE_TITLE)
    missing = _identity("Fonte MSI MAG A650BNL")
    equivalent = _identity("Fonte ATX MSI MAG A650BNL 650 W 80+ BRONZE")

    assert MatchingEngine().score(source, missing).decision != "reject"
    assert MatchingEngine().score(source, equivalent).decision == "auto_match"
    assert equivalent.category_attrs["efficiency"] == "80 plus bronze"
    assert equivalent.category_attrs["wattage"] == "650 w"


def test_mpn_extractor_accepts_digit_prefixed_manufacturer_code() -> None:
    assert ("3067zpax39hh9", "306-7ZPAX39-HH9") in extract_all_mpn_forms(SOURCE_TITLE)
    pdp = _identity(
        "Fonte ATX 650W 80 Plus Bronze MSI MAG A650BNL",
    )
    pdp_with_labeled_mpn = identity_from_price_item(
        _item(
            "Fonte ATX 650W 80 Plus Bronze MSI MAG A650BNL",
            specs={"text": "<p>Part Number: 306-7ZPAX39-HH9</p>"},
        )
    )
    assert pdp.mpn is None
    assert pdp_with_labeled_mpn.mpn == "3067zpax39hh9"


def test_psu_conflicting_mpn_rejects_but_missing_mpn_does_not() -> None:
    source = _identity(SOURCE_TITLE)
    candidate = _identity(
        "Fonte MSI MAG A650BNL 650W 80 Plus Bronze",
        model="MAG A650BNL",
    )
    assert MatchingEngine().score(source, candidate).decision == "auto_match"

    conflict = _identity(
        "Fonte MSI MAG A650BNL 650W 80 Plus Bronze 306-7ZPAX39-HH8",
        model="MAG A650BNL",
    )
    assert MatchingEngine().score(source, conflict).decision == "reject"


def test_psu_extracts_complete_model_codes_across_brands() -> None:
    for title, brand, model in (
        ("Fonte Corsair CV650 650W 80 Plus Bronze", "Corsair", "cv650"),
        ("Cooler Master MWE 650 Bronze 650W Power Supply", "Cooler Master", "mwe"),
        ("Fonte XPG Pylon 650W 80 Plus Bronze", "XPG", "pylon"),
    ):
        identity = _identity(title, brand=brand)
        assert identity.category == "psu"
        assert model in (identity.model or "")
