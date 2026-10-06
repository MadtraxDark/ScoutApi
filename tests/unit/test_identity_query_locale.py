"""Cor localizável do título lidera a query; identificadores permanecem exatos."""

from __future__ import annotations

import pytest

from scout_api.modules.matching.identity import (
    ProductIdentity,
    _title_search_phrase,
    build_search_queries,
    fold_text,
)


def _identity(title: str, color: str) -> ProductIdentity:
    return ProductIdentity(
        gtin=None,
        brand="apple",
        model="iphone 15",
        title=title,
        title_normalized=fold_text(title),
        variant_attrs={"color": color, "storage": "128gb"},
        category="smartphone",
    )


@pytest.mark.parametrize(
    ("title", "color", "expected"),
    [
        ("Apple iPhone 15 128GB Rosa", "Rosa", "apple iphone 15 128gb pink"),
        (
            "Apple iPhone 15 Pro 128GB Titânio Preto",
            "Titânio Preto",
            "apple iphone 15 pro 128gb black titanium",
        ),
    ],
)
def test_titulo_localiza_cor_en_us_na_mesma_posicao(title, color, expected) -> None:
    identity = _identity(title, color)
    assert _title_search_phrase(identity, "en-US") == expected
    assert build_search_queries(identity, locale="en-US")[0] == expected


def test_ladder_us_prioriza_pink_mantendo_rosa_como_fallback() -> None:
    queries = build_search_queries(
        _identity("Apple iPhone 15 128GB Rosa", "Rosa"), locale="en-US"
    )
    assert "pink" in queries[0].split()
    assert "rosa" not in queries[0].split()
    assert any("rosa" in query.casefold().split() for query in queries[1:])


def test_locale_pt_br_preserva_cor_original_sem_categoria_inferida() -> None:
    identity = _identity("Apple iPhone 15 128GB Rosa", "Rosa")
    assert _title_search_phrase(identity, "pt-BR") == "apple iphone 15 128gb rosa"
    assert build_search_queries(identity, locale="pt-BR")[0] == (
        "apple iphone 15 128gb rosa"
    )


def test_locale_pt_br_preserva_acabamento_titanio() -> None:
    identity = _identity("Apple iPhone 15 Pro 128GB Titânio Preto", "Titânio Preto")
    assert _title_search_phrase(identity, "pt-BR") == (
        "apple iphone 15 pro 128gb titanio preto"
    )


def test_localizacao_nao_altera_substrings_de_modelo_ou_mpn() -> None:
    identity = ProductIdentity(
        gtin=None,
        brand="acme",
        model="XRosa256B",
        title="Acme XRosa256B 128GB Rosa SM-ROSA256B",
        title_normalized="acme xrosa256b 128gb rosa sm-rosa256b",
        variant_attrs={"color": "Rosa", "storage": "128gb"},
        mpn="smrosa256b",
        mpn_display="SM-ROSA256B",
    )
    queries = build_search_queries(identity, locale="en-US")
    assert any("xrosa256b" in query.casefold() for query in queries)
    assert any("sm-rosa256b" in query.casefold() for query in queries)
    assert all("xpink256b" not in query.casefold() for query in queries)
    assert all("sm-pink256b" not in query.casefold() for query in queries)
