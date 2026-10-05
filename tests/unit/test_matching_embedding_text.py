from __future__ import annotations

from scout_api.modules.matching.embedding_text import build_embedding_texts
from scout_api.modules.matching.identity import ProductIdentity


def _identity(**overrides: object) -> ProductIdentity:
    values: dict[str, object] = {
        "gtin": "0000000000000",
        "brand": "Apple",
        "model": "iPhone 17 Pro",
        "title": " Apple iPhone 17 Pro 256 GB Preto ",
        "title_normalized": "apple iphone 17 pro 256gb preto",
        "variant_attrs": {"storage": "256gb", "color": "preto"},
        "store": None,
        "product_id": None,
        "price": None,
        "currency": None,
        "mpn": "MG7L4LL/A",
        "mpn_display": "MG7L4LL/A",
        "mpn_aliases": frozenset({"MG7L4LL/A"}),
        "model_numbers": frozenset({"MG7L4LL/A"}),
        "monitor_model_code": None,
        "category": "smartphone",
    }
    values.update(overrides)
    return ProductIdentity(**values)  # type: ignore[arg-type]


def test_builds_four_text_representations_and_keeps_variant_fields() -> None:
    texts = build_embedding_texts(_identity())

    assert texts.raw == "Apple iPhone 17 Pro 256 GB Preto"
    assert texts.normalized == "apple iphone 17 pro 256gb preto"
    assert texts.structured == (
        "category: smartphone\n"
        "brand: Apple\n"
        "model: iPhone 17 Pro\n"
        "variant_color: preto\n"
        "variant_storage: 256gb"
    )
    assert texts.hybrid == f"{texts.raw}\n{texts.structured}"


def test_structured_representation_does_not_add_exact_identifiers() -> None:
    texts = build_embedding_texts(_identity())

    assert "0000000000000" not in texts.structured
    assert "MG7L4LL/A" not in texts.structured


def test_structured_representation_omits_missing_fields_and_sorts_variants() -> None:
    texts = build_embedding_texts(
        _identity(
            brand=None,
            model=None,
            category=None,
            variant_attrs={"storage": "512gb", "color": "azul"},
        )
    )

    assert texts.structured == "variant_color: azul\nvariant_storage: 512gb"


def test_structured_representation_keeps_external_values_on_one_line() -> None:
    texts = build_embedding_texts(
        _identity(model="iPhone\n17 Pro", variant_attrs={"color": "azul\nmarinho"})
    )

    assert texts.structured == (
        "category: smartphone\nbrand: Apple\nmodel: iPhone 17 Pro\n"
        "variant_color: azul marinho"
    )
