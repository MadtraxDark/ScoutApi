"""Registration must not create a second CanonicalProduct for a known product.

Fixtures are isolated test data (titles reused from matching regressions).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.matching.canonical_resolver import (
    ProductIdentityReviewRequired,
    identity_lock_key,
    request_identity,
)
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferSnapshot,
    ProductIdentifier,
    StoreListing,
)
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.schemas import ProductRegisterRequest

_OWNER = AuthenticatedPrincipal(
    id=UUID("11111111-1111-4111-8111-111111111111"),
    role=UserRole.USER,
    display_name="tester",
)
_OTHER = AuthenticatedPrincipal(
    id=UUID("22222222-2222-4222-8222-222222222222"),
    role=UserRole.USER,
    display_name="other",
)

_GTIN = "7891991010863"


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _visaovip(**overrides: object) -> ProductRegisterRequest:
    data: dict[str, object] = {
        "title": "Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7",
        "brand": "MSI",
        "store": "visaovip",
        "country": "PY",
        "product_id": "55359",
        "canonical_url": "https://www.visaovip.com/produto/55359",
        "price": "650.00",
        "currency": "USD",
    }
    data.update(overrides)
    return ProductRegisterRequest.model_validate(data)


def _kabum(**overrides: object) -> ProductRegisterRequest:
    data: dict[str, object] = {
        "title": (
            "MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7 "
            "2557 MHz 192-bit FP4 and DLSS 4 Ray Tracing G5070-12S3C"
        ),
        "brand": "MSI",
        "store": "kabum",
        "country": "BR",
        "product_id": "777166",
        "canonical_url": "https://www.kabum.com.br/produto/777166",
        "price": "4299.90",
    }
    data.update(overrides)
    return ProductRegisterRequest.model_validate(data)


def test_same_listing_twice_is_existing_listing_without_side_effects(
    session: Session,
) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_visaovip(), owner=_OWNER)
    session.commit()
    snapshots = _count(session, OfferSnapshot)

    second = service.register(_visaovip(price="999.00"), owner=_OWNER)
    session.commit()

    assert first.resolution == "new_product"
    assert second.resolution == "existing_listing"
    assert second.matched_by == "store_identity"
    assert second.created is False and second.listing_created is False
    assert second.product.id == first.product.id
    assert _count(session, CanonicalProduct) == 1
    assert _count(session, StoreListing) == 1
    assert _count(session, OfferSnapshot) == snapshots


def test_other_store_without_gtin_becomes_new_offer(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_visaovip(), owner=_OWNER)
    session.commit()

    second = service.register(_kabum(), owner=_OWNER)
    session.commit()

    assert second.resolution == "existing_product_new_listing"
    assert second.matched_by == "identity"
    assert second.created is False
    assert second.listing_created is True
    assert second.product.id == first.product.id
    assert second.match_confidence is not None
    assert _count(session, CanonicalProduct) == 1
    assert {listing.store for listing in second.product.listings} == {
        "visaovip",
        "kabum",
    }


def test_late_gtin_is_attached_to_existing_canonical(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_visaovip(), owner=_OWNER)
    session.commit()

    second = service.register(_kabum(gtin=_GTIN), owner=_OWNER)
    session.commit()
    third = service.register(
        _kabum(
            store="pichau",
            product_id="p-1",
            canonical_url="https://www.pichau.com.br/p-1",
            title="Placa de video generica",
            brand=None,
            gtin=_GTIN,
        ),
        owner=_OWNER,
    )
    session.commit()

    assert second.product.id == first.product.id
    assert second.product.gtins == [_GTIN]
    assert third.resolution == "existing_product_new_listing"
    assert third.matched_by == "gtin"
    assert third.product.id == first.product.id
    assert _count(session, CanonicalProduct) == 1
    assert _count(session, ProductIdentifier) == 1


def test_reimport_same_listing_learns_gtin(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_visaovip(), owner=_OWNER)
    session.commit()
    again = service.register(_visaovip(gtin=_GTIN), owner=_OWNER)
    session.commit()
    assert again.resolution == "existing_listing"
    assert again.product.id == first.product.id
    assert again.product.gtins == [_GTIN]


def test_close_family_is_a_different_product(session: Session) -> None:
    service = ProductRegistrationService(session)
    service.register(_visaovip(), owner=_OWNER)
    session.commit()

    ti = service.register(
        _kabum(
            title="MSI Shadow 3X OC 16GB GeForce RTX 5070 Ti GDDR7",
            product_id="888",
            canonical_url="https://www.kabum.com.br/produto/888",
        ),
        owner=_OWNER,
    )
    session.commit()

    assert ti.resolution == "new_product"
    assert ti.created is True
    assert _count(session, CanonicalProduct) == 2


def test_same_store_other_listing_requires_review(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_kabum(), owner=_OWNER)
    session.commit()

    with pytest.raises(ProductIdentityReviewRequired) as exc:
        service.register(
            _kabum(
                product_id="777999",
                canonical_url="https://www.kabum.com.br/produto/777999",
            ),
            owner=_OWNER,
        )
    session.rollback()
    assert exc.value.code == "PRODUCT_IDENTITY_REVIEW_REQUIRED"
    assert exc.value.candidate.id == first.product.id
    assert _count(session, CanonicalProduct) == 1
    assert _count(session, StoreListing) == 1


def test_review_decisions_attach_or_create(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_kabum(), owner=_OWNER)
    session.commit()
    other_listing = {
        "product_id": "777999",
        "canonical_url": "https://www.kabum.com.br/produto/777999",
    }

    attached = service.register(
        _kabum(**other_listing, target_product_id=str(first.product.id)),
        owner=_OWNER,
    )
    session.commit()
    assert attached.resolution == "existing_product_new_listing"
    assert attached.matched_by == "user_confirmed"
    assert attached.product.id == first.product.id

    created = service.register(
        _kabum(
            product_id="778000",
            canonical_url="https://www.kabum.com.br/produto/778000",
            create_new_product=True,
        ),
        owner=_OWNER,
    )
    session.commit()
    assert created.resolution == "new_product"
    assert created.product.id != first.product.id
    assert _count(session, CanonicalProduct) == 2


def test_target_product_conflicting_with_gtin_is_rejected(session: Session) -> None:
    service = ProductRegistrationService(session)
    gtin_owner = service.register(_visaovip(gtin=_GTIN), owner=_OWNER)
    other = service.register(
        _kabum(
            title="Mouse Logitech G Pro X Superlight 2",
            brand="Logitech",
            product_id="1",
            canonical_url="https://www.kabum.com.br/produto/1",
        ),
        owner=_OWNER,
    )
    session.commit()
    assert gtin_owner.product.id != other.product.id

    with pytest.raises(RequestError) as exc:
        service.register(
            _kabum(gtin=_GTIN, target_product_id=str(other.product.id)),
            owner=_OWNER,
        )
    assert exc.value.code == "PRODUCT_IDENTITY_CONFLICT"


def test_private_product_of_other_user_is_never_matched(session: Session) -> None:
    service = ProductRegistrationService(session)
    private = service.register(_visaovip(), owner=_OWNER)
    session.commit()

    resolved = service.resolve(_kabum(), viewer=_OTHER)
    assert resolved.resolution == "new_product"
    assert resolved.product is None

    mine = service.register(_kabum(), owner=_OTHER)
    session.commit()
    assert mine.product.id != private.product.id


def test_resolve_is_read_only(session: Session) -> None:
    service = ProductRegistrationService(session)
    first = service.register(_visaovip(), owner=_OWNER)
    session.commit()
    before = (
        _count(session, CanonicalProduct),
        _count(session, StoreListing),
        _count(session, OfferSnapshot),
    )

    same = service.resolve(_visaovip(), viewer=_OWNER)
    other_store = service.resolve(_kabum(gtin=_GTIN), viewer=_OWNER)
    new = service.resolve(
        _kabum(
            title="Mouse Logitech G Pro X Superlight 2",
            brand="Logitech",
            product_id="1",
            canonical_url="https://www.kabum.com.br/produto/1",
        ),
        viewer=_OWNER,
    )

    assert not session.new and not session.dirty
    session.commit()
    assert (
        _count(session, CanonicalProduct),
        _count(session, StoreListing),
        _count(session, OfferSnapshot),
    ) == before
    assert _count(session, ProductIdentifier) == 0
    assert same.resolution == "existing_listing"
    assert same.listing is not None
    assert other_store.resolution == "existing_product_new_listing"
    assert other_store.product is not None
    assert other_store.product.id == first.product.id
    assert new.resolution == "new_product"


def test_lock_key_is_shared_by_equivalent_titles() -> None:
    a = request_identity(_visaovip(), gtin=None, category=None)
    b = request_identity(_kabum(), gtin=_GTIN, category=None)
    key = identity_lock_key(a, None)
    assert key is not None
    assert key == identity_lock_key(b, _GTIN)


def _http_client(session: Session):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from scout_api.main import app
    from scout_api.modules.auth.deps import require_authenticated_user
    from scout_api.modules.matching.router import get_registration_service

    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_registration_service] = lambda: (
        ProductRegistrationService(session)
    )
    app.dependency_overrides[require_authenticated_user] = lambda: _OWNER
    return TestClient(app), app, overrides


def test_http_review_conflict_returns_existing_product(session: Session) -> None:
    client, app, overrides = _http_client(session)
    try:
        body = _kabum().model_dump(mode="json", exclude_none=True)
        assert client.post("/products", json=body).status_code == 200
        body.update(
            product_id="777999",
            canonical_url="https://www.kabum.com.br/produto/777999",
        )
        resolved = client.post("/products/resolve", json=body)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolution"] == "needs_review"

        conflict = client.post("/products", json=body)
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()["detail"]
        assert detail["code"] == "PRODUCT_IDENTITY_REVIEW_REQUIRED"
        assert detail["existing_product"]["id"] == resolved.json()["product"]["id"]
        assert detail["existing_product"]["title"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
    assert _count(session, CanonicalProduct) == 1


def test_structured_edition_keeps_gpu_lines_apart(session: Session) -> None:
    """Short titles must not erase a known edition (Dual OC vs Prime OC)."""
    service = ProductRegistrationService(session)
    for index, edition in enumerate(("Dual OC Edition", "Prime OC")):
        response = service.register(
            ProductRegisterRequest(
                title=f"ASUS {edition}",
                brand="Asus",
                model="GeForce RTX 5070",
                variant=edition,
                attributes={"edition": edition},
                store="pichau" if index else "kabum",
                country="BR",
                product_id=str(index + 1),
                canonical_url=f"https://loja.example/produto/{index + 1}",
            ),
            owner=_OWNER,
        )
        session.commit()
        assert response.resolution == "new_product"
    assert _count(session, CanonicalProduct) == 2
