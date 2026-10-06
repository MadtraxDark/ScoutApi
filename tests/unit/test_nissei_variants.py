"""Nissei Magento selected-variant / Product Match identity tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from scrapy.http import HtmlResponse, Request

from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.crawler.spiders.paraguay.nissei import NisseiSpider
from scout_api.modules.crawler.utils.product_attributes import (
    SOURCE_SELECTED_VARIANT,
    SOURCE_SPECIFICATIONS,
    SOURCE_URL_SLUG,
    resolve_product_identity,
)
from scout_api.modules.matching.identity import (
    build_search_queries,
    critical_identity_conflict,
    identity_from_price_item,
    identity_reference_item,
)
from scout_api.modules.matching.product_match_service import _serp_title_reject_reason
from scout_api.modules.matching.search_adapters.paraguay.nissei import (
    NisseiSearchAdapter,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nissei"


def _response(name: str, url: str) -> HtmlResponse:
    body = (FIXTURES / name).read_bytes()
    return HtmlResponse(url, body=body, encoding="utf-8", request=Request(url))


def test_nissei_sole_options_without_storage_in_url() -> None:
    url = "https://nissei.com/br/phone-family-base"
    item = NisseiSpider().parse_product(
        _response("configurable_sole_options_no_slug.html", url)
    )

    assert item.canonical_url == url
    assert item.sku == "SKU-256-BLACK"
    assert item.product_id == "SKU-256-BLACK"
    assert item.price == Decimal("799")
    assert item.currency == "USD"
    assert item.metadata["variant"]["storage"] == "256 GB"
    assert item.metadata["variant"]["color"] == "Black Titanium"
    assert item.metadata["source"]["storage"] == SOURCE_SELECTED_VARIANT
    assert item.metadata["source"]["color"] == SOURCE_SELECTED_VARIANT
    assert item.metadata["parent_product_id"] == "100001"
    assert item.metadata["variant_product_id"] == "200001"

    identity = identity_from_price_item(item)
    assert identity.variant_attrs["storage"] == "256gb"
    assert "black" in identity.variant_attrs["color"]
    queries = build_search_queries(identity)
    assert any("256" in q.lower() for q in queries)


def test_nissei_simple_product_with_storage_in_url_and_specs() -> None:
    url = "https://nissei.com/br/phone-family-base-256-gb-black-titanium-1"
    item = NisseiSpider().parse_product(_response("simple_slug_with_storage.html", url))

    assert item.sku == "136695"
    assert item.metadata["variant"]["storage"] == "256 GB"
    assert item.metadata["variant"]["color"] == "Black Titanium"
    assert item.metadata["source"]["storage"] == SOURCE_SPECIFICATIONS
    assert item.metadata["source"]["color"] == SOURCE_SPECIFICATIONS
    assert item.gtin == "123456789012"


def test_nissei_selected_variant_wins_over_url_slug() -> None:
    url = "https://nissei.com/br/phone-family-base-256-gb-black-titanium-1"
    item = NisseiSpider().parse_product(
        _response("structured_wins_over_slug.html", url)
    )

    assert item.metadata["variant"]["storage"] == "512 GB"
    assert item.metadata["variant"]["color"] == "Gray Titanium"
    assert item.metadata["source"]["storage"] == SOURCE_SELECTED_VARIANT
    assert item.sku == "SKU-512-GRAY"
    assert item.price == Decimal("900")
    conflicts = item.metadata.get("variant_conflicts") or []
    assert any(c.get("attribute") == "storage" for c in conflicts)


def test_nissei_variant_consistency_selected_child() -> None:
    url = "https://nissei.com/br/phone-family-base"
    item = NisseiSpider().parse_product(
        _response("variant_consistency_selected.html", url)
    )

    assert item.metadata["variant"]["storage"] == "256 GB"
    assert item.metadata["variant"]["color"] == "Black"
    assert item.sku == "SKU-256-BLACK"
    assert item.price == Decimal("799")
    assert item.metadata["variant_product_id"] == "200256"


def test_nissei_unselected_multi_options_leave_storage_missing() -> None:
    url = "https://nissei.com/br/phone-family-base"
    item = NisseiSpider().parse_product(
        _response("configurable_unselected_multi.html", url)
    )

    # Must NOT pick the first of many options.
    assert item.metadata.get("selected_variant") in ({}, None)
    assert "storage" not in (item.metadata.get("variant") or {})
    assert item.sku == "PC-100"
    assert item.price == Decimal("700")


def test_selected_variant_source_priority_over_url() -> None:
    bundle = resolve_product_identity(
        selected_variant={"storage": "512 GB", "color": "Gray"},
        url_evidence={"storage": "256 GB", "color": "Black"},
        title="Phone 256 GB Black",
    )
    assert bundle.value("storage") == "512 GB"
    assert bundle.get("storage").source == SOURCE_SELECTED_VARIANT
    assert bundle.value("color") == "Gray"


def test_url_slug_is_supporting_evidence_only() -> None:
    bundle = resolve_product_identity(
        url_evidence={"storage": "256 GB", "color": "Black Titanium"},
        title="Acme Phone X Dual",
    )
    assert bundle.value("storage") == "256 GB"
    assert bundle.get("storage").source == SOURCE_URL_SLUG


def test_galaxy_a_series_rejects_against_s_series_on_serp() -> None:
    reference = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item(
                    "Samsung Galaxy S25 Ultra 256GB Black Titanium"
                ).model_dump(),
                "brand": "Samsung",
                "model": "Galaxy S25 Ultra",
                "variant": "storage: 256 GB",
                "metadata": {"variant": {"storage": "256 GB"}},
            }
        )
    )
    assert (
        _serp_title_reject_reason(
            reference,
            title="Samsung Galaxy A56 SM-A566B/DS 5G Dual 256 GB - Grafito",
        )
        is not None
    )
    assert (
        _serp_title_reject_reason(
            reference,
            title="Samsung Galaxy S25 Ultra SM-S938BZ/DS 5G Dual",
        )
        is None
    )

    reference = identity_from_price_item(
        identity_reference_item("Samsung Galaxy S25 Ultra 256GB Black Titanium")
    )
    # Force storage on reference the way a full PDP would.
    reference = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item(
                    "Samsung Galaxy S25 Ultra 256GB Black Titanium"
                ).model_dump(),
                "metadata": {
                    "variant": {"storage": "256 GB", "color": "Black Titanium"},
                    "specifications": {
                        "Memoria Interna": "256 GB",
                        "Cor": "Black Titanium",
                    },
                },
                "variant": "color: Black Titanium; storage: 256 GB",
            }
        )
    )
    assert reference.variant_attrs.get("storage") == "256gb"
    reason = _serp_title_reject_reason(reference, title="Samsung Galaxy S25 Ultra")
    assert reason is None


def test_storage_mismatch_rejects_after_full_identity() -> None:
    reference = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item("Acme Phone X 256GB Black").model_dump(),
                "brand": "Acme",
                "model": "Phone X",
                "variant": "color: Black; storage: 256 GB",
                "metadata": {"variant": {"storage": "256 GB", "color": "Black"}},
            }
        )
    )
    candidate = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item("Acme Phone X 512GB Black").model_dump(),
                "brand": "Acme",
                "model": "Phone X",
                "variant": "color: Black; storage: 512 GB",
                "metadata": {"variant": {"storage": "512 GB", "color": "Black"}},
            }
        )
    )
    conflict = critical_identity_conflict(reference, candidate)
    assert conflict is not None
    assert "storage_mismatch" in conflict


def test_missing_storage_is_not_conflict() -> None:
    reference = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item("Acme Phone X 256GB").model_dump(),
                "variant": "storage: 256 GB",
                "metadata": {"variant": {"storage": "256 GB"}},
            }
        )
    )
    candidate = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item("Acme Phone X").model_dump(),
                "variant": None,
                "metadata": {},
            }
        )
    )
    assert reference.variant_attrs.get("storage")
    assert not candidate.variant_attrs.get("storage")
    conflict = critical_identity_conflict(reference, candidate)
    assert conflict is None or "storage_mismatch" not in conflict


def test_nissei_search_keeps_family_candidate_without_storage_in_title() -> None:
    """SERP family titles remain candidates; full PDP resolves storage later."""
    body = b"""
    <ol class="products">
      <li class="product-item">
        <a class="product-item-link" href="/br/acme-phone-x-dual">Acme Phone X Dual</a>
      </li>
    </ol>
    """
    url = "https://nissei.com/py/catalogsearch/result/?q=acme+phone"
    response = HtmlResponse(url, body=body, encoding="utf-8", request=Request(url))
    hits = NisseiSearchAdapter().parse_candidates(response)
    assert len(hits) == 1
    assert "256" not in (hits[0].title or "")
    reference = identity_from_price_item(
        ProductPriceItem.model_validate(
            {
                **identity_reference_item("Acme Phone X 256GB").model_dump(),
                "variant": "storage: 256 GB",
                "metadata": {"variant": {"storage": "256 GB"}},
            }
        )
    )
    assert _serp_title_reject_reason(reference, title=hits[0].title) is None


def test_existing_nissei_installment_fixture_still_parses() -> None:
    body = (
        b'\n    <main id="maincontent">\n      <title>Pl'
        b"aca Madre Gigabyte X870 Aorus Stealth ICE AM"
        b'5 DDR5 ATX</title>\n      <div class="product'
        b'-info-main">\n        <h1><span class="base">'
        b"Placa Madre Gigabyte X870 Aorus Stealth ICE "
        b'AM5 DDR5 ATX</span></h1>\n        <a class="a'
        b'mshopby-brand-title-link">GIGABYTE</a>\n     '
        b'   <div class="price-box" data-role="priceBo'
        b'x" data-product-id="1644544">\n          <spa'
        b'n class="price-wrapper" data-price-amount="3'
        b'226999.996001"\n                data-price-ty'
        b'pe="finalPrice">\n            <span class="pr'
        b'ice">Gs. 3.227.000</span>\n          </span>\n'
        b'          <meta itemprop="price" content="32'
        b'26999.996001">\n        </div>\n        <div c'
        b'lass="stock available"><span>En stock</span>'
        b'</div>\n        <div class="bancos-adheridos '
        b'principal-cuotas">\n          <h3>Hasta <span'
        b">18</span> cuotas\n            <span>sin inte"
        b"reses de Gs. 179.278</span></h3>\n        </d"
        b'iv>\n      </div>\n      <table id="product-at'
        b'tribute-specs-table">\n        <tr><th>UPC</t'
        b"h><td>889523051276</td></tr>\n      </table>\n"
        b'      <form data-product-sku="148321"></form'
        b">\n    </main>\n    "
    )
    url = "https://nissei.com/py/informatica/producto"
    item = NisseiSpider().parse_product(
        HtmlResponse(url, body=body, encoding="utf-8", request=Request(url))
    )
    assert item.gtin == "889523051276"
    assert item.installment_price == Decimal("179278")
    assert item.installment_count == 18
