"""Unit tests for GTIN normalization and MatchingEngine decisions."""

from __future__ import annotations

from decimal import Decimal

from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    ProductIdentity,
    looks_like_accessory,
    normalize_gtin,
)


def _identity(**kwargs: object) -> ProductIdentity:
    defaults: dict[str, object] = {
        "gtin": None,
        "brand": None,
        "model": None,
        "title": "Produto",
        "title_normalized": "produto",
        "variant_attrs": {},
        "store": None,
        "product_id": None,
        "price": Decimal("100.00"),
        "currency": "BRL",
        "mpn": None,
        "mpn_display": None,
    }
    defaults.update(kwargs)
    return ProductIdentity(**defaults)  # type: ignore[arg-type]


def test_normalize_gtin_upc_to_ean13() -> None:
    # UPC-A 036000291452 → EAN-13 with leading zero
    assert normalize_gtin("036000291452") == "0036000291452"


def test_normalize_gtin_rejects_bad_check_digit() -> None:
    assert normalize_gtin("7891234567890") is None


def test_gtin_exact_auto_match() -> None:
    engine = MatchingEngine()
    gtin = normalize_gtin("7891991010863")
    assert gtin is not None
    score = engine.score(
        _identity(gtin=gtin, brand="nestle", title="Chocolate Nestle"),
        _identity(gtin=gtin, brand="nestle", title="Chocolate Nestlé Ao Leite"),
    )
    assert score.decision == "auto_match"
    assert score.confidence >= Decimal("0.99")


def test_variant_storage_mismatch_rejects() -> None:
    engine = MatchingEngine()
    gtin = normalize_gtin("7891991010863")
    score = engine.score(
        _identity(
            gtin=gtin,
            brand="apple",
            title="iPhone 128GB",
            variant_attrs={"storage": "128gb"},
        ),
        _identity(
            gtin=gtin,
            brand="apple",
            title="iPhone 256GB",
            variant_attrs={"storage": "256gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "variant_mismatch" for r in score.reasons)


def test_storage_space_and_color_synonym_do_not_reject() -> None:
    score = MatchingEngine().score(
        _identity(
            gtin=None,
            brand="apple",
            model="iphone17",
            title="Apple iPhone 17 256GB Preto",
            title_normalized="apple iphone 17 256gb preto",
            variant_attrs={"color": "preto", "storage": "256gb"},
        ),
        _identity(
            gtin=None,
            brand="apple",
            model="iphone17",
            title="Apple iPhone 17 256 GB Black",
            title_normalized="apple iphone 17 256 gb black",
            variant_attrs={"color": "black", "storage": "256 gb"},
            store="bestbuy",
            product_id="x",
        ),
    )
    assert score.decision != "reject"
    assert not any(r.code == "variant_mismatch" for r in score.reasons)


def test_luna_grey_synonym_and_ideapad_model_compat() -> None:
    from scout_api.modules.matching.identity import (
        models_compatible,
        normalize_variant_value,
    )

    assert normalize_variant_value("color", "grey") == normalize_variant_value(
        "color", "luna grey"
    )
    assert normalize_variant_value("color", "arctic grey") == "gray"
    assert models_compatible("ideapadslim3iintelcore", "ideapadslim3")
    assert not models_compatible("ideapadslim3iintelcore", "ideapadslim3amdryzen")
    assert not models_compatible(
        "ideapadslim3iintelcore",
        "ideapadslim3",
        left_title="Notebook Lenovo IdeaPad Slim 3i Intel Core 3 100U",
        right_title="Notebook Lenovo IdeaPad Slim 3 AMD Ryzen 7 7735HS",
    )
    assert not models_compatible(
        "ideapadslim3iintelcore",
        "ideapadslim3i",
        left_title=(
            "Notebook Lenovo Ideapad Slim 3i Intel Core 3 100u 8GB 256gb Luna Grey"
        ),
        right_title=(
            "Lenovo - IdeaPad Slim 3i 15.6 Full HD Laptop - Intel Core i5-1335U "
            "16GB Memory - 256GB Storage - Arctic Grey"
        ),
    )
    assert not models_compatible("ideapadslim3", "ideapadslim315irh10intel")

    score = MatchingEngine().score(
        _identity(
            brand="lenovo",
            model="ideapadslim3iintelcore",
            title=(
                "Notebook Lenovo Ideapad Slim 3i Intel Core 3 100u 8GB 256gb Luna Grey"
            ),
            title_normalized="notebook lenovo ideapad slim 3i 8gb 256gb luna grey",
            variant_attrs={"color": "grey", "storage": "256gb", "ram": "8gb"},
        ),
        _identity(
            brand="lenovo",
            model="ideapadslim3",
            title=(
                "Notebook Lenovo IdeaPad Slim 3 Luna Grey - Intel Core 3 100U, 8G 256GB"
            ),
            title_normalized=(
                "notebook lenovo ideapad slim 3 luna grey core 3 100u 8g 256gb"
            ),
            variant_attrs={"color": "luna grey", "storage": "256gb", "ram": "8gb"},
            store="amazon_br",
            product_id="B0TEST",
        ),
    )
    assert score.decision == "auto_match"

    # Soft model + weak title must not auto_match (Best Buy i5 false positive).
    weak = MatchingEngine().score(
        _identity(
            brand="lenovo",
            model="ideapadslim3iintelcore",
            title=(
                "Notebook Lenovo Ideapad Slim 3i Intel Core 3 100u 8GB 256gb Luna Grey"
            ),
            title_normalized="notebook lenovo ideapad slim 3i intel core 3 100u",
            variant_attrs={"color": "grey", "storage": "256gb", "ram": "8gb"},
        ),
        _identity(
            brand="lenovo",
            model="ideapadslim3i",
            title="Lenovo IdeaPad Slim 3i Arctic Grey 16GB 256GB",
            title_normalized="lenovo ideapad slim 3i arctic grey 16gb 256gb",
            variant_attrs={"color": "arctic grey", "storage": "256gb"},
            store="bestbuy",
            product_id="x",
        ),
    )
    assert weak.decision != "auto_match"


def test_cooler_radiator_size_and_manufacturer_code_are_discriminative() -> None:
    engine = MatchingEngine()
    source = _identity(
        brand="MSI",
        model="magcoreliquida12",
        title="Water Cooler MSI MAG CoreLiquid A12 360mm ARGB - CLA12360",
        category="cooler",
        category_attrs={"cooler_type": "liquid", "radiator_size": "360 mm"},
        mpn="cla12360",
    )
    wrong_size = _identity(
        brand="MSI",
        model="magcoreliquida12",
        title="Water Cooler MSI MAG CoreLiquid A12 240mm ARGB - CLA12240",
        category="cooler",
        category_attrs={"cooler_type": "liquid", "radiator_size": "240 mm"},
        mpn="cla12240",
    )

    score = engine.score(source, wrong_size)

    assert score.decision == "reject"
    assert any(
        reason.code == "critical_conflict"
        and "radiator_size_mismatch" in (reason.detail or "")
        for reason in score.reasons
    )


def test_cooler_missing_radiator_size_and_argb_do_not_conflict() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="MSI",
            model="magcoreliquida12",
            title="Water Cooler MSI MAG CoreLiquid A12 360mm ARGB",
            category="cooler",
            category_attrs={"cooler_type": "liquid", "radiator_size": "360 mm"},
        ),
        _identity(
            brand="MSI",
            model="magcoreliquida12",
            title="MSI MAG CoreLiquid A12 Liquid CPU Cooler",
            category="cooler",
            category_attrs={"cooler_type": "liquid"},
        ),
    )

    assert score.decision != "reject"


def test_cooler_market_codes_can_differ_when_model_and_size_agree() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="MSI",
            model="MSI MAG Coreliquid A12 Argb",
            title="MSI MAG CoreLiquid A12 360 ARGB CLA12360",
            category="cooler",
            category_attrs={"cooler_type": "liquid", "radiator_size": "360 mm"},
            mpn="cla12360",
        ),
        _identity(
            brand="MSI",
            model="magcoreliquida12",
            title="MSI MAG CoreLiquid A12 360 ARGB 306-7ZWEM21-813",
            category="cooler",
            category_attrs={"cooler_type": "liquid", "radiator_size": "360 mm"},
            mpn="3067zwem21813",
        ),
    )

    assert score.decision == "auto_match"


def test_identity_refines_ram_as_storage_and_sku_model() -> None:
    from datetime import UTC, datetime

    from scout_api.modules.crawler.models.product import ProductPriceItem
    from scout_api.modules.matching.identity import identity_from_price_item

    item = ProductPriceItem(
        store="magazineluiza",
        country="BR",
        product_id="x",
        title="Notebook Lenovo IdeaPad Slim 3i Intel Core 3 100U 8GB 256GB SSD",
        brand="Lenovo",
        model="83nu0000br",
        variant="color: Luna Grey; storage: 8gb",
        url="https://example.test/p",
        canonical_url="https://example.test/p",
        currency="BRL",
        price=Decimal("3999.00"),
        scraped_at=datetime(2026, 9, 14, tzinfo=UTC),
        metadata={"color": "luna grey", "storage": "8gb"},
    )
    identity = identity_from_price_item(item)
    assert identity.variant_attrs.get("storage") == "256gb"
    assert identity.model == "ideapadslim3i"


def test_accessory_title_rejects() -> None:
    assert looks_like_accessory(
        "Capa para iPhone 15 Pro",
        reference_title="Apple iPhone 15 Pro 128GB",
    )
    engine = MatchingEngine()
    score = engine.score(
        _identity(brand="apple", model="iphone15pro", title="Apple iPhone 15 Pro"),
        _identity(
            brand="apple",
            model="iphone15pro",
            title="Capa Case Silicone iPhone 15 Pro",
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "accessory_reject" for r in score.reasons)


def test_title_only_never_auto_match() -> None:
    engine = MatchingEngine()
    score = engine.score(
        _identity(title="Memoria RAM Kingston Fury 8GB DDR4 3200"),
        _identity(title="Memoria RAM Kingston Fury 8GB DDR4 3200 Mhz Desktop"),
    )
    assert score.decision != "auto_match"


def test_brand_model_auto_match() -> None:
    engine = MatchingEngine()
    score = engine.score(
        _identity(
            brand="kingston",
            model="hx432c16fb3",
            title="Kingston HyperX Fury DDR4",
        ),
        _identity(
            brand="kingston",
            model="hx432c16fb3",
            title="Memoria Kingston HyperX Fury",
        ),
    )
    assert score.decision == "auto_match"


def test_brand_mismatch_rejects() -> None:
    engine = MatchingEngine()
    score = engine.score(
        _identity(brand="samsung", model="galaxy", title="Samsung Galaxy A15"),
        _identity(brand="motorola", model="galaxy", title="Motorola Galaxy A15"),
    )
    assert score.decision == "reject"


def test_identity_from_magalu_style_variant() -> None:
    from datetime import UTC, datetime

    from scout_api.modules.crawler.models.product import ProductPriceItem
    from scout_api.modules.matching.identity import (
        build_search_queries,
        identity_from_price_item,
    )

    item = ProductPriceItem(
        store="magazineluiza",
        country="BR",
        product_id="241268000",
        title='Apple iPhone 17 256GB Preto 6,3" 48MP iOS 5G',
        brand="Apple",
        model="iPhone 17",
        variant="Preto",
        url="https://www.magazineluiza.com.br/p/241268000/",
        canonical_url="https://www.magazineluiza.com.br/p/241268000/",
        currency="BRL",
        price=Decimal("6332.22"),
        scraped_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    identity = identity_from_price_item(item)
    assert identity.variant_attrs.get("color") == "preto"
    assert identity.variant_attrs.get("storage") == "256gb"
    queries = build_search_queries(identity)
    assert any("256gb" in q and "preto" in q for q in queries)
