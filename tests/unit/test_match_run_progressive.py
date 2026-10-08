"""RegressÃµes de resultados durÃ¡veis antes da conclusÃ£o do Product Match."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scout_api.core.config import Settings
from scout_api.core.database import Base
from scout_api.modules.crawler.models.product import ProductPriceItem
from scout_api.modules.matching import match_run_worker as worker_mod
from scout_api.modules.matching.identity import identity_from_price_item
from scout_api.modules.matching.models import (
    CanonicalProduct,
    MatchStoreRun,
    ProductMatchRun,
)
from scout_api.modules.matching.product_match_service import ProductMatchService
from scout_api.modules.matching.schemas import MatchHit, MatchResponse


def _hit(decision: str = "auto_match") -> MatchHit:
    return MatchHit(
        store="amazon_br",
        country="BR",
        decision=decision,
        confidence=Decimal("0.97"),
        product=ProductPriceItem(
            store="amazon",
            country="BR",
            product_id="B0TEST1234",
            title="Samsung Galaxy S25 Ultra",
            brand="Samsung",
            model="S25 Ultra",
            url="https://www.amazon.com.br/dp/B0TEST1234",
            canonical_url="https://www.amazon.com.br/dp/B0TEST1234",
            currency="BRL",
            price=Decimal("4799.99"),
            pix_price=Decimal("4499.99"),
            installment_count=10,
            installment_price=Decimal("479.99"),
            metadata={"authorization": "Bearer segredo", "cookies": "sessao"},
        ),
    )


@pytest.mark.parametrize("decision", ["auto_match", "review"])
def test_staging_preserva_decisao_condicoes_e_exclui_metadata(decision: str) -> None:
    from scout_api.modules.matching.match_run_staging import restore_hit, stage_hit

    original = _hit(decision)
    payload = stage_hit(original)
    restored = restore_hit(payload)
    assert restored.decision == decision
    assert restored.store == "amazon_br"
    assert restored.product.store == "amazon"
    assert restored.product.price == Decimal("4799.99")
    assert restored.product.pix_price == Decimal("4499.99")
    assert restored.product.installment_count == 10
    assert restored.product.metadata == {}
    assert "segredo" not in str(payload)
    assert "sessao" not in str(payload)


def test_reclaim_inclui_hit_anterior_na_persistencia_sem_nova_busca() -> None:
    restored = _hit()
    search = MagicMock()
    service = ProductMatchService(
        search_service=search, scrape_service=MagicMock(), session=MagicMock()
    )
    service._resolve_stores = MagicMock(return_value=["amazon_br"])
    service._persist = MagicMock(return_value=uuid.uuid4())
    reference = restored.product.model_copy(
        update={"store": "reference", "product_id": "reference"}
    )
    response = service._match_with_reference(
        reference,
        identity_from_price_item(reference),
        stores=["amazon_br"],
        include_review=True,
        persist=True,
        include_images=False,
        max_candidates_per_store=1,
        skip_stores={"amazon_br"},
        restored_matches=[restored],
    )
    assert response.matches == [restored]
    assert service._persist.call_args.args[1] == [restored]
    search.search.assert_not_called()


def test_target_callback_classifica_listing_conhecida_como_refresh_e_pula_serp() -> (
    None
):
    search = MagicMock()
    search.search.return_value = []
    service = ProductMatchService(
        search_service=search, scrape_service=MagicMock(), session=MagicMock()
    )
    service._resolve_stores = MagicMock(return_value=["kabum", "amazon_br"])
    service._persist = MagicMock(return_value=uuid.uuid4())
    reference = _hit().product.model_copy(
        update={"store": "synthetic", "product_id": "identity:phone"}
    )
    callback = MagicMock(return_value={"kabum"})

    response = service._match_with_reference(
        reference,
        identity_from_price_item(reference),
        stores=None,
        include_review=True,
        persist=True,
        include_images=False,
        max_candidates_per_store=1,
        on_targets_resolved=callback,
    )

    callback.assert_called_once()
    assert response.unmatched_stores == ["amazon_br"]
    searched_stores = {call.args[0] for call in search.search.call_args_list}
    assert searched_stores == {"amazon_br"}


@pytest.mark.parametrize("decision", ["auto_match", "review"])
def test_reclaim_preserva_regra_de_consenso_gtin(decision: str) -> None:
    restored = _hit(decision)
    restored.product.gtin = "7894900011517"
    reference = restored.product.model_copy(
        update={"store": "reference", "product_id": "reference", "gtin": None}
    )
    service = ProductMatchService(
        search_service=MagicMock(), scrape_service=MagicMock()
    )
    service._resolve_stores = MagicMock(return_value=["amazon_br"])
    response = service._match_with_reference(
        reference,
        identity_from_price_item(reference),
        stores=["amazon_br"],
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=1,
        skip_stores={"amazon_br"},
        restored_matches=[restored],
    )
    assert response.discovered_gtin == (
        "7894900011517" if decision == "auto_match" else None
    )


def test_fencing_final_impede_persistencia_de_worker_antigo() -> None:
    restored = _hit()
    service = ProductMatchService(
        search_service=MagicMock(), scrape_service=MagicMock(), session=MagicMock()
    )
    service._resolve_stores = MagicMock(return_value=["amazon_br"])
    service._persist = MagicMock()
    fence = MagicMock(side_effect=RuntimeError("MATCH_RUN_CLAIM_LOST"))
    with pytest.raises(RuntimeError, match="MATCH_RUN_CLAIM_LOST"):
        service._match_with_reference(
            restored.product,
            identity_from_price_item(restored.product),
            stores=["amazon_br"],
            include_review=True,
            persist=True,
            include_images=False,
            max_candidates_per_store=1,
            skip_stores={"amazon_br"},
            restored_matches=[restored],
            before_persist=fence,
        )
    fence.assert_called_once_with()
    service._persist.assert_not_called()


def test_worker_outcome_commitado_consultavel_antes_do_match_retornar(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prova commit entre conexÃµes; nÃ£o comprova locks PostgreSQL."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'progressive.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker_mod, "get_session_factory", lambda: factory)
    observed = []
    hit = _hit("review")

    def fake_execute(sess: Session, *, on_store_outcome, **kwargs) -> MatchResponse:
        from scout_api.modules.matching.match_run_staging import stage_hit

        on_store_outcome(
            worker_mod.MatchStoreOutcome(
                store="amazon_br",
                display_name="Amazon Brasil",
                status="match",
                duration_ms=10,
                queries=(),
                candidates_found=1,
                candidates_evaluated=1,
                search_duration_ms=4,
                candidate_fetch_duration_ms=5,
                matched_decision="review",
                matched_payload=stage_hit(hit),
                matched_url=hit.product.url,
                matched_price=hit.product.price,
            )
        )
        with factory() as observer:
            run = observer.get(ProductMatchRun, kwargs["run_id"])
            outcome = observer.scalar(
                select(MatchStoreRun).where(MatchStoreRun.run_id == run.id)
            )
            assert run.status == "running"
            assert outcome is not None
            assert outcome.matched_decision == "review"
            assert outcome.matched_price == hit.product.price
            observed.append(outcome.id)
        return MatchResponse(reference=hit.product, matches=[])

    monkeypatch.setattr(worker_mod, "_execute_match_for_run", fake_execute)
    with factory() as session:
        product = CanonicalProduct(title=hit.product.title, owner_user_id=uuid.uuid4())
        session.add(product)
        session.flush()
        run = ProductMatchRun(
            product_id=product.id,
            requested_by=product.owner_user_id,
            status="running",
            reference_url=hit.product.url,
            worker_id="progressive-worker",
            attempts=1,
            started_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        worker_mod.process_claimed_run(
            session,
            run,
            worker_id="progressive-worker",
            settings=Settings(match_run_lease_seconds=600),
        )
        session.commit()
    assert len(observed) == 1
    engine.dispose()


def test_reclaim_descarta_gtin_aprendido_quando_hits_anteriores_conflitam() -> None:
    first, second = _hit(), _hit()
    first.product.gtin = "7894900011517"
    second.product.gtin = "7894900010015"
    second.store = "kabum"
    reference = first.product.model_copy(update={"store": "reference", "gtin": None})
    service = ProductMatchService(
        search_service=MagicMock(), scrape_service=MagicMock()
    )
    response = service._match_with_reference(
        reference,
        identity_from_price_item(reference),
        stores=["amazon_br", "kabum"],
        include_review=True,
        persist=False,
        include_images=False,
        max_candidates_per_store=1,
        target_stores=["amazon_br", "kabum"],
        skip_stores={"amazon_br", "kabum"},
        restored_matches=[first, second],
    )
    assert response.discovered_gtin is None
    assert response.reference.gtin is None


def test_falha_commit_outcome_impede_completed_e_catalogo_incompleto(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha real no commit da sessão curta interrompe antes de persistir catálogo."""
    from scout_api.modules.matching.match_run_staging import stage_hit
    from scout_api.modules.matching.models import StoreListing

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'commit-failure.db'}")
    Base.metadata.create_all(engine)
    main_factory = sessionmaker(bind=engine, expire_on_commit=False)
    commit_attempts = []

    class FailingCommitSession(Session):
        def commit(self) -> None:
            commit_attempts.append("outcome")
            raise RuntimeError("commit da observação indisponível")

    failing_factory = sessionmaker(
        bind=engine, class_=FailingCommitSession, expire_on_commit=False
    )
    monkeypatch.setattr(worker_mod, "get_session_factory", lambda: failing_factory)
    persist_catalog = MagicMock()
    hit = _hit()

    def fake_execute(sess: Session, *, on_store_outcome, **kwargs) -> MatchResponse:
        on_store_outcome(
            worker_mod.MatchStoreOutcome(
                store="amazon_br",
                display_name="Amazon Brasil",
                status="match",
                duration_ms=10,
                queries=(),
                candidates_found=1,
                candidates_evaluated=1,
                search_duration_ms=4,
                candidate_fetch_duration_ms=5,
                matched_decision="auto_match",
                matched_payload=stage_hit(hit),
                matched_url=hit.product.url,
                matched_price=hit.product.price,
            )
        )
        persist_catalog()
        return MatchResponse(reference=hit.product, matches=[hit])

    monkeypatch.setattr(worker_mod, "_execute_match_for_run", fake_execute)
    with main_factory() as session:
        product = CanonicalProduct(title=hit.product.title, owner_user_id=uuid.uuid4())
        session.add(product)
        session.flush()
        run = ProductMatchRun(
            product_id=product.id,
            requested_by=product.owner_user_id,
            status="running",
            reference_url=hit.product.url,
            worker_id="commit-failure-worker",
            attempts=1,
            started_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        run_id = run.id
        worker_mod.process_claimed_run(
            session,
            run,
            worker_id="commit-failure-worker",
            settings=Settings(match_run_lease_seconds=600),
        )
        session.commit()
    with main_factory() as observer:
        run = observer.get(ProductMatchRun, run_id)
        assert run.status == "failed"
        assert (
            observer.scalar(select(MatchStoreRun).where(MatchStoreRun.run_id == run_id))
            is None
        )
        assert (
            observer.scalar(
                select(StoreListing).where(
                    StoreListing.canonical_product_id == run.product_id
                )
            )
            is None
        )
    assert commit_attempts == ["outcome"]
    persist_catalog.assert_not_called()
    engine.dispose()
