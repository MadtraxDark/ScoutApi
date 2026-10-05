"""Controlled benchmark: real approved sources and an isolated catalog.

Run in Compose with PYTHONPATH pointing to the working tree. The application
database is read-only; benchmark catalog state is SQLite or an Alembic-migrated
probe PostgreSQL database. Existing Drive files are reused by default.
--upload-drive creates a unique probe folder and removes only probe artifacts.
Drive reuse timings must not be reported as new-upload timings.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from scout_api.core.config import get_settings
from scout_api.core.database import get_session_factory
from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.images.claim import claim_due_optimizations
from scout_api.modules.images.downloader import ImageDownloader
from scout_api.modules.images.drive_client import get_drive_storage
from scout_api.modules.images.optimizer import AvifOptimizer
from scout_api.modules.images.repository import ProductImageRepository
from scout_api.modules.images.schemas import ApprovedImageInput
from scout_api.modules.images.service import ProductImageService
from scout_api.modules.images.worker import process_claimed_image
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.repository import MatchingRepository
from scout_api.modules.matching.schemas import ProductRegisterRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_id", type=UUID)
    parser.add_argument(
        "--upload-drive",
        action="store_true",
        help="Upload to a unique probe folder and clean up only its artifacts.",
    )
    parser.add_argument(
        "--catalog-db-url",
        help="Isolated benchmark DB already migrated with Alembic; never the app DB.",
    )
    parser.add_argument("--counts", default="1,5,10,20")
    args = parser.parse_args()
    with get_session_factory()() as source_session:
        product = ProductRegistrationService(source_session).get_product(
            args.product_id
        )
        assert product is not None
        sources = ProductImageRepository(source_session).list_for_product(
            args.product_id
        )
        # Only files already approved AND preserved are eligible for this probe.
        snapshots = [
            dict(
                id=row.id,
                source_url=row.source_url,
                original=row.original_drive_file_id,
                optimized=row.optimized_drive_file_id,
            )
            for row in sources
            if row.original_drive_file_id and row.optimized_drive_file_id
        ]
        canonical = MatchingRepository(source_session).get_canonical(args.product_id)
        owner = AuthenticatedPrincipal(
            id=canonical.owner_user_id or args.product_id, role=UserRole.ADMIN
        )
        title = product.title

    drive = get_drive_storage()
    results = []
    for count in map(int, args.counts.split(",")):
        if len(snapshots) < count:
            continue
        if args.catalog_db_url:
            from sqlalchemy.engine import make_url

            assert make_url(args.catalog_db_url).database.startswith(
                "scout_import_benchmark"
            )
            engine = create_engine(args.catalog_db_url)
        else:
            engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            create_all(engine)
        with Session(engine) as session:
            request = ProductRegisterRequest(
                title=title,
                store="magazineluiza",
                product_id=f"{args.product_id}-isolated-{count}",
                country="BR",
                images=[
                    ApprovedImageInput(
                        source_url=row["source_url"], position=i, is_main=i == 0
                    )
                    for i, row in enumerate(snapshots[:count])
                ],
            )
            app = FastAPI()

            @app.post("/products")
            def register(payload: ProductRegisterRequest):
                return ProductRegistrationService(session).register_saved(
                    payload, owner=owner
                )

            started = time.perf_counter()
            response = TestClient(app).post(
                "/products", json=request.model_dump(mode="json")
            )
            http_ms = (time.perf_counter() - started) * 1000
            assert response.status_code == 200, response.text
            saved_id = UUID(response.json()["product"]["id"])
            rows = ProductImageRepository(session).list_for_product(saved_id)
            assert len(rows) == count and all(
                row.original_status == "pending" for row in rows
            )
            mapping = {
                str(row.id): snapshot
                for row, snapshot in zip(rows, snapshots[:count], strict=True)
            }
            times = defaultdict(float)
            artifacts = []

            class ProbeDrive:
                def __init__(self, metrics=times, registry=artifacts):
                    from uuid import uuid4

                    self.metrics = metrics
                    self.registry = registry
                    self.root_folder_id = drive.ensure_folder(
                        f"import-benchmark-{uuid4()}", parent_id=drive.root_folder_id
                    )
                    self.registry.append(self.root_folder_id)

                def ensure_folder(self, name, *, parent_id):
                    before = time.perf_counter()
                    file_id = drive.ensure_folder(name, parent_id=parent_id)
                    if file_id not in self.registry:
                        self.registry.append(file_id)
                    self.metrics["drive_folder_ms"] += (
                        time.perf_counter() - before
                    ) * 1000
                    return file_id

                def upload_bytes(self, *, name, parent_id, data, mime_type):
                    before = time.perf_counter()
                    file_id = drive.upload_bytes(
                        name=name, parent_id=parent_id, data=data, mime_type=mime_type
                    )
                    self.registry.append(file_id)
                    key = (
                        "drive_avif_upload_ms"
                        if name.endswith(".avif")
                        else "drive_original_upload_ms"
                    )
                    self.metrics[key] += (time.perf_counter() - before) * 1000
                    return file_id

                def download_bytes(self, file_id):
                    before = time.perf_counter()
                    data = drive.download_bytes(file_id)
                    self.metrics["drive_original_read_ms"] += (
                        time.perf_counter() - before
                    ) * 1000
                    return data

            class ReadOnlyDrive:
                root_folder_id = drive.root_folder_id

                def __init__(self, metrics=times, sources=mapping):
                    self.metrics = metrics
                    self.sources = sources

                def ensure_folder(self, name, *, parent_id):
                    return "probe"  # No folder mutations in a benchmark.

                def upload_bytes(self, *, name, parent_id, data, mime_type):
                    snapshot = self.sources[name.split(".")[0]]
                    return (
                        snapshot["optimized"]
                        if name.endswith(".avif")
                        else snapshot["original"]
                    )

                def download_bytes(self, file_id):
                    before = time.perf_counter()
                    data = drive.download_bytes(file_id)
                    self.metrics["drive_original_read_ms"] += (
                        time.perf_counter() - before
                    ) * 1000
                    return data

            from unittest.mock import patch

            original_download = ImageDownloader.download
            original_convert = AvifOptimizer.convert

            def download(self, url, metrics=times, action=original_download):
                before = time.perf_counter()
                result = action(self, url)
                metrics["source_download_validate_hash_ms"] += (
                    time.perf_counter() - before
                ) * 1000
                return result

            def convert(self, data, metrics=times, action=original_convert, **kwargs):
                before = time.perf_counter()
                result = action(self, data, **kwargs)
                metrics["avif_conversion_ms"] += (time.perf_counter() - before) * 1000
                return result

            storage = ProbeDrive() if args.upload_drive else ReadOnlyDrive()
            background = time.perf_counter()
            with (
                patch.object(ImageDownloader, "download", download),
                patch.object(AvifOptimizer, "convert", convert),
            ):
                while jobs := claim_due_optimizations(
                    session, worker_id="isolated-benchmark"
                ):
                    session.commit()
                    for row in jobs:
                        process_claimed_image(
                            session, row, drive=storage, settings=get_settings()
                        )
                        session.commit()
                        print(
                            json.dumps(
                                {
                                    "count": count,
                                    "completed_image": str(row.id),
                                    "status": row.optimized_status,
                                }
                            ),
                            flush=True,
                        )
            status = ProductImageService(session).import_status(saved_id, viewer=owner)
            assert status.ready == count, status
            result = {
                "images": count,
                "http_ms": round(http_ms, 1),
                "background_ms": round((time.perf_counter() - background) * 1000, 1),
                **{key: round(value, 1) for key, value in times.items()},
                "drive_new_upload": "real_isolated_probe"
                if args.upload_drive
                else "not_executed_read_only_probe",
            }
            results.append(result)
            print(json.dumps(result), flush=True)
            for file_id in reversed(artifacts):
                drive.delete_file(file_id)
        engine.dispose()
    print("RESULTS=" + json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
