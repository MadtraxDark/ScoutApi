"""Idempotent registration of an already known store listing.

The MSI A520M-A PRO listing below is isolated TEST DATA only (it reproduces a
duplicate-registration report); nothing here touches a real database.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.fingerprints import canonicalize_url
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.models import CanonicalProduct, StoreListing
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import ProductRegisterRequest

_OWNER = AuthenticatedPrincipal(
    id=UUID("11111111-1111-4111-8111-111111111111"),
    role=UserRole.USER,
    display_name="tester",
)

_TITLE = "Placa-Mae MSI A520M-A PRO, AMD AM4, mATX, DDR4, Preto"
_CANONICAL = (
    "https://www.kabum.com.br/produto/280890/"
    "placa-mae-msi-a520m-a-pro-amd-am4-matx-ddr4-preto-a520m-a-pro"
)
_TRACKED = f"{_CANONICAL}?gclsrc=aw.ds&utm_id=22429436060"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _msi(**overrides: object) -> ProductRegisterRequest:
    data: dict[str, object] = {
        "title": _TITLE,
        "brand": "MSI",
        "store": "kabum",
        "country": "BR",
        "product_id": "280890",
        "canonical_url": _CANONICAL,
        "price": "449.90",
    }
    data.update(overrides)
    return ProductRegisterRequest.model_validate(data)


def _seed(session: Session) -> UUID:
    first = ProductRegistrationService(session).register_saved(_msi(), owner=_OWNER)
    assert first.created is True and first.listing_created is True
    return first.product.id


def _assert_no_new_rows(session: Session) -> None:
    assert _count(session, CanonicalProduct) == 1
    assert _count(session, StoreListing) == 1


def test_canonicalize_strips_extended_tracking_keys() -> None:
    assert canonicalize_url(_TRACKED) == _CANONICAL
    url = "https://x.example/p/1?DCLID=a&msclkid=b&mc_cid=c&mc_eid=d&igshid=e&UTM_x=1"
    assert canonicalize_url(url) == "https://x.example/p/1"
    # Product-identifying parameters are kept.
    kept = canonicalize_url("https://x.example/p/1?sku_id=99&utm_id=1")
    assert kept == "https://x.example/p/1?sku_id=99"


def test_register_with_tracked_original_url_is_existing_listing(
    session: Session,
) -> None:
    product_id = _seed(session)
    again = ProductRegistrationService(session).register_saved(
        _msi(canonical_url=_TRACKED, url=_TRACKED), owner=_OWNER
    )
    assert again.resolution == "existing_listing"
    assert again.created is False and again.listing_created is False
    assert again.product.id == product_id
    _assert_no_new_rows(session)


def test_resolve_with_tracked_original_url_is_existing_listing(
    session: Session,
) -> None:
    product_id = _seed(session)
    resolved = ProductRegistrationService(session).resolve(
        _msi(canonical_url=_TRACKED), viewer=_OWNER
    )
    assert resolved.resolution == "existing_listing"
    assert resolved.product is not None and resolved.product.id == product_id
    _assert_no_new_rows(session)


def test_url_alone_finds_listing_even_with_other_product_id(
    session: Session,
) -> None:
    """The tracked URL (tracking stripped) finds the listing by URL key."""
    _seed(session)
    again = ProductRegistrationService(session).register_saved(
        _msi(canonical_url=_TRACKED, product_id="999999"), owner=_OWNER
    )
    assert again.resolution == "existing_listing"
    _assert_no_new_rows(session)


def test_same_url_without_tracking_is_existing_listing(session: Session) -> None:
    _seed(session)
    again = ProductRegistrationService(session).register_saved(_msi(), owner=_OWNER)
    assert again.resolution == "existing_listing"
    assert again.created is False
    _assert_no_new_rows(session)


@pytest.mark.parametrize(
    "variant",
    [
        _CANONICAL.replace("www.kabum.com.br", "WWW.KABUM.COM.BR"),
        _CANONICAL + "/",
        _CANONICAL + "#specs",
        _CANONICAL + "?utm_source=a&gclsrc=aw.ds",
        _CANONICAL + "?utm_id=1&utm_source=a#x",
    ],
)
def test_irrelevant_url_variations_are_existing_listing(
    session: Session, variant: str
) -> None:
    _seed(session)
    # Different product_id so that only the URL key can match.
    again = ProductRegistrationService(session).register_saved(
        _msi(canonical_url=variant, url=variant, product_id="999999"),
        owner=_OWNER,
    )
    assert again.resolution == "existing_listing"
    assert again.created is False and again.listing_created is False
    _assert_no_new_rows(session)


def test_query_order_variation_is_existing_listing(session: Session) -> None:
    base = _CANONICAL + "?a=1&b=2"
    ProductRegistrationService(session).register_saved(
        _msi(canonical_url=base, url=base), owner=_OWNER
    )
    swapped = _CANONICAL + "?b=2&a=1&utm_id=7"
    # The store + country + product_id key resolves the same listing.
    again = ProductRegistrationService(session).register_saved(
        _msi(canonical_url=swapped, url=swapped), owner=_OWNER
    )
    assert again.resolution == "existing_listing"
    _assert_no_new_rows(session)


def test_legacy_raw_url_listing_is_found(session: Session) -> None:
    """A listing stored with a raw (tracked) URL is still found."""
    ProductRegistrationService(session).register_saved(_msi(), owner=_OWNER)
    listing = session.scalars(select(StoreListing)).one()
    listing.canonical_url = _TRACKED
    session.commit()
    repo = MatchingRepository(session)
    assert repo.find_listing_by_url("kabum", _TRACKED) is not None
    again = ProductRegistrationService(session).register_saved(
        _msi(canonical_url=_TRACKED), owner=_OWNER
    )
    assert again.resolution == "existing_listing"
    _assert_no_new_rows(session)


def test_genuinely_new_product_is_still_created(session: Session) -> None:
    _seed(session)
    other = ProductRegistrationService(session).register_saved(
        ProductRegisterRequest.model_validate(
            {
                "title": "Processador AMD Ryzen 5 5600 3.5GHz AM4",
                "brand": "AMD",
                "model": "Ryzen 5 5600",
                "store": "kabum",
                "country": "BR",
                "product_id": "111111",
                "canonical_url": "https://www.kabum.com.br/produto/111111/r5-5600",
            }
        ),
        owner=_OWNER,
    )
    assert other.resolution == "new_product"
    assert other.created is True and other.listing_created is True
    assert _count(session, CanonicalProduct) == 2
    assert _count(session, StoreListing) == 2


def test_integrity_race_replays_as_idempotent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A unique violation lost to a concurrent winner becomes created=false."""
    product_id = _seed(session)
    calls = {"n": 0}

    def racing_register(self, request, *, owner):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("unique"))

    monkeypatch.setattr(ProductRegistrationService, "register", racing_register)
    response = ProductRegistrationService(session).register_saved(_msi(), owner=_OWNER)
    assert calls["n"] == 1
    assert response.created is False and response.listing_created is False
    assert response.resolution == "existing_listing"
    assert response.product.id == product_id
    _assert_no_new_rows(session)


def test_integrity_error_without_winner_stays_an_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_register(self, request, *, owner):  # type: ignore[no-untyped-def]
        raise IntegrityError("INSERT", {}, Exception("unique"))

    monkeypatch.setattr(ProductRegistrationService, "register", failing_register)
    with pytest.raises(IntegrityError):
        ProductRegistrationService(session).register_saved(_msi(), owner=_OWNER)
    assert _count(session, StoreListing) == 0


def _client(session: Session | None):  # type: ignore[no-untyped-def]
    from scout_api.main import app
    from scout_api.modules.auth.deps import require_authenticated_user
    from scout_api.modules.matching.router import get_registration_service

    saved = dict(app.dependency_overrides)
    if session is not None:
        app.dependency_overrides[get_registration_service] = lambda: (
            ProductRegistrationService(session)
        )
    app.dependency_overrides[require_authenticated_user] = lambda: _OWNER
    return TestClient(app), app, saved


def test_http_post_products_duplicate_is_idempotent(session: Session) -> None:
    client, app, saved = _client(session)
    try:
        body = _msi().model_dump(mode="json", exclude_none=True)
        first = client.post("/products", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True

        body["canonical_url"] = _TRACKED
        body["url"] = _TRACKED
        second = client.post("/products", json=body)
        assert second.status_code == 200, second.text
        data = second.json()
        assert data["created"] is False and data["listing_created"] is False
        assert data["resolution"] == "existing_listing"
        assert data["product"]["id"] == first.json()["product"]["id"]

        resolved = client.post("/products/resolve", json=body)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolution"] == "existing_listing"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    _assert_no_new_rows(session)


def test_http_without_database_url_is_503(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout_api.core.config import Settings
    from scout_api.modules.matching import router as matching_router

    monkeypatch.setattr(
        matching_router, "get_settings", lambda: Settings(database_url=None)
    )
    client, app, saved = _client(None)
    try:
        body = _msi().model_dump(mode="json", exclude_none=True)
        for path in ("/products", "/products/resolve"):
            response = client.post(path, json=body)
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    assert _count(session, StoreListing) == 0


def test_http_operational_error_is_503_and_writes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self, **kwargs):  # type: ignore[no-untyped-def]
        raise OperationalError("SELECT", {}, Exception("connection lost"))

    monkeypatch.setattr(MatchingRepository, "find_listing_by_store_identity", broken)
    client, app, saved = _client(session)
    try:
        body = _msi().model_dump(mode="json", exclude_none=True)
        response = client.post("/products", json=body)
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "DATABASE_UNAVAILABLE"
        assert detail["retryable"] is True
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    session.rollback()
    assert _count(session, CanonicalProduct) == 0
    assert _count(session, StoreListing) == 0


def test_other_product_of_same_store_remains_registrable_over_http(
    session: Session,
) -> None:
    client, app, saved = _client(session)
    try:
        first = client.post(
            "/products", json=_msi().model_dump(mode="json", exclude_none=True)
        )
        assert first.status_code == 200, first.text
        other = {
            "title": "Memoria Kingston Fury Beast 16GB DDR4 3200MHz",
            "brand": "Kingston",
            "model": "Fury Beast",
            "store": "kabum",
            "country": "BR",
            "product_id": "222222",
            "canonical_url": "https://www.kabum.com.br/produto/222222/fury-beast",
        }
        response = client.post("/products", json=other)
        assert response.status_code == 200, response.text
        assert response.json()["created"] is True
        assert response.json()["listing_created"] is True
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    assert _count(session, CanonicalProduct) == 2
    assert _count(session, StoreListing) == 2
