"""Prova transacional em PostgreSQL isolado com schema criado via Alembic."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from tests.unit.test_match_run_live import _run
from tests.unit.test_match_run_progressive import _hit

from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import restore_hit, stage_hit
from scout_api.modules.matching.models import (
    CanonicalProduct,
    MatchStoreRun,
    ProductMatchRun,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def postgres_factory():
    address = os.environ.get("TEST_DATABASE_URL")
    if not address:
        pytest.skip("TEST_DATABASE_URL do PostgreSQL isolado não configurada")
    url = make_url(address)
    if (
        not url.drivername.startswith("postgresql")
        or url.database != "scout_progressive_test"
    ):
        pytest.fail("Teste exige PostgreSQL isolado scout_progressive_test")
    engine = create_engine(url, pool_pre_ping=True)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_outcome_commitado_sobrevive_rollback_final_e_worker_antigo(postgres_factory):
    factory = postgres_factory
    with factory() as setup:
        run, principal = _run(setup)
        run_id, product_id = run.id, run.product_id
    try:
        with factory() as writer:
            run = writer.get(ProductMatchRun, run_id)
            hit = _hit()
            row = MatchRunService(writer).apply_store_outcome(
                run,
                store="amazon_br",
                display_name="Amazon Brasil",
                status="match",
                duration_ms=10,
                queries=[],
                candidates_found=1,
                candidates_evaluated=1,
                matched_decision="auto_match",
                matched_payload=stage_hit(hit),
                matched_price=hit.product.price,
                matched_currency="BRL",
                expected_worker_id="worker",
                expected_attempts=2,
            )
            writer.commit()
            with factory() as observer:
                live = MatchRunService(observer).get_live(run_id, principal=principal)
                assert live.run.status == "running"
                assert live.auto_matches_found == 1
                assert live.stores[0].matched_listing_id is None
            row.matched_listing_id = product_id
            run.status = "completed"
            writer.flush()
            writer.rollback()
        with factory() as observer:
            run = observer.get(ProductMatchRun, run_id)
            assert run.status == "running"
            assert run.store_runs[0].matched_listing_id is None
            restored = restore_hit(run.store_runs[0].matched_payload)
            assert restored.product.product_id == hit.product.product_id
            run.worker_id = "new-worker"
            run.attempts = 3
            observer.commit()
        with factory() as stale:
            run = stale.get(ProductMatchRun, run_id)
            rejected = MatchRunService(stale).apply_store_outcome(
                run,
                store="kabum",
                display_name="KaBuM",
                status="no_match",
                duration_ms=1,
                queries=[],
                candidates_found=0,
                candidates_evaluated=0,
                expected_worker_id="worker",
                expected_attempts=2,
            )
            assert rejected is None
            stale.commit()
        with factory() as observer:
            live = MatchRunService(observer).get_live(run_id, principal=principal)
            assert len(live.stores) == 1
            assert live.auto_matches_found == 1
    finally:
        with factory() as cleanup:
            cleanup.execute(delete(MatchStoreRun).where(MatchStoreRun.run_id == run_id))
            cleanup.execute(delete(ProductMatchRun).where(ProductMatchRun.id == run_id))
            cleanup.execute(
                delete(CanonicalProduct).where(CanonicalProduct.id == product_id)
            )
            cleanup.commit()
