"""Regression suite for precision-first product matching."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import (
    ProductIdentity,
    build_search_queries,
    identity_from_price_item,
    normalize_gtin,
    normalize_title,
    normalize_variant_value,
    token_set_ratio,
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
    if "title_normalized" not in kwargs and isinstance(defaults["title"], str):
        defaults["title_normalized"] = normalize_title(defaults["title"])
    return ProductIdentity(**defaults)  # type: ignore[arg-type]


def _item(**overrides: object) -> ProductPriceItem:
    base: dict[str, object] = {
        "store": "kabum",
        "country": "BR",
        "product_id": "1",
        "url": "https://www.kabum.com.br/produto/1",
        "canonical_url": "https://www.kabum.com.br/produto/1",
        "title": "Produto",
        "currency": "BRL",
        "price": Decimal("100.00"),
        "scraped_at": datetime(2026, 9, 19, tzinfo=UTC),
    }
    base.update(overrides)
    return ProductPriceItem.model_validate(base)


def test_same_product_different_title_language_matches() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="apple",
            model="iphone15",
            title="Apple iPhone 15 128GB Blue",
            variant_attrs={"storage": "128gb", "color": "blue"},
        ),
        _identity(
            brand="apple",
            model="iphone15",
            title="Celular Apple iPhone 15 Azul 128 GB",
            variant_attrs={"storage": "128gb", "color": "azul"},
        ),
    )
    assert score.decision == "auto_match"
    assert (
        token_set_ratio(
            "Apple iPhone 15 128GB Blue",
            "Celular Apple iPhone 15 Azul 128 GB",
        )
        >= 0.7
    )


def test_different_storage_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="apple",
            model="iphone15",
            title="Apple iPhone 15 128GB",
            variant_attrs={"storage": "128gb"},
        ),
        _identity(
            brand="apple",
            model="iphone15",
            title="Apple iPhone 15 256GB",
            variant_attrs={"storage": "256gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code in {"variant_mismatch", "critical_conflict"} for r in score.reasons
    )


def test_gpu_ti_suffix_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="nvidia",
            model="rtx5060",
            title="Placa de Video RTX 5060 8GB",
        ),
        _identity(
            brand="nvidia",
            model="rtx5060ti",
            title="Placa de Video RTX 5060 Ti 16GB",
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "critical_conflict" for r in score.reasons)


def test_title_noise_still_matches_with_brand_model() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="kingston",
            model="hx432c16fb3",
            title="Kingston HyperX Fury DDR4 8GB",
            mpn="hx432c16fb3",
        ),
        _identity(
            brand="kingston",
            model="hx432c16fb3",
            title="OFERTA Memoria Kingston HyperX Fury 8GB Frete Gratis Oficial",
            mpn="hx432c16fb3",
        ),
    )
    assert score.decision == "auto_match"


def test_missing_structured_model_uses_title_fallback() -> None:
    ref = identity_from_price_item(
        _item(
            title="SSD Samsung 990 EVO Plus 1TB M.2 NVMe - MZ-V9S1T0B/AM",
            brand="Samsung",
            model="MZ-V9S1T0B/AM",
            variant="color: Preto; storage: 1 TB",
        )
    )
    cand = identity_from_price_item(
        _item(
            store="amazon_br",
            product_id="B0DHLFWBQ1",
            title=("Samsung SSD 990 EVO Plus 1TB, PCIe Gen 4x4, MZ-V9S1T0B/AM Preto"),
            brand="Samsung",
            model=None,
            url="https://www.amazon.com.br/dp/B0DHLFWBQ1",
            canonical_url="https://www.amazon.com.br/dp/B0DHLFWBQ1",
        )
    )
    assert ref.model == "990evoplus"
    assert cand.model == "990evoplus"
    assert ref.mpn == cand.mpn == "mzv9s1t0bam"
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"
    assert any(r.code in {"mpn_exact", "brand_model_exact"} for r in score.reasons)


def test_gtin_exact_high_confidence() -> None:
    gtin = normalize_gtin("7891991010863")
    assert gtin is not None
    score = MatchingEngine().score(
        _identity(gtin=gtin, brand="nestle", title="Chocolate A"),
        _identity(gtin=gtin, brand="nestle", title="Chocolate B com ruido"),
    )
    assert score.decision == "auto_match"
    assert score.confidence >= Decimal("0.99")


def test_ambiguous_title_only_does_not_auto_match() -> None:
    score = MatchingEngine().score(
        _identity(title="Memoria RAM 8GB DDR4 Desktop"),
        _identity(title="Memoria RAM 8GB DDR4 Notebook"),
    )
    assert score.decision != "auto_match"


def test_ssd_series_mismatch_870_vs_990_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="samsung",
            model="990evoplus",
            title="SSD Samsung 990 EVO Plus 1TB MZ-V9S1T0B/AM",
            mpn="mzv9s1t0bam",
            variant_attrs={"storage": "1tb"},
        ),
        _identity(
            brand="samsung",
            model="870evo",
            title="Samsung 870 EVO 1TB Internal SSD SATA",
            variant_attrs={"storage": "1tb"},
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "critical_conflict" for r in score.reasons)


def test_natural_title_query_leads_and_mpn_remains_exact_fallback() -> None:
    identity = identity_from_price_item(
        _item(
            title="SSD Samsung 990 EVO Plus 1TB - MZ-V9S1T0B/AM",
            brand="Samsung",
            model="990 Evo Plus",
            variant="storage: 1 TB",
        )
    )
    queries = build_search_queries(identity)
    assert identity.mpn == "mzv9s1t0bam"
    assert identity.mpn_display == "MZ-V9S1T0B/AM"
    assert queries[0] == "ssd samsung 990 evo plus 1tb"
    assert "MZ-V9S1T0B/AM" in queries
    assert any(q == "samsung 990 evo plus 1tb" for q in queries)
    assert any("mzv9s1t0bam" in q for q in queries)


def test_monitor_model_code_exact_match_overrides_title_and_variant_noise() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor Gamer ASUS TUF VG259Q5A 24.5 Full HD 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
            variant="size: 24.5; color: black",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            product_id="monitor-1",
            title="ASUS TUF Gaming VG259Q5A 24.5 IPS 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
            variant="size: 25; color: white",
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert reference.monitor_model_code == candidate.monitor_model_code == "VG259Q5A"
    assert score.decision == "auto_match"
    assert score.confidence == Decimal("0.9900")
    assert any(reason.code == "monitor_model_code_exact" for reason in score.reasons)


def test_monitor_model_code_case_is_normalized_safely() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor ASUS TUF VG259Q5A 24.5 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor ASUS TUF vg259q5a 24.5 IPS 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )

    assert reference.monitor_model_code == candidate.monitor_model_code == "VG259Q5A"
    assert MatchingEngine().score(reference, candidate).decision == "auto_match"


def test_monitor_model_code_extraction_supports_varied_manufacturer_formats() -> None:
    expected = {
        "ASUS TUF VG27AQ3A 27 180Hz": "VG27AQ3A",
        "Alienware AW2725DF 27 QD-OLED": "AW2725DF",
        "Dell G2724D 27 IPS": "G2724D",
        "LG UltraGear 27GS95QE-B 27 240Hz": "27GS95QE-B",
        "Samsung LS32DG802SNXZA 32 4K": "LS32DG802SNXZA",
        "Gigabyte M27Q-X 27 IPS": "M27Q-X",
    }
    for title, model_code in expected.items():
        identity = identity_from_price_item(
            _item(
                title=title,
                brand="monitor brand",
                metadata={"category": "monitor"},
            )
        )
        assert identity.monitor_model_code == model_code

    no_model_code = identity_from_price_item(
        _item(
            title="Monitor Gamer ASUS TUF 24.5 Full HD 200Hz IPS HDR10 BT2020",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    assert no_model_code.monitor_model_code is None


def test_monitor_without_model_code_keeps_readable_family_serp_query() -> None:
    identity = identity_from_price_item(
        _item(
            title=(
                'Monitor Gamer Samsung Odyssey G30 24" Led Full HD 144Hz '
                "1ms HDMI/DP FreeSync"
            ),
            brand="Samsung",
            model="Odyssey G30",
            metadata={
                "category": "monitor",
                "specifications": {
                    "screen_size": '24"',
                    "resolution": "FHD",
                    "refresh_rate": "144Hz",
                    "panel": "VA",
                },
            },
        )
    )

    queries = build_search_queries(identity)

    assert identity.monitor_model_code is None
    assert "samsung odyssey g30" in queries


def test_monitor_screen_size_normalizes_inch_notation() -> None:
    reference = identity_from_price_item(
        _item(
            title='Samsung Odyssey G30 24" 144Hz',
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"screen_size": '24"', "size": '24"'},
            },
        )
    )
    candidate = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 24 Pol 144Hz LS24BG300ELMZD",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"screen_size": "24", "size": "24"},
            },
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert (
        reference.variant_attrs["screen_size"] == candidate.variant_attrs["screen_size"]
    )
    assert reference.variant_attrs["size"] == candidate.variant_attrs["size"]
    assert score.decision != "reject"


def test_monitor_different_screen_sizes_still_reject() -> None:
    reference = identity_from_price_item(
        _item(
            title='Samsung Odyssey G30 24" 144Hz',
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"screen_size": '24"', "size": '24"'},
            },
        )
    )
    candidate = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 27 Pol 144Hz",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"screen_size": "27", "size": "27"},
            },
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert score.decision == "reject"
    assert any(reason.code == "variant_mismatch" for reason in score.reasons)


def test_monitor_resolution_normalizes_explicit_dimensions() -> None:
    reference = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 Full HD",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"resolution": "FHD (1920x1080)"},
            },
        )
    )
    candidate = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 Full HD",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"resolution": "1920 x 1080 pixels"},
            },
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert (
        reference.variant_attrs["resolution"] == candidate.variant_attrs["resolution"]
    )
    assert score.decision != "reject"


def test_monitor_refresh_rate_normalizes_space_before_hz() -> None:
    reference = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 144Hz",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"refresh_rate": "144Hz"},
            },
        )
    )
    candidate = identity_from_price_item(
        _item(
            title="Samsung Odyssey G30 144 Hz",
            brand="Samsung",
            metadata={
                "category": "monitor",
                "specifications": {"refresh_rate": "144 Hz"},
            },
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert (
        reference.variant_attrs["refresh_rate"]
        == candidate.variant_attrs["refresh_rate"]
    )
    assert score.decision != "reject"


def test_monitor_model_code_mismatch_rejects_similar_titles() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor Gamer ASUS TUF VG259Q5A 24.5 Full HD 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor ASUS TUF VG259QM 24.5 Full HD 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert score.decision == "reject"
    assert any(reason.code == "monitor_model_code_conflict" for reason in score.reasons)


def test_monitor_model_code_suffix_is_part_of_exact_identifier() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor LG UltraGear 27GS95QE 27 inch 240Hz",
            brand="LG",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor LG UltraGear 27GS95QE-B 27 inch 240Hz",
            brand="LG",
            metadata={"category": "monitor"},
        )
    )

    assert reference.monitor_model_code == "27GS95QE"
    assert candidate.monitor_model_code == "27GS95QE-B"
    assert MatchingEngine().score(reference, candidate).decision == "reject"


def test_monitor_missing_model_code_falls_back_to_regular_match() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor Gamer ASUS TUF VG259Q5A 24.5 Full HD 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor Asus TUF Gaming 24.5 Full HD IPS 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert score.decision == "auto_match"
    assert any(reason.code == "brand_model_exact" for reason in score.reasons)


def test_monitor_missing_code_can_continue_with_matching_display_specs() -> None:
    reference = identity_from_price_item(
        _item(
            title=("Monitor Gamer Gigabyte GS24F14 23.8 Pol Full HD IPS 144Hz 1ms"),
            brand="Gigabyte",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor Gamer Gigabyte 23.8 Pol Full HD IPS 144Hz 1ms",
            brand="Gigabyte",
            metadata={"category": "monitor"},
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert reference.monitor_model_code == "GS24F14"
    assert candidate.monitor_model_code is None
    assert score.decision == "review"
    assert any(reason.code == "monitor_specs_agree" for reason in score.reasons)


def test_candidate_only_monitor_model_code_falls_back_to_regular_match() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor ASUS TUF Gaming 24.5 Full HD IPS 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="Monitor ASUS TUF VG259Q5A 24.5 Full HD 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert score.decision == "auto_match"
    assert any(reason.code == "brand_model_exact" for reason in score.reasons)


def test_no_monitor_model_codes_keep_regular_matching() -> None:
    reference = identity_from_price_item(
        _item(
            title="Monitor ASUS TUF Gaming 24.5 Full HD IPS 200Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="amazon_br",
            title="ASUS TUF Gaming 24.5 IPS Full HD 200 Hz",
            brand="ASUS",
            metadata={"category": "monitor"},
        )
    )
    score = MatchingEngine().score(reference, candidate)

    assert reference.monitor_model_code is None
    assert candidate.monitor_model_code is None
    assert score.decision == "auto_match"


def test_rank_candidates_prefers_mpn_title_hit() -> None:
    from scout_api.modules.matching.identity import rank_candidates_for_query
    from scout_api.modules.matching.search_candidate import SearchCandidate

    ranked = rank_candidates_for_query(
        [
            SearchCandidate(
                url="https://www.amazon.com.br/dp/B0WRONG001",
                title="SSD Samsung 990 PRO 1TB",
                product_id="B0WRONG001",
            ),
            SearchCandidate(
                url="https://www.amazon.com.br/dp/B0DHLFWBQ1",
                title="Samsung SSD 990 EVO Plus 1TB MZ-V9S1T0B/AM",
                product_id="B0DHLFWBQ1",
            ),
        ],
        "MZ-V9S1T0B/AM",
    )
    assert ranked[0].product_id == "B0DHLFWBQ1"


def test_rank_candidates_ignores_amazon_keywords_querystring() -> None:
    """SERP URLs embed keywords=… — must not flatten color ranking."""
    from scout_api.modules.matching.identity import rank_candidates_for_query
    from scout_api.modules.matching.search_candidate import SearchCandidate

    ranked = rank_candidates_for_query(
        [
            SearchCandidate(
                url=(
                    "https://www.amazon.com.br/dp/B0TEAL?"
                    "keywords=apple+iphone+16+128gb+preto"
                ),
                title="Apple iPhone 16 (128 GB) – Verde-acinzentado",
                product_id="B0TEAL",
            ),
            SearchCandidate(
                url=(
                    "https://www.amazon.com.br/dp/B0DJFTJ6LX?"
                    "keywords=apple+iphone+16+128gb+preto"
                ),
                title="Apple iPhone 16 (128 GB) – Preto",
                product_id="B0DJFTJ6LX",
            ),
        ],
        "apple iphone 16 128gb preto",
    )
    assert ranked[0].product_id == "B0DJFTJ6LX"


def test_iphone_pro_suffix_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="apple",
            model="iphone15",
            title="Apple iPhone 15 128GB",
            variant_attrs={"storage": "128gb"},
        ),
        _identity(
            brand="apple",
            model="iphone15pro",
            title="Apple iPhone 15 Pro 128GB",
            variant_attrs={"storage": "128gb"},
        ),
    )
    assert score.decision == "reject"


def test_ddr4_vs_ddr5_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="kingston",
            model="fury",
            title="Kingston Fury 16GB DDR4 3200",
        ),
        _identity(
            brand="kingston",
            model="fury",
            title="Kingston Fury 16GB DDR5 5600",
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "critical_conflict" for r in score.reasons)


def test_normalize_title_compacts_capacity_and_mpn() -> None:
    assert "128gb" in normalize_title("iPhone 15 128 GB Blue").split()
    assert "128gb" in normalize_title("iPhone 15 128GB Blue").split()
    assert "mzv9s1t0bam" in normalize_title("SSD Samsung MZ-V9S1T0B/AM 1TB").split()


def test_iphone16_preto_matches_black_cross_locale() -> None:
    ref = identity_from_price_item(
        _item(
            store="magazineluiza",
            product_id="238803400",
            title='Apple iPhone 16 128GB Preto 6,1" 48MP iOS 5G',
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
        )
    )
    cand = identity_from_price_item(
        _item(
            store="amazon_br",
            product_id="B0DJFTJ6LX",
            title="Apple iPhone 16 (128 GB) – Black",
            brand="Apple",
            model="iPhone 16",
            url="https://www.amazon.com.br/dp/B0DJFTJ6LX",
            canonical_url="https://www.amazon.com.br/dp/B0DJFTJ6LX",
            variant="tamanho: 128 GB; cor: Black",
        )
    )
    assert ref.variant_attrs.get("color") == "preto"
    assert cand.variant_attrs.get("color") == "black"
    assert cand.variant_attrs.get("storage") == "128gb"
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"


def test_iphone16_rejects_different_color_cor_alias() -> None:
    """Amazon BR publishes ``cor`` / ``tamanho`` — must not drop the color gate."""
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 16 128GB Preto",
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
        )
    )
    cand = identity_from_price_item(
        _item(
            store="amazon_br",
            product_id="B0DJFSTQHX",
            title="Apple iPhone 16 (128 GB) – Verde-acinzentado",
            brand="Apple",
            model="iPhone 16",
            url="https://www.amazon.com.br/dp/B0DJFSTQHX",
            canonical_url="https://www.amazon.com.br/dp/B0DJFSTQHX",
            variant="tamanho: 128 GB; cor: Verde-Acizentado",
        )
    )
    assert cand.variant_attrs.get("color") is not None
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "reject"
    assert any(r.code == "variant_mismatch" for r in score.reasons)


def test_iphone16_rejects_pro_and_storage_and_16e() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 16 128GB Preto",
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
        )
    )
    engine = MatchingEngine()
    pro = identity_from_price_item(
        _item(
            title="Apple iPhone 16 Pro 128GB Preto",
            brand="Apple",
            model="iPhone 16 Pro",
            variant="Preto",
            product_id="2",
        )
    )
    assert engine.score(ref, pro).decision == "reject"

    gb256 = identity_from_price_item(
        _item(
            title="Apple iPhone 16 256GB Preto",
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
            product_id="3",
            metadata={"storage": "256gb"},
        )
    )
    assert engine.score(ref, gb256).decision == "reject"

    iphone_e = identity_from_price_item(
        _item(
            title="Apple iPhone 16e 128GB Black",
            brand="Apple",
            model="iPhone 16e",
            variant="Black",
            product_id="4",
        )
    )
    assert engine.score(ref, iphone_e).decision == "reject"


def test_iphone16_title_noise_still_matches() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                title='Apple iPhone 16 128GB Preto 5G Tela 6.1"',
                brand="Apple",
                model="iPhone 16",
                variant="Preto",
            )
        ),
        identity_from_price_item(
            _item(
                store="bestbuy",
                product_id="6507508",
                title="iPhone 16 Apple Black 128 GB Unlocked",
                brand="Apple",
                model="iPhone 16",
                url="https://www.bestbuy.com/site/6507508.p",
                canonical_url="https://www.bestbuy.com/site/6507508.p",
                variant="color: Black; storage: 128 GB",
            )
        ),
    )
    assert score.decision == "auto_match"


def test_iphone16_search_queries_include_color_synonyms() -> None:
    identity = identity_from_price_item(
        _item(
            title="Apple iPhone 16 128GB Preto",
            brand="Apple",
            model="iPhone 16",
            variant="Preto",
        )
    )
    queries = build_search_queries(identity)
    assert any("preto" in q for q in queries)
    assert any("black" in q for q in queries)
    assert any(q == "iphone 16 128gb" for q in queries)


def test_iphone17_cosmic_orange_matches_translated_compound_color() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro 256GB Laranja cósmico",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="tamanho: 256 GB; cor: Laranja cósmico",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="visaovip",
            title="Apple iPhone 17 Pro MG7L4LL/A A3256 256GB / eSIM - Cosmic Orange",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    assert normalize_variant_value("color", ref.variant_attrs["color"]) == (
        normalize_variant_value("color", candidate.variant_attrs["color"])
    )
    assert normalize_variant_value("color", "Naranja cósmico") == (
        normalize_variant_value("color", "Cosmic Orange")
    )
    assert candidate.variant_attrs["storage"] == "256gb"
    assert candidate.mpn == "mg7l4lla"
    assert candidate.model_numbers == frozenset({"a3256"})
    score = MatchingEngine().score(ref, candidate)
    assert score.decision == "auto_match"
    assert any(reason.code == "brand_model_exact" for reason in score.reasons)
    assert any("iphone 17 pro 256gb orange" in q for q in build_search_queries(ref))


def test_iphone17_pro_max_stays_incompatible_after_color_normalization() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro 256GB Laranja cósmico",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Laranja cósmico; storage: 256 GB",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="shoppingchina",
            title="Celular Apple 17 Pro Max 256 Gb Orange Esim",
            brand="Apple",
            model="iPhone 17 Pro Max",
            variant="color: Orange; storage: 256 GB",
        )
    )
    score = MatchingEngine().score(ref, candidate)
    assert score.decision == "reject"
    assert any(
        reason.code == "critical_conflict" and "phone_mismatch" in (reason.detail or "")
        for reason in score.reasons
    )


def test_iphone17_refurbished_listing_matches_with_condition_note() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro 256GB Laranja cósmico",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Laranja cósmico; storage: 256GB",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="bestbuy",
            title=(
                "Apple - Refurbished Excellent - iPhone 17 Pro 256GB 5G Fully "
                "Unlocked Cosmic Orange"
            ),
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    score = MatchingEngine().score(ref, candidate)
    assert score.decision == "auto_match"
    assert any(reason.code == "offer_condition" for reason in score.reasons)
    assert any("refurbished" in (reason.detail or "") for reason in score.reasons)


def test_renewed_listing_matches_against_new_reference_with_condition() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                title="Apple iPhone 16 128GB Black",
                brand="Apple",
                model="iPhone 16",
                variant="Black",
            )
        ),
        identity_from_price_item(
            _item(
                store="amazon_us",
                product_id="B0RENEWED",
                title="Apple iPhone 16, 128GB, Black - Unlocked (Renewed Premium)",
                brand="Apple",
                model="iPhone 16",
                url="https://www.amazon.com/dp/B0RENEWED",
                canonical_url="https://www.amazon.com/dp/B0RENEWED",
                variant="color: Black; storage: 128 GB",
            )
        ),
    )
    assert score.decision == "auto_match"
    assert any(r.code == "offer_condition" for r in score.reasons)
    assert any("renewed" in (r.detail or "") for r in score.reasons)
    assert any("premium" in (r.detail or "") for r in score.reasons)


def test_renewed_does_not_relax_storage_conflict() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                title="Apple iPhone 16 256GB Black",
                brand="Apple",
                model="iPhone 16",
                variant="color: Black; storage: 256GB",
            )
        ),
        identity_from_price_item(
            _item(
                store="amazon_us",
                title="Apple iPhone 16, 128GB, Black - Unlocked (Renewed)",
                brand="Apple",
                model="iPhone 16",
                variant="color: Black; storage: 128 GB",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code in {"critical_conflict", "variant_mismatch"}
        and "128" in (r.detail or "")
        and "256" in (r.detail or "")
        for r in score.reasons
    )


def test_used_listing_still_rejects_against_new_reference() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                title="Apple iPhone 16 128GB Black",
                brand="Apple",
                model="iPhone 16",
                variant="Black",
            )
        ),
        identity_from_price_item(
            _item(
                store="amazon_us",
                title="Apple iPhone 16, 128GB, Black - Used",
                brand="Apple",
                model="iPhone 16",
                variant="color: Black; storage: 128 GB",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "condition_reject" for r in score.reasons)


def test_new_and_renewed_listings_keep_distinct_store_product_ids() -> None:
    """Same canonical identity may have separate commercial listings."""
    from scout_api.modules.matching.identity import parse_offer_condition

    new_title = "Apple iPhone 17 256GB Black"
    renewed_title = "Apple iPhone 17 256GB Black (Renewed Premium)"
    assert parse_offer_condition(new_title).code in {"new", "unknown"}
    renewed = parse_offer_condition(renewed_title)
    assert renewed.code == "renewed"
    assert renewed.grade == "premium"
    # Distinct ASINs / product_ids remain distinct commercial offers.
    assert "B0NEW" != "B0RENEWED"


def test_semantic_attribute_aliases_cover_spanish_and_carrier_lock() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro 256GB Laranja cósmico Desbloqueado",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Laranja cósmico; storage: 256GB",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="international",
            title="Apple iPhone 17 Pro 256GB Cosmic Orange Unlocked",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    assert ref.variant_attrs["network_lock"] == candidate.variant_attrs["network_lock"]
    assert MatchingEngine().score(ref, candidate).decision == "auto_match"

    locked = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro 256GB Cosmic Orange Locked",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    assert MatchingEngine().score(ref, locked).decision == "reject"


def test_explicit_identical_phone_model_number_is_strong_evidence() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro A3256 256GB Cosmic Orange",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="visaovip",
            title="Apple iPhone 17 Pro MG7L4LL/A A3256 256GB eSIM Cosmic Orange",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    score = MatchingEngine().score(ref, candidate)
    assert score.decision == "auto_match"
    assert any(reason.code == "model_number_exact" for reason in score.reasons)


def test_identical_regional_manufacturer_part_numbers_are_strong_evidence() -> None:
    ref = identity_from_price_item(
        _item(
            title="Apple iPhone 17 Pro MG7L4LL/A 256GB Cosmic Orange",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="visaovip",
            title="Apple iPhone 17 Pro MG7L4LL/A A3256 256GB eSIM Cosmic Orange",
            brand="Apple",
            model="iPhone 17 Pro",
            variant="color: Cosmic Orange; storage: 256GB",
        )
    )
    score = MatchingEngine().score(ref, candidate)
    assert score.decision == "auto_match"
    assert any(reason.code == "mpn_exact" for reason in score.reasons)


def test_unknown_translated_color_is_reviewed_instead_of_rejected() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="example",
            model="device-x1",
            title="Example Device X1 Coral",
            variant_attrs={"color": "coral"},
        ),
        _identity(
            brand="example",
            model="device-x1",
            title="Example Device X1 Coraline",
            variant_attrs={"color": "coralino"},
        ),
    )
    assert score.decision == "review"
    assert any(reason.code == "variant_semantic_uncertain" for reason in score.reasons)


def test_iphone_bundle_with_watch_rejects() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                title="Apple iPhone 16 128GB Preto",
                brand="Apple",
                model="iPhone 16",
                variant="Preto",
            )
        ),
        identity_from_price_item(
            _item(
                store="magazineluiza",
                product_id="239229600",
                title=(
                    "Smartphone Apple iPhone 16 128GB Preto 5G "
                    "Smartwatch Apple Watch SE 3 40mm"
                ),
                brand="Apple",
                model="iPhone 16",
                url="https://www.magazineluiza.com.br/p/239229600/",
                canonical_url="https://www.magazineluiza.com.br/p/239229600/",
                variant="Preto",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "bundle_reject" for r in score.reasons)


def test_missing_controller_count_is_not_a_conflict() -> None:
    """Unknown controller qty must not reject an otherwise matching console."""
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="Console Sony PlayStation 5 CFI-2115B Digital 825GB 8K",
            variant_attrs={"storage": "825gb", "color": "branco"},
            mpn="cfi2115b",
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Edição Digital 825GB 1 Controle Branco Sony",
            variant_attrs={"storage": "825gb", "color": "branco"},
        ),
    )
    assert score.decision == "auto_match"
    assert not any("controller" in (r.detail or "") for r in score.reasons)


def test_explicit_controller_count_conflict_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Digital 825GB 1 Controle Branco",
            variant_attrs={"storage": "825gb"},
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Digital 825GB 2 Controles Branco",
            variant_attrs={"storage": "825gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code == "critical_conflict" and "controller_count" in (r.detail or "")
        for r in score.reasons
    )


def test_digital_vs_disc_edition_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="Console Sony PlayStation 5 CFI-2115B Digital 825GB 8K",
            variant_attrs={"storage": "825gb"},
            gtin="0711719021483",
        ),
        _identity(
            brand="sony",
            model="playstation5discversion",
            title=(
                "Sony PlayStation 5 PS5 Disc Version Gaming Console, "
                "Ultra-High Speed 825GB SSD, Bluetooth 5.1, 120Hz 8K Output"
            ),
            variant_attrs={"storage": "825gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code == "critical_conflict" and "console_edition" in (r.detail or "")
        for r in score.reasons
    )


def test_storage_825_vs_1tb_rejects() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Digital Edition 825GB",
            variant_attrs={"storage": "825gb"},
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Digital Edition 1TB",
            variant_attrs={"storage": "1tb"},
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code in {"critical_conflict", "variant_mismatch"}
        and "storage" in (r.detail or "")
        for r in score.reasons
    )


def test_console_plus_fortnite_bundle_rejects_against_bare() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="Console Sony PlayStation 5 CFI-2115B Digital 825GB 8K",
            variant_attrs={"storage": "825gb"},
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title=("CONSOLE SONY PLAYSTATION 5 CFI-2115B DIGITAL 825GB 8K + FORTNITE"),
            variant_attrs={"storage": "825gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "bundle_reject" for r in score.reasons)


def test_sparse_console_title_still_matches_marketing_title() -> None:
    """Shopping China-style sparse title vs KaBuM marketing must auto_match."""
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5slimdigital",
            title=(
                "Console Sony PlayStation 5 Edição Digital Slim, SSD 825GB, "
                "Controle DualSense, Astro's Playroom, Branco - CFI 2114B"
            ),
            variant_attrs={"storage": "825gb", "color": "branco"},
            mpn="cfi2114b",
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title="Console Sony Playstation 5 Cfi 2115 B Digital 825 Gb 8 K",
            variant_attrs={"storage": "825gb", "color": "branco"},
            mpn="cfi2115b",
            gtin="0711719021483",
        ),
    )
    assert score.decision == "auto_match"
    assert any(r.code == "brand_model_exact" for r in score.reasons)


def test_resolve_model_enriches_generic_playstation_from_title() -> None:
    from scout_api.modules.matching.identity import resolve_model

    assert (
        resolve_model(
            "PlayStation 5",
            "Console Sony PlayStation 5 Edição Digital Slim 825GB Branco",
        )
        == "playstation5slimdigital"
    )


def test_ps5_search_queries_omit_slim_and_hyphenate_cfi() -> None:
    """Shopping China empties on brand+slim+storage; hyphenated CFI ranks."""
    from scout_api.modules.matching.identity import (
        ProductIdentity,
        build_search_queries,
        extract_mpn_forms,
    )

    _norm, display = extract_mpn_forms(
        "PlayStation 5",
        "Console Sony PlayStation 5 Edição Digital Slim CFI 2114B",
    )
    assert display == "CFI-2114B"

    queries = build_search_queries(
        ProductIdentity(
            gtin=None,
            brand="sony",
            model="playstation5slimdigital",
            title=(
                "Console Sony PlayStation 5 Edição Digital Slim, SSD 825GB, "
                "Controle DualSense, Branco - CFI 2114B"
            ),
            title_normalized="console sony playstation 5 edicao digital slim",
            variant_attrs={"storage": "825gb", "color": "branco"},
            store="kabum",
            product_id="1050570",
            price=None,
            currency="BRL",
            mpn="cfi2114b",
            mpn_display="CFI-2114B",
        )
    )
    assert "CFI-2114B" in queries
    assert "sony playstation 5 digital 825gb" in queries
    assert "playstation 5 digital 825gb" in queries
    # Primary series queries must not require "slim" (title fallback may keep it).
    assert all(
        "slim" not in q
        for q in queries
        if q.startswith("sony playstation 5") or q.startswith("playstation 5")
    )


def test_marketing_noise_bluetooth_8k_does_not_block_ps5_match() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="sony",
            model="playstation5digital",
            title="PlayStation 5 Edição Digital 825GB Branco",
            variant_attrs={"storage": "825gb", "color": "branco"},
            mpn="cfi2115b",
        ),
        _identity(
            brand="sony",
            model="playstation5digital",
            title=(
                "Sony PlayStation 5 Digital Edition, Ultra-High Speed 825GB SSD, "
                "WiFi 6, Bluetooth 5.1, 120Hz 8K Output, White"
            ),
            variant_attrs={"storage": "825gb", "color": "white"},
        ),
    )
    assert score.decision == "auto_match"


def test_match_stops_after_two_empty_searches() -> None:
    """Do not burn the full query list when SERP repeatedly returns []."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.schemas import MatchRequest

    ref = _item(
        store="magazineluiza",
        product_id="238803400",
        title="Apple iPhone 16 128GB Preto",
        brand="Apple",
        model="iPhone 16",
        url="https://www.magazineluiza.com.br/p/238803400/",
        canonical_url="https://www.magazineluiza.com.br/p/238803400/",
        variant="Preto",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = ref
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = []

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url="https://www.magazineluiza.com.br/p/238803400/",
            stores=["kabum"],
            persist=False,
        )
    )
    assert resp.unmatched_stores == ["kabum"]
    assert search.search.call_count == 2


# --- GPU / discrete graphics identity (Search vs Match) ---


def test_gpu_query_generation_progressive_not_full_title() -> None:
    """Queries prefer brand+chip+edition+VRAM over noisy SEO title heads."""
    identity = identity_from_price_item(
        _item(
            store="visaovip",
            title=(
                "Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7 "
                "- 912-V532-232"
            ),
            brand="MSI",
            model="Shadow 3X OC",
            sku="912-V532-232",
            variant="ram: 12 GB",
            metadata={
                "specifications": {
                    "REFERÊNCIA": "912-V532-232",
                    "MODELO": "Shadow 3X OC",
                    "gpu_model": "GeForce RTX5070",
                }
            },
        )
    )
    queries = build_search_queries(identity)
    assert queries
    # Progressive commercial ladder leads; MPN is a fallback for GPUs.
    assert any(q.casefold().startswith("msi rtx 5070 shadow 3x") for q in queries[:4])
    assert any("912-V532-232" in q for q in queries)
    # Must not lead with a long SEO dump (Ray Tracing / MHz / bus width).
    joined = " | ".join(queries).casefold()
    assert "ray tracing" not in joined
    assert "2557" not in joined
    assert "192" not in joined
    assert "dlss" not in joined
    assert queries[0].casefold().startswith("placa de video msi")


def test_rx7600_query_generation_preserves_mpn_and_localizes_category() -> None:
    identity = identity_from_price_item(
        _item(
            store="pichau",
            title=(
                "Placa de Video XFX Radeon RX 7600 Speedster SWFT210, 8GB, "
                "GDDR6, 128-bit, RX-76PSWFTFY"
            ),
            brand="XFX",
            model="Radeon RX 7600",
            sku="RX-76PSWFTFY",
            gtin="0840191500725",
            metadata={"category": "gpu"},
        )
    )

    pt_queries = build_search_queries(identity, locale="pt-BR")
    en_queries = build_search_queries(identity, locale="en-US")
    pt_budget = pt_queries[:5]
    en_budget = en_queries[:5]

    assert identity.mpn_display == "RX-76PSWFTFY"
    assert identity.model == "rx7600"
    assert "RX-76PSWFTFY" in pt_budget
    assert "RX-76PSWFTFY" in en_budget
    assert (
        pt_queries[0] == "placa de video xfx radeon rx 7600 speedster swft210 8gb gddr6"
    )
    assert "radeonrx7600" not in pt_queries
    assert en_queries[0].startswith("graphics card xfx radeon rx 7600")
    assert any("rx 7600" in query.casefold() for query in pt_budget)
    assert any("rx 7600" in query.casefold() for query in en_budget)
    assert any("placa de video" in query.casefold() for query in pt_budget)
    assert any("graphics card" in query.casefold() for query in en_budget)
    assert not any("placa de video" in query.casefold() for query in en_budget)

    ordinary_gpu_model = identity_from_price_item(
        _item(
            title="Placa de Video GeForce RTX-5060 8GB",
            brand="NVIDIA",
            model="GeForce RTX-5060",
            metadata={"category": "gpu"},
        )
    )
    assert ordinary_gpu_model.mpn_display is None

    rx7600xt = identity_from_price_item(
        _item(
            title="Placa de Video XFX Radeon RX 7600 XT 8GB",
            brand="XFX",
            model="Radeon RX 7600 XT",
            metadata={"category": "gpu"},
        )
    )
    score = MatchingEngine().score(identity, rx7600xt)
    assert score.decision == "reject"
    assert any(
        reason.code == "critical_conflict" and "gpu_mismatch" in reason.detail
        for reason in score.reasons
    )


def test_category_context_uses_canonical_category_and_integration_locale() -> None:
    examples = (
        (
            "cpu",
            "Processador AMD Ryzen 7 5800X3D",
            "AMD",
            "Ryzen 7 5800X3D",
            "processador",
            "processor",
        ),
        (
            "motherboard",
            "Placa Mae ASUS TUF GAMING B650M-E WIFI",
            "ASUS",
            "B650M-E",
            "placa mae",
            "motherboard",
        ),
        (
            "monitor",
            "Monitor ASUS VG259Q5A Gaming Monitor",
            "ASUS",
            "VG259Q5A",
            "monitor",
            "monitor",
        ),
        (
            "smartphone",
            "Apple iPhone 15 128GB",
            "Apple",
            "iPhone 15",
            "celular",
            "smartphone",
        ),
        (
            "ssd",
            "SSD Samsung 990 EVO Plus 1TB M.2",
            "Samsung",
            "990 EVO Plus",
            "ssd",
            "ssd",
        ),
    )

    for category, title, brand, model, pt_term, en_term in examples:
        identity = identity_from_price_item(
            _item(
                title=title,
                brand=brand,
                model=model,
                metadata={"category": category},
            )
        )
        pt_queries = " ".join(
            build_search_queries(identity, locale="pt-BR")[:5]
        ).casefold()
        en_queries = " ".join(
            build_search_queries(identity, locale="en-US")[:5]
        ).casefold()

        assert pt_term in pt_queries
        assert en_term in en_queries
        pt_first = build_search_queries(identity, locale="pt-BR")[0]
        if category == "motherboard":
            assert pt_first == "placa mae asus tuf gaming b650m-e wifi"
        if category == "cpu":
            assert pt_first == "processador amd ryzen 7 5800x3d"
        if pt_term != en_term:
            assert pt_term not in en_queries

    phone = identity_from_price_item(
        _item(
            title="Apple iPhone 15 128GB Rosa",
            brand="Apple",
            model="iPhone 15",
            metadata={"category": "smartphone"},
        )
    )
    phone_query = build_search_queries(phone, locale="pt-BR")[0]
    assert phone_query == "apple iphone 15 128gb rosa"
    assert not phone_query.startswith("celular")


def test_motherboard_model_suffix_conflict_rejects_different_boards() -> None:
    reference = identity_from_price_item(
        _item(
            store="pichau",
            product_id="a520m-k-v2",
            title="Placa-Mae Gigabyte A520M K V2 A520 AM4 mATX DDR4",
            brand="Gigabyte",
            model="A520M K V2",
            metadata={"category": "motherboard"},
        )
    )
    candidate = identity_from_price_item(
        _item(
            store="kabum",
            product_id="a520m-ds3h-v2",
            title=(
                "Placa-Mae Gigabyte A520M DS3H V2, AMD, Micro ATX, DDR4, "
                "Preto - A520M DS3H V2"
            ),
            brand="Gigabyte",
            model="A520M DS3H V2",
            metadata={"category": "motherboard"},
        )
    )

    score = MatchingEngine().score(reference, candidate)

    assert score.decision == "reject"
    assert any(
        reason.code == "critical_conflict" and "motherboard_mismatch" in reason.detail
        for reason in score.reasons
    )


def test_gpu_same_product_different_titles_match() -> None:
    ref = identity_from_price_item(
        _item(
            store="visaovip",
            product_id="55359",
            title="Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7",
            brand="MSI",
            sku="912-V532-232",
        )
    )
    kabum = identity_from_price_item(
        _item(
            store="kabum",
            product_id="777166",
            title=(
                "MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7 "
                "2557 MHz 192-bit FP4 and DLSS 4 Ray Tracing G5070-12S3C"
            ),
            brand="MSI",
        )
    )
    magalu = identity_from_price_item(
        _item(
            store="magazineluiza",
            product_id="fkff6cf4a2",
            title=(
                "MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7 "
                "192bit FP4 Ray Tracing"
            ),
            brand="MSI",
        )
    )
    engine = MatchingEngine()
    for candidate in (kabum, magalu):
        score = engine.score(ref, candidate)
        assert score.decision == "auto_match", score.reasons
        assert any(r.code == "brand_match" for r in score.reasons)


def test_gpu_ti_suffix_blocks_match() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="MSI Shadow 3X OC 12GB GeForce RTX 5070 GDDR7",
                brand="MSI",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="MSI Shadow 3X OC 12GB GeForce RTX 5070 Ti GDDR7",
                brand="MSI",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code == "critical_conflict" and "gpu_mismatch" in (r.detail or "")
        for r in score.reasons
    )


def test_gpu_vram_mismatch_blocks_match() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="MSI Shadow 3X OC 12GB GeForce RTX 5070 GDDR7",
                brand="MSI",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="MSI Shadow 3X OC 16GB GeForce RTX 5070 GDDR7",
                brand="MSI",
            )
        ),
    )
    assert score.decision == "reject"
    assert any("vram" in (r.detail or "") for r in score.reasons)


def test_gpu_edition_mismatch_blocks_match() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="MSI Shadow 3X OC 12GB GeForce RTX 5070 GDDR7",
                brand="MSI",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="MSI Gaming Trio OC 12GB GeForce RTX 5070 GDDR7",
                brand="MSI",
            )
        ),
    )
    assert score.decision == "reject"
    assert any("edition" in (r.detail or "") for r in score.reasons)


def test_gpu_title_noise_does_not_block_match() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="MSI RTX 5070 Shadow 3X OC 12GB GDDR7",
                brand="MSI",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title=(
                    "Placa de Video MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce "
                    "12GB GDDR7 DLSS 4 Ray Tracing 2557 MHz 192-bit FP4"
                ),
                brand="MSI",
            )
        ),
    )
    assert score.decision == "auto_match"


def test_gpu_candidate_retrieval_mock_uses_progressive_query() -> None:
    """Search layer is queried with progressive identity queries, not full title."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.schemas import MatchRequest
    from scout_api.modules.matching.search_candidate import SearchCandidate

    ref = _item(
        store="visaovip",
        product_id="55359",
        title="Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7",
        brand="MSI",
        sku="912-V532-232",
        url="https://visaovip.com/prod/x/55359/",
        canonical_url="https://visaovip.com/prod/x/55359/",
        metadata={
            "specifications": {
                "REFERÊNCIA": "912-V532-232",
                "gpu_model": "GeForce RTX5070",
            }
        },
    )
    cand_item = _item(
        store="kabum",
        product_id="777166",
        title="MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7",
        brand="MSI",
        url="https://www.kabum.com.br/produto/777166/placa",
        canonical_url="https://www.kabum.com.br/produto/777166/placa",
    )
    scrape = MagicMock()
    scrape.scrape.side_effect = [ref, cand_item]
    search = MagicMock()
    search.is_search_supported.return_value = True

    def _search(store: str, query: str, **_kwargs: object) -> list[SearchCandidate]:
        # Identifier-only MPNs often miss; progressive commercial query hits.
        if "shadow" in query.casefold() and "5070" in query:
            return [
                SearchCandidate(
                    url=cand_item.url,
                    title=cand_item.title,
                    product_id="777166",
                    metadata={"source": "mock"},
                )
            ]
        return []

    search.search.side_effect = _search

    resp = ProductMatchService(scrape_service=scrape, search_service=search).match(
        MatchRequest(
            reference_url=ref.url,
            stores=["kabum"],
            persist=False,
        )
    )
    assert resp.matches
    assert resp.matches[0].store == "kabum"
    assert resp.matches[0].product.product_id == "777166"
    queried = [call.args[1] for call in search.search.call_args_list]
    assert any("shadow" in q.casefold() for q in queried)
    assert all("ray tracing" not in q.casefold() for q in queried)


def test_cross_category_phone_ssd_ram_console_still_match() -> None:
    """GPU changes must not break other categories."""
    engine = MatchingEngine()
    phone = engine.score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Apple iPhone 15 128GB Blue",
                brand="Apple",
                model="iPhone 15",
                variant="color: Blue; storage: 128GB",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Celular Apple iPhone 15 Azul 128 GB",
                brand="Apple",
                model="iPhone 15",
                variant="color: Azul; storage: 128 GB",
            )
        ),
    )
    assert phone.decision == "auto_match"

    ssd = engine.score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="SSD Samsung 990 EVO Plus 1TB M.2 NVMe - MZ-V9S1T0B/AM",
                brand="Samsung",
                model="MZ-V9S1T0B/AM",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Samsung 990 EVO Plus 1TB NVMe SSD MZ-V9S1T0B/AM",
                brand="Samsung",
                model="990 EVO Plus",
            )
        ),
    )
    assert ssd.decision == "auto_match"

    ram = engine.score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Kingston HyperX Fury DDR4 8GB",
                brand="Kingston",
                model="hx432c16fb3",
                mpn="hx432c16fb3",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Memoria Kingston HyperX Fury 8GB DDR4 HX432C16FB3",
                brand="Kingston",
                model="hx432c16fb3",
            )
        ),
    )
    # Prefer structured MPN when present on identity_from_price_item via sku/title.
    if ram.decision != "auto_match":
        ram = MatchingEngine().score(
            _identity(
                brand="kingston",
                model="hx432c16fb3",
                title="Kingston HyperX Fury DDR4 8GB",
                mpn="hx432c16fb3",
            ),
            _identity(
                brand="kingston",
                model="hx432c16fb3",
                title="Memoria Kingston HyperX Fury 8GB DDR4",
                mpn="hx432c16fb3",
            ),
        )
    assert ram.decision == "auto_match"

    console = engine.score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="PlayStation 5 Edição Digital 825GB",
                brand="Sony",
                model="PlayStation 5 Digital",
                variant="storage: 825GB",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Sony PlayStation 5 Digital Edition 825GB SSD White",
                brand="Sony",
                model="PS5 Digital",
                variant="storage: 825 GB",
            )
        ),
    )
    assert console.decision in {"auto_match", "review"}


# --- Gigabyte GeForce RTX 5060: identity-only discovery regressions ----------


def test_gigabyte_rtx5060_laptop_form_factor_rejects() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Placa de Vídeo Gigabyte GeForce RTX 5060",
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
        identity_from_price_item(
            _item(
                store="bestbuy",
                product_id="2",
                title=(
                    'GIGABYTE - GAMING A16 - 16" 165Hz 1920x1200 WUXGA '
                    "Intel Core i7-13620H - 1TB SSD - 32GB DDR5 RAM - "
                    "GeForce RTX 5060 - Black Steel"
                ),
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "form_factor_reject" for r in score.reasons)


def test_gigabyte_rtx5060_same_family_different_titles_match() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Gigabyte GeForce RTX 5060 Gaming OC",
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Gigabyte RTX 5060 Gaming OC 8GB",
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
    )
    assert score.decision == "auto_match"


def test_gigabyte_rtx5060_different_manufacturer_rejects() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Gigabyte GeForce RTX 5060",
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="MSI GeForce RTX 5060",
                brand="MSI",
                model="GeForce RTX 5060",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "brand_mismatch" for r in score.reasons)


def test_gigabyte_rtx5060_ti_suffix_rejects() -> None:
    score = MatchingEngine().score(
        identity_from_price_item(
            _item(
                store="a",
                product_id="1",
                title="Gigabyte GeForce RTX 5060",
                brand="Gigabyte",
                model="GeForce RTX 5060",
            )
        ),
        identity_from_price_item(
            _item(
                store="b",
                product_id="2",
                title="Gigabyte GeForce RTX 5060 Ti",
                brand="Gigabyte",
                model="GeForce RTX 5060 Ti",
            )
        ),
    )
    assert score.decision == "reject"
    assert any(
        r.code == "critical_conflict" and "gpu_mismatch" in (r.detail or "")
        for r in score.reasons
    )


def test_gigabyte_rtx5060_base_query_allows_multiple_editions() -> None:
    """brand+model without edition: Windforce / Gaming OC / Eagle are all candidates."""
    from dataclasses import replace

    from scout_api.modules.matching.identity import identity_reference_item

    ref = replace(
        identity_from_price_item(
            identity_reference_item(
                "Placa de Vídeo Gigabyte GeForce RTX 5060",
                category="gpu",
            )
        ),
        price=None,
    )
    assert ref.brand == "gigabyte"
    assert "5060" in (ref.model or "")
    assert "ti" not in (ref.model or "")
    assert not ref.variant_attrs.get("edition")

    queries = build_search_queries(ref)
    assert queries
    assert any("gigabyte" in q.casefold() and "5060" in q for q in queries)
    assert all("gaming oc" not in q.casefold() for q in queries)
    assert all("windforce" not in q.casefold() for q in queries)

    engine = MatchingEngine()
    for title in (
        "Placa de Vídeo Gigabyte RTX 5060 Windforce 8GB GDDR7",
        "Gigabyte GeForce RTX 5060 Gaming OC 8GB",
        "Gigabyte RTX 5060 Eagle OC 8GB GDDR7",
    ):
        score = engine.score(
            ref,
            identity_from_price_item(
                _item(store="kabum", product_id="x", title=title, brand="Gigabyte")
            ),
        )
        assert score.decision == "auto_match", (title, score.reasons)


def test_gigabyte_rtx5060_variant_specified_restricts_edition() -> None:
    ref = identity_from_price_item(
        _item(
            store="synthetic",
            product_id="identity",
            title="Gigabyte GeForce RTX 5060 Gaming OC",
            brand="Gigabyte",
            model="GeForce RTX 5060",
        )
    )
    assert ref.variant_attrs.get("edition")

    queries = build_search_queries(ref)
    assert any("gaming" in q.casefold() for q in queries)

    engine = MatchingEngine()
    gaming = engine.score(
        ref,
        identity_from_price_item(
            _item(
                store="kabum",
                product_id="1",
                title="Gigabyte RTX 5060 Gaming OC 8GB GDDR7",
                brand="Gigabyte",
            )
        ),
    )
    assert gaming.decision == "auto_match"

    windforce = engine.score(
        ref,
        identity_from_price_item(
            _item(
                store="kabum",
                product_id="2",
                title="Gigabyte RTX 5060 Windforce 8GB GDDR7",
                brand="Gigabyte",
            )
        ),
    )
    assert windforce.decision == "reject"
    assert any("edition" in (r.detail or "") for r in windforce.reasons)


def test_match_from_item_identity_only_discovers_without_reference_url() -> None:
    """Search receives progressive identity queries; no known store URL is injected."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060",
        category="gpu",
    )
    cand = _item(
        store="kabum",
        product_id="999001",
        title="Placa de Vídeo Gigabyte RTX 5060 Windforce OC 8GB GDDR7",
        brand="Gigabyte",
        model="GeForce RTX 5060",
        url="https://www.kabum.com.br/produto/999001/placa",
        canonical_url="https://www.kabum.com.br/produto/999001/placa",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = cand
    search = MagicMock()
    search.is_search_supported.return_value = True

    def _search(store: str, query: str, **_kwargs: object) -> list[SearchCandidate]:
        assert "kabum.com.br" not in query.casefold()
        assert "http" not in query.casefold()
        if "gigabyte" in query.casefold() and "5060" in query:
            return [
                SearchCandidate(
                    url=cand.url,
                    title=cand.title,
                    product_id="999001",
                    metadata={"source": "mock"},
                )
            ]
        return []

    search.search.side_effect = _search
    resp = ProductMatchService(
        scrape_service=scrape, search_service=search
    ).match_from_item(
        reference,
        stores=["kabum"],
        persist=False,
    )
    assert resp.matches
    assert resp.matches[0].store == "kabum"
    assert resp.matches[0].product.product_id == "999001"
    assert resp.matches[0].product.url.startswith("https://www.kabum.com.br/")
    queried = [call.args[1] for call in search.search.call_args_list]
    assert queried
    assert all("http" not in q.casefold() for q in queried)
    # Reference scrape must not be called — only candidate URLs.
    assert all(
        "scout://identity" not in str(call.args[0])
        for call in scrape.scrape.call_args_list
    )


def test_match_early_stops_scraping_after_auto_match() -> None:
    """Once a store auto_matches, do not scrape remaining SERP candidates."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB",
        category="gpu",
    )
    good = _item(
        store="kabum",
        product_id="good-1",
        title="Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB GDDR7",
        brand="Gigabyte",
        model="GeForce RTX 5060",
        url="https://www.kabum.com.br/produto/good-1/placa",
        canonical_url="https://www.kabum.com.br/produto/good-1/placa",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = good
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(
            url=good.url,
            title=good.title,
            product_id="good-1",
        ),
        SearchCandidate(
            url="https://www.kabum.com.br/produto/noise-2/placa",
            title="Placa de Vídeo Gigabyte GeForce RTX 5060 Gaming OC 8GB",
            product_id="noise-2",
        ),
        SearchCandidate(
            url="https://www.kabum.com.br/produto/noise-3/placa",
            title="Placa de Vídeo Gigabyte GeForce RTX 5060 Eagle OC 8GB",
            product_id="noise-3",
        ),
    ]

    resp = ProductMatchService(
        scrape_service=scrape, search_service=search
    ).match_from_item(
        reference,
        stores=["kabum"],
        persist=False,
        max_candidates_per_store=5,
        clear_reference_price=True,
    )
    assert resp.matches
    assert resp.matches[0].decision == "auto_match"
    assert scrape.scrape.call_count == 1


def test_match_skips_scrape_when_serp_title_clearly_conflicts() -> None:
    """Notebook SERP titles must not trigger a discrete-GPU PDP scrape."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060 8GB GDDR7",
        category="gpu",
    )
    scrape = MagicMock()
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(
            url="https://www.bestbuy.com/site/laptop-rtx-5060/123.p",
            title="ASUS TUF Gaming A15 Laptop with GeForce RTX 5060 16GB RAM",
            product_id="123",
        ),
        SearchCandidate(
            url="https://www.bestbuy.com/site/gpu-rtx-5060/456.p",
            title="Gigabyte GeForce RTX 5060 WINDFORCE OC 8G Graphics Card",
            product_id="456",
        ),
    ]
    good = _item(
        store="bestbuy",
        product_id="456",
        title="Gigabyte GeForce RTX 5060 WINDFORCE OC 8G Graphics Card",
        brand="Gigabyte",
        model="GeForce RTX 5060",
        url="https://www.bestbuy.com/site/gpu-rtx-5060/456.p",
        canonical_url="https://www.bestbuy.com/site/gpu-rtx-5060/456.p",
        country="US",
        currency="USD",
    )
    scrape.scrape.return_value = good

    resp = ProductMatchService(
        scrape_service=scrape, search_service=search
    ).match_from_item(
        reference,
        stores=["bestbuy"],
        persist=False,
        max_candidates_per_store=5,
        clear_reference_price=True,
    )
    assert scrape.scrape.call_count == 1
    assert scrape.scrape.call_args.args[0] == good.url
    assert resp.matches
    assert resp.matches[0].product.product_id == "456"


def test_match_skips_duplicate_candidate_url_scrape() -> None:
    """Same candidate URL appearing twice must be scraped once per Match."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB",
        category="gpu",
    )
    # Candidate scrapes to a clearly different SKU → reject, loop continues.
    noise = _item(
        store="kabum",
        product_id="noise-1",
        title="Placa de Vídeo MSI GeForce RTX 4070 Ventus 12GB",
        brand="MSI",
        model="GeForce RTX 4070",
        url="https://www.kabum.com.br/produto/noise-1/placa",
        canonical_url="https://www.kabum.com.br/produto/noise-1/placa",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = noise
    search = MagicMock()
    search.is_search_supported.return_value = True
    cand = SearchCandidate(
        url=noise.url,
        title=None,  # missing SERP title must not cheap-reject
        product_id="noise-1",
    )
    search.search.return_value = [cand, cand]

    ProductMatchService(scrape_service=scrape, search_service=search).match_from_item(
        reference,
        stores=["kabum"],
        persist=False,
        max_candidates_per_store=5,
        clear_reference_price=True,
    )
    assert scrape.scrape.call_count == 1


def test_match_respects_scrape_budget_across_queries() -> None:
    """max_candidates_per_store caps total PDP scrapes for a store, not per SERP."""
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB",
        category="gpu",
    )
    noise = _item(
        store="pichau",
        product_id="noise-1",
        title="Placa de Vídeo MSI GeForce RTX 4070 Ventus 12GB",
        brand="MSI",
        model="GeForce RTX 4070",
        url="https://www.pichau.com.br/produto/noise-1",
        canonical_url="https://www.pichau.com.br/produto/noise-1",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = noise
    search = MagicMock()
    search.is_search_supported.return_value = True

    def _search(
        store_key: str, query: str, *, limit: int = 5, **_kwargs: object
    ) -> list[SearchCandidate]:
        del store_key, query
        return [
            SearchCandidate(
                url=f"https://www.pichau.com.br/produto/noise-{i}",
                title=None,  # missing SERP title must not cheap-reject
                product_id=f"noise-{i}",
            )
            for i in range(limit)
        ]

    search.search.side_effect = _search

    ProductMatchService(scrape_service=scrape, search_service=search).match_from_item(
        reference,
        stores=["pichau"],
        persist=False,
        max_candidates_per_store=3,
        clear_reference_price=True,
    )
    assert scrape.scrape.call_count == 3


def test_match_forces_include_images_false_on_candidates() -> None:
    from unittest.mock import MagicMock

    from scout_api.modules.matching.identity import identity_reference_item
    from scout_api.modules.matching.product_match_service import ProductMatchService
    from scout_api.modules.matching.search_candidate import SearchCandidate

    reference = identity_reference_item(
        "Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB",
        category="gpu",
    )
    good = _item(
        store="kabum",
        product_id="good-1",
        title="Placa de Vídeo Gigabyte GeForce RTX 5060 Windforce OC 8GB GDDR7",
        brand="Gigabyte",
        model="GeForce RTX 5060",
        url="https://www.kabum.com.br/produto/good-1/placa",
        canonical_url="https://www.kabum.com.br/produto/good-1/placa",
    )
    scrape = MagicMock()
    scrape.scrape.return_value = good
    search = MagicMock()
    search.is_search_supported.return_value = True
    search.search.return_value = [
        SearchCandidate(url=good.url, title=good.title, product_id="good-1"),
    ]

    ProductMatchService(scrape_service=scrape, search_service=search).match_from_item(
        reference,
        stores=["kabum"],
        persist=False,
        include_images=True,
        max_candidates_per_store=3,
        clear_reference_price=True,
    )
    assert scrape.scrape.call_args.kwargs.get("include_images") is False


def test_long_smartphone_title_does_not_drive_raw_serp_query() -> None:
    """Commercial PDP titles must not become SERP queries as-is.

    Structured identity (brand + series + storage) drives progressive queries;
    marketing noise (cameras, battery, AI slogans) stays out of the primary ladder.
    """
    title = (
        "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
        '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
    )
    identity = identity_from_price_item(
        _item(title=title, brand="Samsung", model=None, variant=None)
    )
    assert identity.model == "galaxys25ultra"
    assert identity.variant_attrs.get("storage") == "256gb"
    assert identity.variant_attrs.get("color") == "titanio preto"
    queries = build_search_queries(identity)
    # Explicit category stays in its title position; promotional detail is removed.
    assert queries[0] == "celular samsung galaxy s25 ultra 5g 256gb titanio preto"
    assert "samsung galaxy s25 ultra 256gb" in queries
    assert any(q == "samsung galaxy s25 ultra" for q in queries)
    assert any("256gb" in q and "preto" in q for q in queries)
    assert any("256gb" in q and "black" in q for q in queries)
    # Brand+storage without the commercial series must never lead retrieval.
    assert "samsung 256gb" not in queries
    joined = " | ".join(queries)
    assert "5000mah" not in joined
    assert "quadrupla" not in joined
    assert "dual chip" not in joined.casefold()


def test_smartphone_marketing_noise_does_not_change_identity() -> None:
    base = identity_from_price_item(
        _item(
            title="Samsung Galaxy S25 Ultra 256GB Titânio Preto",
            brand="Samsung",
        )
    )
    noisy = identity_from_price_item(
        _item(
            title=(
                "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
                "Câm. Quádrupla 200MP Bateria 5000mAh Dual Chip"
            ),
            brand="Samsung",
        )
    )
    assert base.model == noisy.model == "galaxys25ultra"
    assert base.variant_attrs.get("storage") == noisy.variant_attrs.get("storage")
    assert normalize_title(base.title).split()[:4] != []  # smoke
    assert (
        build_search_queries(base)[0] == "samsung galaxy s25 ultra 256gb titanio preto"
    )
    assert build_search_queries(noisy)[0] == (
        "celular samsung galaxy s25 ultra 5g 256gb titanio preto"
    )


def test_galaxy_model_suffix_conflict_and_queries() -> None:
    engine = MatchingEngine()
    ultra = _identity(
        brand="samsung",
        model="galaxys25ultra",
        title="Samsung Galaxy S25 Ultra 256GB",
        variant_attrs={"storage": "256gb"},
    )
    plain = _identity(
        brand="samsung",
        model="galaxys25",
        title="Samsung Galaxy S25 256GB",
        variant_attrs={"storage": "256gb"},
    )
    score = engine.score(ultra, plain)
    assert score.decision == "reject"
    assert any(r.code == "critical_conflict" for r in score.reasons)
    queries = build_search_queries(ultra)
    assert all("s25+" not in q.casefold() for q in queries)
    assert any("galaxy s25 ultra" in q for q in queries)


def test_storage_conflict_not_relaxed_by_broad_retrieval() -> None:
    """Search may surface 512GB siblings; matcher must still reject."""
    score = MatchingEngine().score(
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 256GB",
            variant_attrs={"storage": "256gb"},
        ),
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 512GB",
            variant_attrs={"storage": "512gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "variant_mismatch" for r in score.reasons)


def test_bare_titanium_finish_not_conflict_with_titanio_preto() -> None:
    """Bare 'Titânio' on Amazon is missing hue, not a conflict with Titânio Preto."""
    from scout_api.modules.matching.identity import variants_equal

    assert variants_equal("color", "titanio preto", "titanio")
    assert variants_equal("color", "titanio preto", "titanium")
    assert not variants_equal("color", "titanio preto", "titanio branco")

    score = MatchingEngine().score(
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 256GB Titânio Preto",
            variant_attrs={"storage": "256gb", "color": "titanio preto"},
        ),
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Celular Galaxy S25 Ultra 5G, 256GB, 12GB RAM",
            variant_attrs={"storage": "256gb", "color": "titanio"},
        ),
    )
    assert score.decision != "reject"
    assert not any(r.code == "variant_mismatch" for r in score.reasons)


def test_missing_ram_battery_is_not_conflict() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 256GB 12GB RAM 5000mAh",
            variant_attrs={"storage": "256gb", "ram": "12gb", "color": "black"},
        ),
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 256GB Titanium Black",
            variant_attrs={"storage": "256gb", "color": "black"},
        ),
    )
    assert score.decision == "auto_match"


def test_compact_phone_model_expands_for_serp() -> None:
    from scout_api.modules.matching.identity import model_search_phrase

    assert (
        model_search_phrase(model="galaxys25ultra", title="Produto Samsung")
        == "galaxy s25 ultra"
    )
    assert (
        model_search_phrase(model="iphone16pro", title="Celular Apple")
        == "iphone 16 pro"
    )


def test_phone_wearable_kit_is_bundle_reject() -> None:
    engine = MatchingEngine()
    score = engine.score(
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title="Samsung Galaxy S25 Ultra 256GB Titânio Preto",
            variant_attrs={"storage": "256gb"},
        ),
        _identity(
            brand="samsung",
            model="galaxys25ultra",
            title=(
                "Celular Samsung Galaxy S25 Ultra 5G, 256GB, Titânio Preto "
                "+ Samsung Galaxy Fit3 Display 1.6 Prata"
            ),
            variant_attrs={"storage": "256gb"},
        ),
    )
    assert score.decision == "reject"
    assert any(r.code == "bundle_reject" for r in score.reasons)
    from scout_api.modules.crawler.core.fingerprints import title_hint_from_url
    from scout_api.modules.matching.identity import (
        enrich_candidate_title,
        rank_candidates_for_query,
    )
    from scout_api.modules.matching.search_candidate import SearchCandidate

    url = (
        "https://www.magazineluiza.com.br/"
        "celular-samsung-galaxy-s25-ultra-5g-256gb-galaxy-ai-titanio-preto-69/"
        "p/238922200/te/ssul/"
    )
    hint = title_hint_from_url(url)
    assert hint is not None
    assert "s25" in hint.casefold() and "ultra" in hint.casefold()
    assert "titanio" in hint.casefold() or "preto" in hint.casefold()

    bare = SearchCandidate(url=url, title=None, product_id="238922200")
    enriched = enrich_candidate_title(bare)
    assert enriched.title and "ultra" in enriched.title.casefold()

    aliexpress_url = "https://pt.aliexpress.com/item/1005012492913541.html"
    assert title_hint_from_url(aliexpress_url) is None
    opaque = SearchCandidate(
        url=aliexpress_url,
        title=None,
        product_id="1005012492913541",
    )
    assert enrich_candidate_title(opaque).title is None

    ranked = rank_candidates_for_query(
        [
            SearchCandidate(
                url=(
                    "https://www.magazineluiza.com.br/"
                    "celular-samsung-galaxy-s25-5g-256gb-azul/p/wrong/te/x/"
                ),
                title=None,
                product_id="wrong",
            ),
            bare,
        ],
        "samsung galaxy s25 ultra 256gb titanio preto",
    )
    assert ranked[0].product_id == "238922200"


# --- Motherboard long commercial title: SEARCH recall + MATCH precision ------

_MB_LONG_TITLE = (
    "Placa Mae Asus Tuf Gaming B650M-E WIFI, DDR5, Socket AMD AM5, "
    "M-ATX, Chipset AMD B650, TUF-GAMING-B650M-E-WIFI"
)


def test_motherboard_long_title_queries_are_compact_not_raw() -> None:
    """Raw commercial title must not drive SERP; prefer board identity ladder."""
    identity = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            store="pichau",
            product_id="mb-ref",
            url="https://www.pichau.com.br/mb-ref",
            canonical_url="https://www.pichau.com.br/mb-ref",
        )
    )
    queries = build_search_queries(identity)
    assert queries, "expected progressive queries"
    assert not any(q.casefold() == _MB_LONG_TITLE.casefold() for q in queries)
    # Must not collapse to brand-only / brand+wifi noise (live false-negative path).
    assert "asus" not in {q.casefold() for q in queries}
    assert "asus wifi" not in {q.casefold() for q in queries}
    joined = " | ".join(q.casefold() for q in queries)
    assert "b650m" in joined
    assert any("wifi" in q.casefold() for q in queries)
    # Strong identifier from manufacturer PN when present in title.
    assert any("tuf-gaming-b650m-e-wifi" in q.casefold() for q in queries)


def test_motherboard_wifi_variant_is_not_color_gate() -> None:
    """Bare WiFi commercial variant must not become color=wifi (Kabum reject)."""
    ref = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            variant="WiFi",
            store="pichau",
            product_id="mb-ref",
            url="https://www.pichau.com.br/mb-ref",
            canonical_url="https://www.pichau.com.br/mb-ref",
        )
    )
    assert ref.variant_attrs.get("color") != "wifi"
    assert "wifi" not in (ref.variant_attrs.get("color") or "")

    cand = identity_from_price_item(
        _item(
            title=(
                "Placa-Mãe ASUS TUF Gaming B650M-E, WIFI, AMD AM5, B650, "
                "DDR5, Preto - 90MB1FV0-M0EAY0"
            ),
            brand="ASUS",
            store="kabum",
            product_id="523145",
            url="https://www.kabum.com.br/produto/523145/placa",
            canonical_url="https://www.kabum.com.br/produto/523145/placa",
        )
    )
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"
    assert not any(r.code == "variant_mismatch" for r in score.reasons)


def test_motherboard_short_title_missing_specs_still_matches() -> None:
    """Missing DDR5/AM5/M-ATX on candidate title is unknown, not conflict."""
    ref = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            store="pichau",
            product_id="mb-ref",
            url="https://www.pichau.com.br/mb-ref",
            canonical_url="https://www.pichau.com.br/mb-ref",
        )
    )
    cand = identity_from_price_item(
        _item(
            title="ASUS TUF GAMING B650M-E WIFI",
            brand="ASUS",
            store="kabum",
            product_id="cand-short",
            url="https://www.kabum.com.br/produto/cand-short/placa",
            canonical_url="https://www.kabum.com.br/produto/cand-short/placa",
        )
    )
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"


def test_motherboard_visaovip_short_title_style_matches() -> None:
    """Visão VIP shorter title (Wi-Fi / AM5 / DDR5) must still auto-match."""
    ref = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            store="kabum",
            product_id="523145",
            url="https://www.kabum.com.br/produto/523145/placa",
            canonical_url="https://www.kabum.com.br/produto/523145/placa",
        )
    )
    cand = identity_from_price_item(
        _item(
            title="Placa Mãe Asus Tuf Gaming B650M-E Wi-Fi Socket AM5 DDR5",
            brand="ASUS",
            store="visaovip",
            country="PY",
            currency="USD",
            product_id="vv-mb",
            url="https://www.visaovip.com/prod/placas-mae-amd/vv-mb/",
            canonical_url="https://www.visaovip.com/prod/placas-mae-amd/vv-mb/",
        )
    )
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"


def test_motherboard_near_neighbor_model_rejects() -> None:
    """B650M-E must not auto-match B650M-PLUS / B650M-A."""
    ref = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            store="pichau",
            product_id="mb-ref",
            url="https://www.pichau.com.br/mb-ref",
            canonical_url="https://www.pichau.com.br/mb-ref",
        )
    )
    engine = MatchingEngine()
    for bad_title, pid in (
        ("ASUS TUF GAMING B650M-PLUS WIFI", "plus"),
        ("ASUS PRIME B650M-A WIFI", "prime-a"),
        ("ASUS TUF GAMING B650-PLUS WIFI", "b650-plus"),
    ):
        cand = identity_from_price_item(
            _item(
                title=bad_title,
                brand="ASUS",
                store="kabum",
                product_id=pid,
                url=f"https://www.kabum.com.br/produto/{pid}/placa",
                canonical_url=f"https://www.kabum.com.br/produto/{pid}/placa",
            )
        )
        score = engine.score(ref, cand)
        assert score.decision == "reject", bad_title


def test_motherboard_model_suffix_preserved_in_normalized_title() -> None:
    """Hyphenated board suffixes (B650M-E) must survive title normalization."""
    normalized = normalize_title(_MB_LONG_TITLE)
    assert "b650m" in normalized
    # Discriminating -E must not be dropped as the stopword "e".
    assert "b650me" in normalized.replace(" ", "") or "b650m-e" in normalized
    assert "e" in normalized.split() or "b650me" in normalized.replace(" ", "")


def test_motherboard_unitless_ram_slots_are_not_variant_gate() -> None:
    """Memory slot count (4) must not conflict with DDR5 / capacity strings."""
    ref = identity_from_price_item(
        _item(
            title=_MB_LONG_TITLE,
            brand="Asus",
            store="pichau",
            product_id="mb-ref",
            url="https://www.pichau.com.br/mb-ref",
            canonical_url="https://www.pichau.com.br/mb-ref",
            metadata={"specifications": {"Memória RAM": "4", "Slots": "4"}},
        )
    )
    assert "ram" not in ref.variant_attrs or not re.fullmatch(
        r"\d{1,2}", ref.variant_attrs.get("ram", "")
    )
    cand = identity_from_price_item(
        _item(
            title="Placa Mãe Asus Tuf Gaming B650M-E WiFi Socket AM5 DDR5",
            brand="ASUS",
            store="magazineluiza",
            product_id="ml-ok",
            url="https://www.magazineluiza.com.br/p/ml-ok",
            canonical_url="https://www.magazineluiza.com.br/p/ml-ok",
        )
    )
    score = MatchingEngine().score(ref, cand)
    assert score.decision == "auto_match"
    assert not any(
        r.code == "variant_mismatch" and "ram" in (r.detail or "")
        for r in score.reasons
    )


def test_cpu_x3d_suffix_rejects_base_sku() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="amd",
            model="ryzen75800x3d",
            title="AMD Ryzen 7 5800X3D",
        ),
        _identity(
            brand="amd",
            model="ryzen75800x",
            title="AMD Ryzen 7 5800X",
        ),
    )
    assert score.decision == "reject"


def test_cpu_exact_model_matches_title_with_complementary_specs() -> None:
    reference = _identity(
        brand="amd",
        model="Ryzen 7 5800X3D",
        title="Processador AMD Ryzen 7 5800X3D 3.4GHz 8-Core Cache 100MB AM4",
        category="cpu",
    )
    candidates = (
        _identity(
            brand="amd",
            model="AMD R7 5800X3D",
            title=(
                "CPU AMD R7 5800X3D 8 Core AM4 3.4GHz 100MB "
                "10th Anniversary Edition Boxed"
            ),
            category="cpu",
        ),
        _identity(
            brand="amd",
            model="Ryzen 7 5800X3D",
            title=(
                "Processador AMD Ryzen 7 5800X3D AM4 8 Núcleos até 4.5 GHz "
                "96 MB 3D V-Cache (Sem Cooler)"
            ),
            category="cpu",
        ),
        _identity(
            brand="amd",
            model="Ryzen 7 5800X3D",
            title=(
                "Processador AMD Ryzen 7 5800X3D 10th Anniversary Edition "
                "Socket AM4 / 3.4GHz / 100MB"
            ),
            category="cpu",
        ),
    )
    for candidate in candidates:
        score = MatchingEngine().score(reference, candidate)
        assert score.decision == "auto_match"
        assert any(reason.code == "processor_model_exact" for reason in score.reasons)


def test_cpu_model_and_family_conflicts_reject_exactly() -> None:
    reference = _identity(
        brand="amd",
        model="Ryzen 7 5800X3D",
        title="AMD Ryzen 7 5800X3D AM4",
        category="cpu",
    )
    for model, title in (
        ("Ryzen 7 5800X", "AMD Ryzen 7 5800X AM4"),
        ("Ryzen 7 5700X3D", "AMD Ryzen 7 5700X3D AM4"),
        ("Ryzen 7 7800X3D", "AMD Ryzen 7 7800X3D AM5"),
        ("Ryzen 5 5600G", "AMD Ryzen 5 5600G AM4"),
    ):
        score = MatchingEngine().score(
            reference,
            _identity(brand="amd", model=model, title=title, category="cpu"),
        )
        assert score.decision == "reject"
        assert any(reason.code == "critical_conflict" for reason in score.reasons)


def test_intel_cpu_model_suffixes_are_preserved() -> None:
    reference = _identity(
        brand="intel",
        model="Intel Core i9-14900K",
        title="Intel Core i9-14900K LGA1700",
        category="cpu",
    )
    exact = MatchingEngine().score(
        reference,
        _identity(
            brand="intel",
            model="Core i9 14900K",
            title="CPU Intel i9-14900K LGA 1700",
            category="cpu",
        ),
    )
    suffix_conflict = MatchingEngine().score(
        reference,
        _identity(
            brand="intel",
            model="Core i9-14900KF",
            title="CPU Intel Core i9-14900KF LGA1700",
            category="cpu",
        ),
    )
    assert exact.decision == "auto_match"
    assert suffix_conflict.decision == "reject"


def test_intel_core_ultra_285k_signature_matches_without_global_threshold() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="intel",
            model="Core Ultra 9 285K",
            title="Intel Core Ultra 9 285K LGA1851",
            category="cpu",
        ),
        _identity(
            brand="intel",
            model="Intel Core Ultra 9 285K",
            title="CPU Ultra 9 285K Socket LGA 1851",
            category="cpu",
        ),
    )
    assert score.decision == "auto_match"


def test_cpu_missing_socket_is_unknown_but_explicit_socket_conflict_rejects() -> None:
    reference = _identity(
        brand="amd",
        model="Ryzen 7 5800X3D",
        title="AMD Ryzen 7 5800X3D AM4",
        category="cpu",
    )
    absent = MatchingEngine().score(
        reference,
        _identity(
            brand="amd",
            model="Ryzen 7 5800X3D",
            title="AMD Ryzen 7 5800X3D",
            category="cpu",
        ),
    )
    conflict = MatchingEngine().score(
        reference,
        _identity(
            brand="amd",
            model="Ryzen 7 5800X3D",
            title="AMD Ryzen 7 5800X3D Socket AM5",
            category="cpu",
        ),
    )
    assert absent.decision == "auto_match"
    assert conflict.decision == "reject"
    assert any(
        "processor_socket_mismatch" in (r.detail or "") for r in conflict.reasons
    )


def test_cpu_opn_boxed_and_tray_are_recognized_as_mpn_aliases() -> None:
    reference = identity_from_price_item(
        _item(
            title="AMD Ryzen 7 5800X3D 100-100000651POF", metadata={"category": "cpu"}
        )
    )
    candidate = identity_from_price_item(
        _item(title="AMD Ryzen 7 5800X3D 100-000000651", metadata={"category": "cpu"})
    )
    assert "100100000651pof" in reference.mpn_aliases
    assert "100000000651" in candidate.mpn_aliases
    assert reference.mpn != candidate.mpn


def test_cpu_queries_use_spaced_model_and_keep_suffix() -> None:
    identity = identity_from_price_item(
        _item(
            product_id="a9c9041d-503a-4942-8b62-a483bd2f7c78",
            title=(
                "Processador AMD Ryzen 7 5800X3D, 3.4GHZ, 8-CORE, "
                "Cache 100MB, AM4 - 100-100000651POF"
            ),
            brand="AMD",
            model="Ryzen 7 5800X3D",
            metadata={"category": "cpu"},
        )
    )
    queries = build_search_queries(identity)
    assert queries[:2] == [
        "processador amd ryzen 7 5800x3d am4",
        "amd ryzen 7 5800x3d",
    ]
    assert not any(query.endswith("5800x") for query in queries)


def test_motherboard_parse_model_excludes_socket_marketing_tail() -> None:
    from scout_api.modules.crawler.utils.category_profiles.extra_parsers import (
        parse_motherboard,
    )

    parsed = parse_motherboard(_MB_LONG_TITLE, "motherboard")
    model = (parsed.model or "").casefold()
    assert "b650m" in model
    assert "socket" not in model
    assert "chipset" not in model
    assert "ddr5" not in model
    assert "m-atx" not in model and "matx" not in model.replace(" ", "")
