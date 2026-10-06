"""Amazon match helpers: ASIN, SERP shell, display names, condition prefilter."""

from __future__ import annotations

from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.crawler.spiders.amazon.marketplace import AMAZON_BR
from scout_api.modules.crawler.spiders.amazon.parsing import prepare_amazon_fetch_url
from scout_api.modules.crawler.stores import store_display_name
from scout_api.modules.matching.identity import (
    build_search_queries,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import _serp_title_reject_reason


def test_store_display_names_are_friendly() -> None:
    assert store_display_name("amazon_br") == "Amazon Brasil"
    assert store_display_name("amazon_us") == "Amazon US"
    assert store_display_name("shoppingchina") == "Shopping China"
    assert store_display_name("visaovip") == "Visão VIP"
    assert "_" not in store_display_name("amazon_br")
    assert "_" not in store_display_name("amazon_us")


def test_amazon_tracking_url_canonicalizes_to_dp_asin() -> None:
    noisy = (
        "https://www.amazon.com.br/Celular-Samsung-Galaxy-Titânio/dp/B0DSYJCY45/"
        "ref=asc_df_B0DSYJCY45?tag=googleshopp00-20&linkCode=df0&th=1&language=pt_BR"
    )
    prepared = prepare_amazon_fetch_url(noisy, AMAZON_BR.host)
    assert "/dp/B0DSYJCY45" in prepared
    assert "tag=" not in prepared
    assert "hvadid=" not in prepared
    canon = canonicalize_url(prepared)
    assert "B0DSYJCY45" in canon.upper()


def test_long_smartphone_title_builds_identity_queries_not_raw_title() -> None:
    title = (
        "Celular Samsung Galaxy S25 Ultra 5G 256GB Galaxy AI Titânio Preto "
        '6,9" 12GB RAM Câm. Quádrupla 200+50+10+50MP Bateria 5000mAh Dual Chip'
    )
    identity = identity_from_price_item(
        identity_reference_item(title, brand="Samsung", category="smartphone")
    )
    queries = build_search_queries(identity)
    # Title-first retrieval preserves explicit category; family remains fallback.
    assert queries[0] == "celular samsung galaxy s25 ultra 5g 256gb titanio preto"
    assert "samsung galaxy s25 ultra" in queries
    assert "samsung galaxy s25 ultra 256gb" in queries
    assert title not in queries
    assert any("titanium black" in q or "black" in q for q in queries)


def test_generic_alphanumeric_model_is_kept_in_search_queries() -> None:
    title = 'Monitor Gamer ASUS TUF 24.5", Full HD, 200Hz, Fast IPS, Preto - VG259Q5A'
    identity = identity_from_price_item(
        identity_reference_item(
            title,
            brand="ASUS",
            model="VG259Q5A",
            category="monitor",
        )
    )

    queries = build_search_queries(identity)

    assert queries[0] == "monitor gamer asus tuf full hd fast ips preto vg259q5a"
    assert "asus vg259q5a" in queries[:3]
    assert "vg259q5a" in queries
    assert "asus preto" not in queries[:2]
    assert title not in queries


def test_localized_color_query_follows_search_integration_locale() -> None:
    identity = identity_from_price_item(
        identity_reference_item(
            "Apple iPhone 15 128GB Rosa",
            brand="Apple",
            model="iPhone 15",
            category="smartphone",
            variant="Rosa",
        )
    )

    br_queries = build_search_queries(identity, locale="pt-BR")
    us_queries = build_search_queries(identity, locale="en-US")
    pink_us = next(query for query in us_queries if " pink" in query)

    assert any("rosa" in query for query in br_queries)
    assert pink_us == "apple iphone 15 128gb pink"
    assert not any("rosa" in query for query in us_queries[:5])


def test_store_query_locales_are_market_metadata_and_keep_identifiers() -> None:
    from scout_api.modules.crawler.stores import STORE_CONFIGS

    assert STORE_CONFIGS["bestbuy"].query_locale == "en-US"
    assert STORE_CONFIGS["amazon_us"].query_locale == "en-US"
    assert STORE_CONFIGS["amazon_br"].query_locale == "pt-BR"

    identity = identity_from_price_item(
        identity_reference_item(
            "AMD Ryzen 7 5800X3D VG259Q5A A3256 MG7L4LL/A Rosa",
            brand="AMD",
            model="5800X3D VG259Q5A A3256 MG7L4LL/A",
            category="cpu",
            variant="Rosa",
        )
    )
    queries = build_search_queries(identity, locale="en-US")
    joined = " ".join(queries).lower()
    for identifier in ("5800x3d", "vg259q5a", "a3256", "mg7l4ll/a"):
        assert identifier in joined


def test_serp_prefilter_rejects_renewed_when_reference_is_new() -> None:
    ref = identity_from_price_item(
        identity_reference_item(
            "Samsung Galaxy S25 Ultra 256GB Titanium Black",
            brand="Samsung",
            category="smartphone",
        )
    )
    reason = _serp_title_reject_reason(
        ref,
        title="Samsung Galaxy S25 Ultra, 256GB, Titanium Black - Unlocked (Renewed)",
    )
    assert reason is not None
    assert "condition" in reason


def test_asin_length_gate_in_search_parser() -> None:
    from scrapy.http import HtmlResponse

    from scout_api.modules.matching.search_adapters.amazon.parse import (
        parse_amazon_search_results,
    )

    html = (
        '<div data-component-type="s-search-result" data-asin="B0DSYJCY45">'
        "<h2><a href='/dp/B0DSYJCY45'><span>Celular S25 Ultra 256GB</span></a></h2>"
        "</div>"
        '<div data-component-type="s-search-result" data-asin="SHORT">'
        "<h2><a href='/dp/SHORT'><span>Bad</span></a></h2></div>"
    )
    response = HtmlResponse(
        url="https://www.amazon.com.br/s?k=test",
        body=html.encode("utf-8"),
        encoding="utf-8",
    )
    cands = parse_amazon_search_results(response, host="amazon.com.br", source="t")
    assert len(cands) == 1
    assert cands[0].product_id == "B0DSYJCY45"
    assert "S25" in (cands[0].title or "")


def test_market_isolation_display_names() -> None:
    assert store_display_name("amazon_br") != store_display_name("amazon_us")
