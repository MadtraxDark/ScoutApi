"""Repository persistence tests against an in-memory SQLite stand-in."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.core.database import Base
from scout_api.modules.crawler.models.product import ProductOffer, ProductPriceItem
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.identity import ProductIdentity
from scout_api.modules.matching.match_run_serializers import candidate_to_view
from scout_api.modules.matching.models import MatchCandidateLog
from scout_api.modules.matching.repository import MatchingRepository


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


def _item(**overrides: object) -> ProductPriceItem:
    base = {
        "store": "kabum",
        "country": "BR",
        "product_id": "123",
        "sku": "SKU-1",
        "gtin": "7891991010863",
        "url": "https://www.kabum.com.br/produto/123",
        "canonical_url": "https://www.kabum.com.br/produto/123",
        "title": "Produto Teste",
        "price": Decimal("199.90"),
        "currency": "BRL",
        "seller": "KaBuM!",
        "availability": "available",
        "available": True,
    }
    base.update(overrides)
    return ProductPriceItem.model_validate(base)


def test_repository_upsert_canonical_listing_and_snapshot(session: Session) -> None:
    repo = MatchingRepository(session)
    identity = ProductIdentity(
        gtin="7891991010863",
        brand="Acme",
        model="X1",
        title="Produto Teste",
        title_normalized="produto teste",
        variant_attrs={"color": "black"},
    )
    canonical = repo.upsert_canonical_from_identity(identity, title="Produto Teste")
    item = _item()
    listing = repo.upsert_listing(
        canonical=canonical,
        item=item,
        decision="auto_match",
        confidence=Decimal("0.9500"),
    )
    offer = ProductOffer(
        store=item.store,
        country=item.country,
        product_id=item.product_id,
        url=item.url,
        canonical_url=item.canonical_url,
        price=item.price,
        currency=item.currency,
        seller=item.seller,
        availability=item.availability,
        available=item.available,
    )
    snapshot = repo.append_snapshot_from_offer(listing, offer)
    repo.commit()

    found = repo.find_canonical_by_gtin("7891991010863")
    assert found is not None
    assert found.id == canonical.id
    assert repo.find_listing_by_url("kabum", item.canonical_url) is not None
    assert repo.latest_snapshot(listing.id) is not None
    assert snapshot.fingerprint
    assert Base.metadata.tables.keys() >= {
        "canonical_products",
        "store_listings",
        "offer_snapshots",
    }


def test_match_candidate_embedding_evidence_is_metadata_only() -> None:
    evidence = {
        "status": "ok",
        "provider": "openai",
        "model": "text-embedding-3-small",
        "representation": "hybrid",
        "similarity": 0.94,
        "duration_ms": 120,
        "cache_hits": 1,
        "input_tokens": 42,
    }
    candidate = MatchCandidateLog(
        sequence=1,
        decision="review",
        reasons=["variant_semantic_uncertain"],
        embedding_evidence=evidence,
    )

    view = candidate_to_view(candidate)
    assert view.embedding_evidence == evidence
    assert not hasattr(view, "vector")
