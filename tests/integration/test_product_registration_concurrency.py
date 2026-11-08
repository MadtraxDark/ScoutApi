"""Concurrent registration of the same product (no GTIN) on PostgreSQL.

Requires a disposable database: ``TEST_DATABASE_URL`` plus
``ALLOW_SUPABASE_PERSISTENCE_TEST=1``. Never falls back to ``DATABASE_URL``.
Fixtures are isolated test data.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from scout_api.core.config import Settings, get_settings
from scout_api.core.database import create_db_engine, reset_database_cache
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.models import (
    CanonicalProduct,
    OfferEvent,
    OfferSnapshot,
    StoreListing,
)
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.schemas import ProductRegisterRequest

pytestmark = pytest.mark.integration

_OWNER = AuthenticatedPrincipal(
    id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
    role=UserRole.USER,
    display_name="concurrency-tester",
)


@pytest.fixture
def pg_factory():
    url = (os.environ.get("TEST_DATABASE_URL") or "").strip()
    if not url or os.environ.get("ALLOW_SUPABASE_PERSISTENCE_TEST") != "1":
        pytest.skip(
            "Defina TEST_DATABASE_URL (banco descartável) e "
            "ALLOW_SUPABASE_PERSISTENCE_TEST=1"
        )
    get_settings.cache_clear()
    reset_database_cache()
    engine = create_db_engine(Settings(database_url=url))
    create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    created: list[uuid.UUID] = []
    try:
        yield factory, created
    finally:
        with factory() as cleanup:
            for product_id in created:
                listing_ids = select(StoreListing.id).where(
                    StoreListing.canonical_product_id == product_id
                )
                cleanup.execute(
                    delete(OfferEvent).where(OfferEvent.listing_id.in_(listing_ids))
                )
                cleanup.execute(
                    delete(OfferSnapshot).where(
                        OfferSnapshot.listing_id.in_(listing_ids)
                    )
                )
                cleanup.execute(
                    delete(StoreListing).where(
                        StoreListing.canonical_product_id == product_id
                    )
                )
                cleanup.execute(
                    delete(CanonicalProduct).where(CanonicalProduct.id == product_id)
                )
            cleanup.commit()
        engine.dispose()
        reset_database_cache()
        get_settings.cache_clear()


def _request(store: str, product_id: str, title: str) -> ProductRegisterRequest:
    return ProductRegisterRequest(
        title=title,
        brand="MSI",
        store=store,
        country="BR",
        product_id=product_id,
        canonical_url=f"https://{store}.example/produto/{product_id}",
    )


def test_concurrent_cross_store_registration_creates_one_canonical(
    pg_factory,
) -> None:
    factory, created = pg_factory
    suffix = uuid.uuid4().hex[:8]
    first_registered = threading.Event()
    outcome: dict[str, object] = {}

    def first_store() -> None:
        with factory() as session:
            response = ProductRegistrationService(session).register(
                _request(
                    "loja-a",
                    f"a-{suffix}",
                    "Placa de Video MSI Shadow 3X OC 12GB GeForce RTX5070 GDDR7",
                ),
                owner=_OWNER,
            )
            outcome["first"] = response
            first_registered.set()
            # Keep the transaction (and the identity lock) open on purpose.
            time.sleep(0.8)
            session.commit()

    worker = threading.Thread(target=first_store)
    worker.start()
    assert first_registered.wait(timeout=20)

    started = time.perf_counter()
    with factory() as session:
        second = ProductRegistrationService(session).register(
            _request(
                "loja-b",
                f"b-{suffix}",
                "MSI RTX 5070 12G Shadow 3X OC NVIDIA GeForce 12GB GDDR7",
            ),
            owner=_OWNER,
        )
        session.commit()
    waited = time.perf_counter() - started
    worker.join(timeout=20)

    first = outcome["first"]
    created.extend({first.product.id, second.product.id})  # type: ignore[attr-defined]
    assert second.product.id == first.product.id, "CanonicalProduct duplicado"  # type: ignore[attr-defined]
    assert second.resolution == "existing_product_new_listing"
    assert waited >= 0.5, "o segundo cadastro deveria aguardar o lock de identidade"
    with factory() as session:
        listing_stores = session.scalars(
            select(StoreListing.store).where(
                StoreListing.canonical_product_id == second.product.id
            )
        ).all()
        canonical_count = session.scalar(
            select(func.count())
            .select_from(StoreListing)
            .where(StoreListing.product_id.in_([f"a-{suffix}", f"b-{suffix}"]))
            .where(StoreListing.canonical_product_id != second.product.id)
        )
    assert sorted(listing_stores) == ["loja-a", "loja-b"]
    assert canonical_count == 0


@pytest.mark.parametrize(
    "brand", ["MSI", None], ids=["with_identity", "no_identity_lock"]
)
def test_concurrent_same_listing_registers_once_and_replays(
    pg_factory, brand: str | None
) -> None:
    """Two simultaneous POST-equivalent calls for one listing (MSI test data).

    Exactly one CanonicalProduct and one StoreListing may exist; the loser
    answers idempotently (created=false) instead of failing.
    """
    factory, created = pg_factory
    suffix = uuid.uuid4().hex[:8]
    product_id = f"msi-{suffix}"
    base = f"https://www.kabum.com.br/produto/{product_id}/placa-mae-msi-a520m-a-pro"
    urls = [f"{base}?gclsrc=aw.ds&utm_id=22429436060", base]
    barrier = threading.Barrier(2)
    results: list[object] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def run(index: int) -> None:
        request = ProductRegisterRequest(
            title="Placa-Mae MSI A520M-A PRO, AMD AM4, mATX, DDR4, Preto",
            brand=brand,
            store="kabum",
            country="BR",
            product_id=product_id,
            canonical_url=urls[index],
        )
        try:
            with factory() as session:
                barrier.wait(timeout=20)
                results[index] = ProductRegistrationService(session).register_saved(
                    request, owner=_OWNER
                )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [None, None], errors
    responses = [r for r in results if r is not None]
    assert len(responses) == 2
    created.extend({r.product.id for r in responses})  # type: ignore[attr-defined]
    assert sorted(r.created for r in responses) == [False, True]  # type: ignore[attr-defined]
    assert sorted(r.listing_created for r in responses) == [False, True]  # type: ignore[attr-defined]
    assert responses[0].product.id == responses[1].product.id  # type: ignore[attr-defined]
    with factory() as session:
        listing_count = session.scalar(
            select(func.count())
            .select_from(StoreListing)
            .where(StoreListing.product_id == product_id)
        )
        product_count = session.scalar(
            select(func.count())
            .select_from(CanonicalProduct)
            .where(CanonicalProduct.id == responses[0].product.id)  # type: ignore[attr-defined]
        )
    assert listing_count == 1
    assert product_count == 1
