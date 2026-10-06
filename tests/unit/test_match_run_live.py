"""Contrato leve e autorização dos resultados progressivos de MatchRun."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.test_match_run_progressive import _hit

from scout_api.core.database import Base
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.matching.match_run_service import MatchRunService
from scout_api.modules.matching.match_run_staging import stage_hit, stage_product
from scout_api.modules.matching.models import (
    CanonicalProduct,
    MatchStoreRun,
    ProductMatchRun,
)
from scout_api.modules.matching.schemas import MatchStoreLiveView


@pytest.fixture()
def factory():
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "https://operator:secret@store.example/product",
        "https://store.example/product?access_token=private",
        "javascript:alert(1)",
    ],
)
def test_live_nao_expoe_credenciais_em_urls(url: str) -> None:
    view = MatchStoreLiveView(
        id=uuid.uuid4(),
        store="fixture",
        status="match",
        matched_url=url,
        matched_canonical_url=url,
    )
    assert view.matched_url is None
    assert view.matched_canonical_url is None


def _run(session: Session):
    principal = AuthenticatedPrincipal(id=uuid.uuid4(), role=UserRole.USER)
    product = CanonicalProduct(title="Produto privado", owner_user_id=principal.id)
    session.add(product)
    session.flush()
    run = ProductMatchRun(
        product_id=product.id,
        requested_by=principal.id,
        status="running",
        worker_id="worker",
        attempts=2,
        started_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
        claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        reference_payload={"segredo": "contexto interno"},
    )
    session.add(run)
    session.commit()
    return run, principal


def test_live_sem_lojas_e_lease_expirada(factory) -> None:
    with factory() as session:
        run, principal = _run(session)
        run.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        view = MatchRunService(session).get_live(run.id, principal=principal)
        assert view.run.status == "running"
        assert view.is_effectively_active is False
        assert view.stores == []
        assert view.auto_matches_found == 0


def test_live_owner_inacessivel_mesmo_erro_run_inexistente(factory) -> None:
    with factory() as session:
        run, _ = _run(session)
        other = AuthenticatedPrincipal(id=uuid.uuid4(), role=UserRole.USER)
        for run_id in (run.id, uuid.uuid4()):
            with pytest.raises(LookupError, match="^RUN_NOT_FOUND$"):
                MatchRunService(session).get_live(run_id, principal=other)


def test_live_projecao_uma_query_sem_evidencias_nem_contexto(factory) -> None:
    with factory() as session:
        run, principal = _run(session)
        for decision, store in (("auto_match", "amazon_br"), ("review", "kabum")):
            hit = _hit(decision)
            session.add(
                MatchStoreRun(
                    run_id=run.id,
                    store=store,
                    status="match",
                    matched_decision=decision,
                    matched_payload=stage_hit(hit),
                    matched_price=hit.product.price,
                    matched_currency="BRL",
                    queries=["evidencia privada"],
                    matched_reasons=["razão privada"],
                )
            )
        session.commit()
        run_id = run.id
    statements = []

    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(factory.kw["bind"], "before_cursor_execute", collect)
    try:
        with factory() as observer:
            view = MatchRunService(observer).get_live(run_id, principal=principal)
            payload = view.model_dump(mode="json")
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", collect)
    assert len(statements) == 1
    assert all("match_candidate_logs" not in sql for sql in statements)
    assert all("reference_payload" not in sql for sql in statements)
    assert view.auto_matches_found == 1
    assert {row.matched_decision for row in view.stores} == {"auto_match", "review"}
    assert view.stores[0].matched_store == "amazon"
    assert view.stores[0].matched_product_id == "B0TEST1234"
    serialized = str(payload)
    for private in (
        "contexto interno",
        "evidencia privada",
        "razão privada",
        "matched_payload",
        "worker_id",
    ):
        assert private not in serialized
    assert isinstance(payload["stores"][0]["matched_price"], str)
    assert Decimal(payload["stores"][0]["matched_price"]) == Decimal("4799.99")


def test_live_schema_legado_decisao_desconhecida_e_decimal() -> None:
    row = MatchStoreLiveView(
        id=uuid.uuid4(),
        store="amazon_br",
        status="match",
        matched_price=Decimal("0.01"),
    )
    assert row.matched_decision is None
    assert row.model_dump(mode="json")["matched_price"] == "0.01"


def test_roster_inicio_outcome_idempotente_e_fencing_attempt(factory) -> None:
    with factory() as session:
        run, principal = _run(session)
        service = MatchRunService(session)
        service.record_targets(
            run, ["amazon_br", "kabum"], stage_product(_hit().product)
        )
        session.commit()
        assert run.stores_total == 2
        assert all(
            row.status == "pending" and row.started_at is None for row in run.store_runs
        )
        service.record_store_started(run, "amazon_br")
        assert (
            next(row for row in run.store_runs if row.store == "amazon_br").status
            == "running"
        )
        kwargs = dict(
            store="amazon_br",
            display_name="Amazon",
            status="match",
            duration_ms=10,
            queries=[],
            candidates_found=1,
            candidates_evaluated=1,
            matched_decision="auto_match",
            matched_payload=stage_hit(_hit()),
            expected_worker_id="worker",
        )
        assert service.apply_store_outcome(run, expected_attempts=1, **kwargs) is None
        first = service.apply_store_outcome(run, expected_attempts=2, **kwargs)
        session.commit()
        second = service.apply_store_outcome(run, expected_attempts=2, **kwargs)
        assert first.id == second.id
        assert run.stores_completed == 1
        assert run.matches_found == 1
        service.record_store_started(run, "amazon_br")
        assert first.status == "match"
        live = service.get_live(run.id, principal=principal)
        assert live.auto_matches_found == 1
