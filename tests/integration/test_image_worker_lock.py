"""Opt-in PostgreSQL check against an isolated Alembic-migrated probe DB."""

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.matching.models import CanonicalProduct
from scout_api.modules.matching.repository import MatchingRepository


def test_image_processing_lock_does_not_block_catalog_registration() -> None:
    url = os.getenv("IMPORT_BENCHMARK_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated import benchmark PostgreSQL database")
    assert (make_url(url).database or "").startswith("scout_import_benchmark")
    engine = create_engine(url)
    try:
        with Session(engine) as worker, Session(engine) as registration:
            product_id = worker.scalar(select(CanonicalProduct.id).limit(1))
            assert product_id is not None
            ProductImageRepository(worker).lock_processing(product_id)
            registration.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            MatchingRepository(registration).lock_canonical(product_id)
            # Another worker is coordinated while the registration row is free.
            acquired = registration.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"product-images:{product_id}"},
            )
            assert acquired is False
    finally:
        engine.dispose()
