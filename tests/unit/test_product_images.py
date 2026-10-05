"""Unit tests for product images (Drive mock, SSRF, AVIF fallback, gallery)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout_api.modules.auth.schemas import AuthenticatedPrincipal, UserRole
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.images.downloader import (
    ImageDownloader,
    assert_url_safe_for_download,
    validate_image_bytes,
)
from scout_api.modules.images.drive_client import (
    DriveClientError,
    GoogleDriveClient,
    InMemoryDriveStorage,
)
from scout_api.modules.images.optimizer import AvifOptimizer
from scout_api.modules.images.pipeline import ImagePipeline
from scout_api.modules.images.schemas import (
    AddProductImageRequest,
    ApprovedImageInput,
    GalleryPatchRequest,
    GalleryReorderItem,
)
from scout_api.modules.images.service import ProductImageService, to_image_view
from scout_api.modules.matching.db import create_all
from scout_api.modules.matching.product_registration_service import (
    ProductRegistrationService,
)
from scout_api.modules.matching.schemas import ProductRegisterRequest

_OWNER = AuthenticatedPrincipal(
    id=UUID("11111111-1111-4111-8111-111111111111"),
    role=UserRole.USER,
    display_name="tester",
)


@pytest.mark.parametrize("count", [1, 5, 10, 20])
def test_registration_only_persists_image_references(
    session: Session, count: int
) -> None:
    import time

    def slow_download(_url: str):
        time.sleep(10)
        raise AssertionError("O request não deve baixar imagens")

    request = ProductRegisterRequest(
        title="Produto de teste",
        gtin="7891991010863",
        images=[
            ApprovedImageInput(
                source_url=f"https://cdn.example/{i}.png", position=i, is_main=i == 0
            )
            for i in range(count)
        ],
    )
    started = time.perf_counter()
    with patch.object(
        ImageDownloader, "download", side_effect=slow_download
    ) as download:
        response = ProductRegistrationService(session).register(request, owner=_OWNER)
    elapsed = time.perf_counter() - started
    assert elapsed < 2
    download.assert_not_called()
    session.commit()
    assert len(response.product.images) == count
    assert all(image.original_status == "pending" for image in response.product.images)
    assert all(image.display_url for image in response.product.images)
    repeated = ProductRegistrationService(session).register(request, owner=_OWNER)
    assert len(repeated.product.images) == count


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png_bytes(size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), (0, 128, 255, 128)).save(buf, format="PNG")
    return buf.getvalue()


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


@pytest.fixture
def drive() -> InMemoryDriveStorage:
    return InMemoryDriveStorage()


def test_drive_inmemory_upload_download_delete(drive: InMemoryDriveStorage) -> None:
    folder = drive.ensure_folder("original", parent_id=drive.root_folder_id)
    file_id = drive.upload_bytes(
        name="a.png", parent_id=folder, data=b"abc", mime_type="image/png"
    )
    assert drive.download_bytes(file_id) == b"abc"
    drive.delete_file(file_id)
    with pytest.raises(DriveClientError, match="Storage file not found"):
        drive.download_bytes(file_id)
    drive.delete_file("missing")  # idempotent


def test_google_drive_client_refresh_and_upload() -> None:
    service = MagicMock()
    files = service.files.return_value
    files.list.return_value.execute.return_value = {"files": []}
    files.create.return_value.execute.return_value = {"id": "folder-1"}
    # Second create = upload
    files.create.return_value.execute.side_effect = [
        {"id": "folder-1"},
        {"id": "file-9"},
    ]
    client = GoogleDriveClient(service=service)
    with patch.object(client, "_credentials", return_value=MagicMock()):
        folder = client.ensure_folder("x", parent_id="root")
        assert folder == "folder-1"
        file_id = client.upload_bytes(
            name="a.png", parent_id=folder, data=b"hi", mime_type="image/png"
        )
        assert file_id == "file-9"


def test_google_drive_client_builds_per_request_http() -> None:
    """httplib2.Http is not thread-safe; AVIF workers share one Drive client.

    Each Drive API call must get its own AuthorizedHttp (Google requestBuilder
    pattern), otherwise concurrent optimize_now hits SSL bad_record_mac.
    """
    captured: dict[str, object] = {}

    def fake_build(*_args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return MagicMock(name="drive-service")

    client = GoogleDriveClient()
    creds = MagicMock(name="creds")
    with (
        patch.object(client, "_credentials", return_value=creds),
        patch("scout_api.modules.images.drive_client.build", side_effect=fake_build),
    ):
        service = client._drive()
        assert service is client._drive()  # cached service object

    assert "requestBuilder" in captured
    request_builder = captured["requestBuilder"]
    assert callable(request_builder)

    req_a = request_builder(None, "GET", "https://www.googleapis.com/drive/v3/a")
    req_b = request_builder(None, "GET", "https://www.googleapis.com/drive/v3/b")
    assert req_a.http is not req_b.http
    assert req_a.http.http is not req_b.http.http


def test_google_drive_delete_404_is_ok() -> None:
    service = MagicMock()
    from googleapiclient.errors import HttpError

    resp = MagicMock()
    resp.status = 404
    http_err = HttpError(resp, b"not found")
    service.files.return_value.delete.return_value.execute.side_effect = http_err
    client = GoogleDriveClient(service=service)
    client.delete_file("gone")


@pytest.mark.parametrize(
    ("http_status", "reason", "availability", "code", "retryable"),
    [
        (404, "notFound", "not_found", "storage_not_found", False),
        (
            403,
            "insufficientFilePermissions",
            "permission_denied",
            "storage_permission_denied",
            False,
        ),
        (
            403,
            "userRateLimitExceeded",
            "temporarily_unavailable",
            "storage_unavailable",
            True,
        ),
        (429, "", "temporarily_unavailable", "storage_unavailable", True),
        (503, "", "temporarily_unavailable", "storage_unavailable", True),
        (401, "", "storage_error", "storage_credentials_invalid", False),
    ],
)
def test_drive_http_errors_are_classified_without_exposing_provider_details(
    http_status: int,
    reason: str,
    availability: str,
    code: str,
    retryable: bool,
) -> None:
    import json

    from googleapiclient.errors import HttpError

    from scout_api.modules.images.drive_client import _drive_http_error

    response = MagicMock()
    response.status = http_status
    content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    failure = _drive_http_error(HttpError(response, content))
    assert failure.availability == availability
    assert failure.code == code
    assert failure.retryable is retryable
    assert "Google" not in str(failure)


def test_expired_drive_oauth_grant_is_non_retryable_storage_error() -> None:
    from google.auth.exceptions import GoogleAuthError

    from scout_api.modules.images.drive_client import _drive_transport_error

    failure = _drive_transport_error(
        GoogleAuthError("invalid_grant: Token has been expired or revoked.")
    )
    assert failure.availability == "storage_error"
    assert failure.code == "storage_credentials_invalid"
    assert failure.retryable is False
    assert "expired or revoked" not in str(failure)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.png",
        "http://localhost/x.png",
        "http://[::1]/x.png",
        "http://169.254.169.254/latest/meta-data",
        "ftp://example.com/x.png",
    ],
)
def test_ssrf_blocked_literal(url: str) -> None:
    with pytest.raises(RequestError):
        assert_url_safe_for_download(url)


def test_ssrf_blocked_private_dns() -> None:
    with patch(
        "scout_api.modules.images.downloader.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("10.0.0.5", 0))],
    ):
        with pytest.raises(RequestError, match="privada"):
            assert_url_safe_for_download("https://evil.example/x.png")


def test_validate_rejects_non_image() -> None:
    with pytest.raises(RequestError, match="não é uma imagem"):
        validate_image_bytes(
            b"not-an-image",
            claimed_content_type="image/png",
            max_dimension=4096,
        )


def test_validate_accepts_png_and_rgba() -> None:
    mime, w, h, ext = validate_image_bytes(
        _png_bytes(), claimed_content_type="text/html", max_dimension=4096
    )
    assert mime.startswith("image/")
    assert w == 32 and h == 32 and ext == "png"
    mime2, _, _, _ = validate_image_bytes(
        _rgba_png_bytes(), claimed_content_type="image/png", max_dimension=4096
    )
    assert mime2.startswith("image/")


def test_downloader_redirect_to_private_blocked() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.host == "cdn.example":
            return httpx.Response(
                302, headers={"Location": "http://127.0.0.1/secret.png"}
            )
        return httpx.Response(200, content=_png_bytes())

    transport = httpx.MockTransport(handler)
    downloader = ImageDownloader()

    real_client = httpx.Client(transport=transport, follow_redirects=False)

    class _Ctx:
        def __enter__(self) -> httpx.Client:
            return real_client

        def __exit__(self, *args: object) -> None:
            return None

    def fake_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host in {"127.0.0.1", "localhost"}:
            return [(2, 1, 6, "", ("127.0.0.1", 0))]
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    with (
        patch(
            "scout_api.modules.images.downloader.socket.getaddrinfo",
            side_effect=fake_getaddrinfo,
        ),
        patch("httpx.Client", return_value=_Ctx()),
    ):
        with pytest.raises(RequestError):
            downloader.download("https://cdn.example/photo.png")
    assert calls["n"] >= 1


def test_display_url_fallback_rules(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    reg = ProductRegistrationService(session)
    created = reg.register(
        ProductRegisterRequest(title="Cam", brand="Acme", gtin="7891991010863"),
        owner=_OWNER,
    )
    product_id = created.product.id
    svc = ProductImageService(session, drive=drive, schedule_avif=False)
    png = _png_bytes()

    def fake_download(url: str):
        import hashlib

        from scout_api.modules.images.downloader import DownloadedImage

        return DownloadedImage(
            data=png,
            content_type="image/png",
            width=32,
            height=32,
            sha256=hashlib.sha256(png).hexdigest(),
            extension="png",
            source_url=url,
        )

    with patch.object(svc._pipeline._downloader, "download", side_effect=fake_download):
        view = svc.add_image(
            product_id,
            AddProductImageRequest(
                source_url="https://cdn.example/a.png", is_main=True
            ),
            owner=_OWNER,
        )
    assert view.original_status == "ready"
    assert view.optimized_status == "pending"
    assert view.display_url == view.original_url
    assert view.display_url and "variant=original" in view.display_url
    assert view.optimized_url is None

    # Mark optimized ready → prefer AVIF path (distinct URL for cache safety)
    from scout_api.modules.images.repository import ProductImageRepository

    row = ProductImageRepository(session).get(view.image_id)
    assert row is not None
    row.optimized_status = "ready"
    row.optimized_drive_file_id = row.original_drive_file_id
    row.optimized_mime_type = "image/avif"
    session.flush()
    ready = to_image_view(row)
    assert ready.display_url == ready.optimized_url
    assert ready.optimized_url and "variant=optimized" in ready.optimized_url

    row.optimized_drive_file_id = None
    session.flush()
    missing_optimized_file = to_image_view(row)
    assert missing_optimized_file.image_status == "ready"
    assert missing_optimized_file.optimized_url is None
    assert missing_optimized_file.display_url == missing_optimized_file.original_url
    assert missing_optimized_file.image_warning_code == "conversion_failed"

    row.optimized_status = "failed"
    session.flush()
    failed = to_image_view(row)
    assert failed.display_url == failed.original_url

    row.optimized_status = "processing"
    session.flush()
    processing = to_image_view(row)
    assert processing.display_url == processing.original_url

    drive.delete_file(row.original_drive_file_id)
    with pytest.raises(RequestError) as failure:
        svc.get_content(product_id, row.id, viewer=_OWNER, variant="original")
    assert failure.value.code == "IMAGE_STORAGE_NOT_FOUND"
    assert failure.value.retryable is False


def test_image_availability_distinguishes_missing_processing_and_broken_reference(
    session: Session,
) -> None:
    from scout_api.modules.images.repository import ProductImageRepository

    product = (
        ProductRegistrationService(session)
        .register(
            ProductRegisterRequest(title="State", gtin="7891991010863"),
            owner=_OWNER,
        )
        .product
    )
    row = ProductImageRepository(session).create(
        product_id=product.id,
        source_url="https://cdn.example/image.png",
        position=0,
        is_main=True,
        original_status="pending",
    )
    pending = to_image_view(row)
    assert pending.image_status == "processing"
    assert pending.image_error_code is None

    row.original_status = "ready"
    row.original_drive_file_id = ""
    invalid = to_image_view(row)
    assert invalid.image_status == "invalid_reference"
    assert invalid.image_error_code == "invalid_reference"


def test_product_without_images_has_explicit_missing_image_status(
    session: Session,
) -> None:
    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Sem foto", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    view = reg.get_product(product.id, viewer=_OWNER)
    assert view is not None
    assert view.primary_image_url is None
    assert view.primary_image_status == "missing"


def test_approval_persists_only_selected(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Phone", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    svc = ProductImageService(session, drive=drive, schedule_avif=False)
    pngs = [_png_bytes(color=(i * 40, 0, 0)) for i in range(5)]

    def fake_download(url: str):
        import hashlib

        from scout_api.modules.images.downloader import DownloadedImage

        idx = int(url.rstrip(".png").split("/")[-1])
        data = pngs[idx]
        return DownloadedImage(
            data=data,
            content_type="image/png",
            width=32,
            height=32,
            sha256=hashlib.sha256(data).hexdigest(),
            extension="png",
            source_url=url,
        )

    approved = [
        ApprovedImageInput(
            source_url=f"https://cdn.example/{i}.png",
            position=i,
            is_main=(i == 0),
        )
        for i in (0, 2, 4)
    ]
    with patch.object(svc._pipeline._downloader, "download", side_effect=fake_download):
        views = svc.persist_approved(product.id, approved, owner=_OWNER)
    assert len(views) == 3
    assert len(drive.files) == 3
    listed = svc.list_images(product.id, viewer=_OWNER)
    assert listed.count == 3


def test_reorder_main_delete(session: Session, drive: InMemoryDriveStorage) -> None:
    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Phone", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    svc = ProductImageService(session, drive=drive, schedule_avif=False)

    def fake_download(url: str):
        import hashlib

        from scout_api.modules.images.downloader import DownloadedImage

        data = _png_bytes(color=(20 if url.endswith("/a.png") else 40, 10, 10))
        return DownloadedImage(
            data=data,
            content_type="image/png",
            width=32,
            height=32,
            sha256=hashlib.sha256(data).hexdigest(),
            extension="png",
            source_url=url,
        )

    with patch.object(svc._pipeline._downloader, "download", side_effect=fake_download):
        a = svc.add_image(
            product.id,
            AddProductImageRequest(
                source_url="https://cdn.example/a.png", is_main=True
            ),
            owner=_OWNER,
        )
        b = svc.add_image(
            product.id,
            AddProductImageRequest(source_url="https://cdn.example/b.png"),
            owner=_OWNER,
        )
    patched = svc.patch_gallery(
        product.id,
        GalleryPatchRequest(
            images=[
                GalleryReorderItem(image_id=b.image_id, position=0, is_main=True),
                GalleryReorderItem(image_id=a.image_id, position=1, is_main=False),
            ]
        ),
        owner=_OWNER,
    )
    assert patched.items[0].image_id == b.image_id
    assert patched.items[0].is_main is True
    svc.delete_image(product.id, b.image_id, owner=_OWNER)
    remaining = svc.list_images(product.id, viewer=_OWNER)
    assert remaining.count == 1
    assert remaining.items[0].image_id == a.image_id
    assert remaining.items[0].is_main is True


def test_avif_failure_keeps_original(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Phone", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    pipeline = ImagePipeline(session, drive=drive, schedule_avif=False)

    def fake_download(url: str):
        import hashlib

        from scout_api.modules.images.downloader import DownloadedImage

        data = _png_bytes()
        return DownloadedImage(
            data=data,
            content_type="image/png",
            width=32,
            height=32,
            sha256=hashlib.sha256(data).hexdigest(),
            extension="png",
            source_url=url,
        )

    with patch.object(pipeline._downloader, "download", side_effect=fake_download):
        rows = pipeline.persist_approved(
            product.id,
            [
                ApprovedImageInput(
                    source_url="https://cdn.example/x.png",
                    position=0,
                    is_main=True,
                )
            ],
        )
    row = rows[0]
    assert row.original_status == "ready"
    with patch.object(
        pipeline._optimizer,
        "convert",
        side_effect=RuntimeError("avif boom"),
    ):
        pipeline.optimize_now(row)
    assert row.original_status == "ready"
    assert row.optimized_status == "failed"
    assert row.original_drive_file_id
    assert drive.files[row.original_drive_file_id][1] == _png_bytes()


def test_avif_optimizer_no_upscale() -> None:
    opt = AvifOptimizer()
    small = _png_bytes(size=64)
    try:
        result = opt.convert(small, max_dimension=4096)
    except Exception as exc:
        # Some environments may lack AVIF encoder in Pillow wheel.
        pytest.skip(f"AVIF encoder unavailable: {exc}")
    assert result.width == 64
    assert result.height == 64
    assert result.mime_type == "image/avif"


def _fake_download_side_effect(png: bytes):
    import hashlib

    from scout_api.modules.images.downloader import DownloadedImage

    def fake_download(url: str):
        return DownloadedImage(
            data=png,
            content_type="image/png",
            width=32,
            height=32,
            sha256=hashlib.sha256(png).hexdigest(),
            extension="png",
            source_url=url,
        )

    return fake_download


def test_original_job_survives_restart_and_avif_failure(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    from datetime import timedelta

    from scout_api.core.config import Settings
    from scout_api.modules.images.claim import claim_due_optimizations, utcnow
    from scout_api.modules.images.repository import ProductImageRepository
    from scout_api.modules.images.worker import process_claimed_image

    response = ProductRegistrationService(session).register_saved(
        ProductRegisterRequest(
            title="Recovery",
            gtin="7891991010863",
            images=[
                ApprovedImageInput(
                    source_url="https://cdn.example/a.png", position=0, is_main=True
                )
            ],
        ),
        owner=_OWNER,
    )
    session.expire_all()
    row = ProductImageRepository(session).list_for_product(response.product.id)[0]
    assert row.original_status == "pending"  # No worker: catalog already committed.
    claimed = claim_due_optimizations(session, worker_id="stopped")
    assert len(claimed) == 1
    session.commit()
    row.optimization_claim_expires_at = utcnow() - timedelta(seconds=1)
    session.commit()
    session.expire_all()
    resumed = claim_due_optimizations(session, worker_id="restarted")
    assert len(resumed) == 1
    session.commit()
    with (
        patch.object(
            ImageDownloader,
            "download",
            side_effect=_fake_download_side_effect(_png_bytes()),
        ),
        patch.object(
            AvifOptimizer, "convert", side_effect=RuntimeError("conversion fails")
        ),
    ):
        process_claimed_image(session, resumed[0], drive=drive, settings=Settings())
    session.commit()
    session.expire_all()
    recovered = ProductImageRepository(session).get(row.id)
    assert recovered.original_status == "ready"
    assert recovered.optimized_status == "failed"
    assert drive.download_bytes(recovered.original_drive_file_id) == _png_bytes()
    assert to_image_view(recovered).display_url == to_image_view(recovered).original_url
    assert ProductRegistrationService(session).get_product(
        response.product.id, viewer=_OWNER
    )


@pytest.mark.parametrize(
    "failure",
    [RequestError("indisponível", code="UPSTREAM_ERROR"), ValueError("unexpected")],
)
def test_original_failure_keeps_product_and_can_retry(
    session: Session, drive: InMemoryDriveStorage, failure: Exception
) -> None:
    from scout_api.core.config import Settings
    from scout_api.modules.images.claim import claim_due_optimizations
    from scout_api.modules.images.worker import process_claimed_image

    response = ProductRegistrationService(session).register_saved(
        ProductRegisterRequest(
            title="Retry",
            gtin="7891991010863",
            images=[
                ApprovedImageInput(source_url="https://cdn.example/a.png", position=0)
            ],
        ),
        owner=_OWNER,
    )
    claimed = claim_due_optimizations(session, worker_id="worker")
    session.commit()
    with patch.object(
        ImageDownloader,
        "download",
        side_effect=failure,
    ):
        process_claimed_image(session, claimed[0], drive=drive, settings=Settings())
    session.commit()
    service = ProductImageService(session, drive=drive)
    status = service.import_status(response.product.id, viewer=_OWNER)
    assert status.saved and status.error == 1
    service.retry_optimization(response.product.id, claimed[0].id, owner=_OWNER)
    session.commit()
    resumed = claim_due_optimizations(session, worker_id="worker-retry")
    assert len(resumed) == 1
    with patch.object(
        ImageDownloader,
        "download",
        side_effect=_fake_download_side_effect(_png_bytes()),
    ):
        process_claimed_image(session, resumed[0], drive=drive, settings=Settings())
    session.commit()
    assert service.import_status(response.product.id, viewer=_OWNER).ready == 1


def test_http_registration_never_calls_image_pipeline(session: Session) -> None:
    import time

    from fastapi.testclient import TestClient

    from scout_api.main import app
    from scout_api.modules.auth.deps import require_authenticated_user
    from scout_api.modules.matching.router import get_registration_service

    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_registration_service] = lambda: (
        ProductRegistrationService(session)
    )
    app.dependency_overrides[require_authenticated_user] = lambda: _OWNER
    try:

        def slow_download(_url: str):
            time.sleep(10)
            raise AssertionError("HTTP aguardou o download")

        started = time.perf_counter()
        with patch.object(
            ImageDownloader, "download", side_effect=slow_download
        ) as download:
            response = TestClient(app).post(
                "/products",
                json={
                    "title": "HTTP isolated test",
                    "gtin": "7891991010863",
                    "images": [
                        {"source_url": "https://cdn.example/a.png", "position": 0}
                    ],
                },
            )
        assert response.status_code == 200, response.text
        assert time.perf_counter() - started < 2
        download.assert_not_called()
        assert response.json()["product"]["images"][0]["original_status"] == "pending"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_drive_retry_reuses_uploaded_uuid_file() -> None:
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "existing"}]
    }
    client = GoogleDriveClient(service=service)
    assert (
        client.upload_bytes(
            name="stable-uuid.avif",
            parent_id="folder",
            data=b"avif",
            mime_type="image/avif",
        )
        == "existing"
    )
    service.files.return_value.create.assert_not_called()


def test_background_deduplicates_original_and_avif(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    from scout_api.core.config import Settings
    from scout_api.modules.images.claim import claim_due_optimizations
    from scout_api.modules.images.worker import process_claimed_image

    response = ProductRegistrationService(session).register_saved(
        ProductRegisterRequest(
            title="Dedup",
            gtin="7891991010863",
            images=[
                ApprovedImageInput(
                    source_url=f"https://cdn.example/{i}.png", position=i
                )
                for i in range(2)
            ],
        ),
        owner=_OWNER,
    )
    claimed = claim_due_optimizations(session, worker_id="worker")
    session.commit()
    with patch.object(
        ImageDownloader,
        "download",
        side_effect=_fake_download_side_effect(_png_bytes()),
    ):
        for row in claimed:
            process_claimed_image(session, row, drive=drive, settings=Settings())
            session.commit()
    assert len(drive.files) == 2  # One original and one AVIF, shared by references.
    assert (
        ProductImageService(session, drive=drive)
        .import_status(response.product.id, viewer=_OWNER)
        .ready
        == 2
    )


def test_pending_original_survives_closed_database_engine(
    tmp_path, drive: InMemoryDriveStorage
) -> None:
    from datetime import timedelta

    from scout_api.core.config import Settings
    from scout_api.modules.images.claim import claim_due_optimizations, utcnow
    from scout_api.modules.images.worker import process_claimed_image

    url = f"sqlite:///{tmp_path / 'restart.sqlite3'}"
    engine = create_engine(url)
    create_all(engine)
    with Session(engine) as api_session:
        response = ProductRegistrationService(api_session).register_saved(
            ProductRegisterRequest(
                title="Restart",
                gtin="7891991010863",
                images=[
                    ApprovedImageInput(
                        source_url="https://cdn.example/a.png", position=0
                    )
                ],
            ),
            owner=_OWNER,
        )
        product_id = response.product.id
        claimed = claim_due_optimizations(api_session, worker_id="crashed-worker")
        assert len(claimed) == 1
        api_session.commit()
    engine.dispose()
    reopened = create_engine(url)
    with Session(reopened) as worker_session:
        assert ProductRegistrationService(worker_session).get_product(
            product_id, viewer=_OWNER
        )
        jobs = claim_due_optimizations(
            worker_session,
            worker_id="restarted-worker",
            now=utcnow() + timedelta(seconds=301),
        )
        assert len(jobs) == 1
        worker_session.commit()
        with patch.object(
            ImageDownloader,
            "download",
            side_effect=_fake_download_side_effect(_png_bytes()),
        ):
            process_claimed_image(
                worker_session, jobs[0], drive=drive, settings=Settings()
            )
        worker_session.commit()
        assert (
            ProductImageService(worker_session, drive=drive)
            .import_status(product_id, viewer=_OWNER)
            .ready
            == 1
        )
    reopened.dispose()


def test_save_does_not_wait_for_slow_avif(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    """Critical path: persist returns while convert is artificially slow."""
    import threading
    import time

    from sqlalchemy.orm import sessionmaker

    from scout_api.core.config import Settings
    from scout_api.modules.images.optimizer import OptimizedImage
    from scout_api.modules.images.repository import ProductImageRepository
    from scout_api.modules.images.worker import ImageOptimizationScheduler

    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Async Cam", gtin="7891991010863"),
        owner=_OWNER,
    ).product

    hold = threading.Event()
    convert_started = threading.Event()
    settings = Settings(
        image_optimization_enabled=True,
        image_avif_max_concurrency=1,
    )
    pipeline = ImagePipeline(
        session, drive=drive, settings=settings, schedule_avif=True
    )

    def slow_convert(data: bytes, **kwargs):
        convert_started.set()
        assert hold.wait(timeout=5)
        return OptimizedImage(
            data=b"fake-avif",
            mime_type="image/avif",
            width=32,
            height=32,
            size_bytes=9,
        )

    png = _png_bytes()
    with patch.object(
        pipeline._downloader,
        "download",
        side_effect=_fake_download_side_effect(png),
    ):
        t0 = time.perf_counter()
        rows = pipeline.persist_approved(
            product.id,
            [
                ApprovedImageInput(
                    source_url="https://cdn.example/slow.png",
                    position=0,
                    is_main=True,
                )
            ],
        )
        save_ms = (time.perf_counter() - t0) * 1000

    assert save_ms < 2000, f"save blocked on AVIF: {save_ms:.1f}ms"
    row = rows[0]
    assert row.original_status == "ready"
    assert row.optimized_status == "pending"
    assert not convert_started.is_set()
    session.commit()

    factory = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    sched = ImageOptimizationScheduler(
        settings=settings, drive=drive, session_factory=factory
    )

    from scout_api.modules.images import worker as worker_mod

    def process_with_slow(session_obj, image, *, drive, settings):
        pipe = ImagePipeline(
            session_obj, drive=drive, settings=settings, schedule_avif=False
        )
        with patch.object(pipe._optimizer, "convert", side_effect=slow_convert):
            return pipe.optimize_now(image, already_claimed=True)

    with patch.object(
        worker_mod, "process_claimed_image", side_effect=process_with_slow
    ):
        worker = threading.Thread(target=sched.sweep_once_inline)
        worker.start()
        assert convert_started.wait(timeout=5)
        # Save already returned long before convert finishes.
        hold.set()
        worker.join(timeout=15)
    assert not worker.is_alive()

    db = factory()
    try:
        done = ProductImageRepository(db).get(row.id)
        assert done is not None
        assert done.optimized_status == "ready"
        assert done.optimized_drive_file_id
    finally:
        db.close()


def test_restart_recovers_pending_avif(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    from sqlalchemy.orm import sessionmaker

    from scout_api.core.config import Settings
    from scout_api.modules.images.optimizer import OptimizedImage
    from scout_api.modules.images.repository import ProductImageRepository
    from scout_api.modules.images.worker import ImageOptimizationScheduler

    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Restart Cam", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    pipeline = ImagePipeline(session, drive=drive, schedule_avif=True)
    png = _png_bytes(color=(1, 2, 3))
    with patch.object(
        pipeline._downloader, "download", side_effect=_fake_download_side_effect(png)
    ):
        rows = pipeline.persist_approved(
            product.id,
            [
                ApprovedImageInput(
                    source_url="https://cdn.example/restart.png",
                    position=0,
                    is_main=True,
                )
            ],
        )
    session.commit()
    image_id = rows[0].id
    assert rows[0].optimized_status == "pending"

    # Simulate API restart: new scheduler instance, same durable pending row.
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    settings = Settings(image_avif_max_concurrency=1)
    worker_a = ImageOptimizationScheduler(
        settings=settings, drive=drive, session_factory=factory
    )
    del worker_a
    worker_b = ImageOptimizationScheduler(
        settings=settings, drive=drive, session_factory=factory
    )

    from scout_api.modules.images import worker as worker_mod

    def process_fake(session_obj, image, *, drive, settings):
        pipe = ImagePipeline(
            session_obj, drive=drive, settings=settings, schedule_avif=False
        )
        with patch.object(
            pipe._optimizer,
            "convert",
            return_value=OptimizedImage(
                data=b"avif",
                mime_type="image/avif",
                width=32,
                height=32,
                size_bytes=4,
            ),
        ):
            return pipe.optimize_now(image, already_claimed=True)

    with patch.object(worker_mod, "process_claimed_image", side_effect=process_fake):
        summary = worker_b.sweep_once_inline()
    assert summary["claimed"] == 1
    assert summary["processed"] == 1

    db = factory()
    try:
        done = ProductImageRepository(db).get(image_id)
        assert done is not None
        assert done.optimized_status == "ready"
    finally:
        db.close()


def test_duplicate_claim_single_conversion(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    from sqlalchemy.orm import sessionmaker

    from scout_api.core.config import Settings
    from scout_api.modules.images.claim import claim_due_optimizations, new_worker_id
    from scout_api.modules.images.repository import ProductImageRepository

    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Claim Cam", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    pipeline = ImagePipeline(session, drive=drive, schedule_avif=True)
    png = _png_bytes(color=(9, 9, 9))
    with patch.object(
        pipeline._downloader, "download", side_effect=_fake_download_side_effect(png)
    ):
        rows = pipeline.persist_approved(
            product.id,
            [
                ApprovedImageInput(
                    source_url="https://cdn.example/claim.png",
                    position=0,
                    is_main=True,
                )
            ],
        )
    session.commit()

    factory = sessionmaker(bind=session.get_bind(), autoflush=False, autocommit=False)
    s1 = factory()
    s2 = factory()
    try:
        settings = Settings(image_optimization_batch_size=4)
        claimed1 = claim_due_optimizations(
            s1, worker_id=new_worker_id(), settings=settings
        )
        s1.commit()
        claimed2 = claim_due_optimizations(
            s2, worker_id=new_worker_id(), settings=settings
        )
        s2.commit()
        assert len(claimed1) == 1
        assert claimed1[0].id == rows[0].id
        assert claimed2 == []
    finally:
        s1.close()
        s2.close()

    # Convert once; second optimize_now is no-op when ready.
    db = factory()
    try:
        row = ProductImageRepository(db).get(rows[0].id)
        assert row is not None
        pipe = ImagePipeline(db, drive=drive, schedule_avif=False)
        convert_calls = {"n": 0}

        def counting_convert(data: bytes, **kwargs):
            from scout_api.modules.images.optimizer import OptimizedImage

            convert_calls["n"] += 1
            return OptimizedImage(
                data=b"avif",
                mime_type="image/avif",
                width=32,
                height=32,
                size_bytes=4,
            )

        with patch.object(pipe._optimizer, "convert", side_effect=counting_convert):
            pipe.optimize_now(row, already_claimed=True)
            pipe.optimize_now(row, already_claimed=True)
        assert convert_calls["n"] == 1
        assert row.optimized_status == "ready"
    finally:
        db.close()


def test_product_list_and_detail_use_display_fallback(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    """Product list/detail cards must show original while AVIF is pending."""
    reg = ProductRegistrationService(session)
    created = reg.register(
        ProductRegisterRequest(title="Card Cam", gtin="7891991010863"),
        owner=_OWNER,
    )
    product_id = created.product.id
    svc = ProductImageService(session, drive=drive, schedule_avif=False)
    png = _png_bytes(color=(50, 50, 50))
    with patch.object(
        svc._pipeline._downloader,
        "download",
        side_effect=_fake_download_side_effect(png),
    ):
        view = svc.add_image(
            product_id,
            AddProductImageRequest(
                source_url="https://cdn.example/card.png", is_main=True
            ),
            owner=_OWNER,
        )
    session.flush()

    listed = reg.list_products(viewer=_OWNER, limit=10)
    match = next(p for p in listed.items if p.id == product_id)
    assert match.primary_image_url == view.display_url
    assert match.primary_image_url and "variant=original" in match.primary_image_url
    assert match.images[0].optimized_status == "pending"

    detail = reg.get_product(product_id, viewer=_OWNER)
    assert detail is not None
    assert detail.primary_image_url == match.primary_image_url
    assert detail.images[0].display_url == view.original_url

    # After AVIF ready, primary switches without re-import.
    from scout_api.modules.images.repository import ProductImageRepository

    row = ProductImageRepository(session).get(view.image_id)
    assert row is not None
    row.optimized_status = "ready"
    row.optimized_drive_file_id = "opt-1"
    session.flush()
    detail2 = reg.get_product(product_id, viewer=_OWNER)
    assert detail2 is not None
    assert (
        detail2.primary_image_url and "variant=optimized" in detail2.primary_image_url
    )
    assert detail2.images[0].display_url == detail2.images[0].optimized_url


def test_retry_reuses_original_without_redownload(
    session: Session, drive: InMemoryDriveStorage
) -> None:
    reg = ProductRegistrationService(session)
    product = reg.register(
        ProductRegisterRequest(title="Retry Cam", gtin="7891991010863"),
        owner=_OWNER,
    ).product
    svc = ProductImageService(session, drive=drive, schedule_avif=False)
    png = _png_bytes(color=(7, 7, 7))
    with patch.object(
        svc._pipeline._downloader,
        "download",
        side_effect=_fake_download_side_effect(png),
    ):
        view = svc.add_image(
            product.id,
            AddProductImageRequest(
                source_url="https://cdn.example/retry.png", is_main=True
            ),
            owner=_OWNER,
        )
    from scout_api.modules.images.repository import ProductImageRepository

    row = ProductImageRepository(session).get(view.image_id)
    assert row is not None
    row.optimized_status = "failed"
    row.optimized_error = "boom"
    session.flush()

    downloads = {"n": 0}

    def counting_download(url: str):
        downloads["n"] += 1
        return _fake_download_side_effect(png)(url)

    from scout_api.modules.images.optimizer import OptimizedImage

    with (
        patch.object(
            svc._pipeline._downloader, "download", side_effect=counting_download
        ),
        patch.object(
            svc._pipeline._optimizer,
            "convert",
            return_value=OptimizedImage(
                data=b"avif",
                mime_type="image/avif",
                width=32,
                height=32,
                size_bytes=4,
            ),
        ),
    ):
        retried = svc.retry_optimization(
            product.id, view.image_id, owner=_OWNER, sync=True
        )
    assert downloads["n"] == 0
    assert retried.optimized_status == "ready"
    row2 = ProductImageRepository(session).get(view.image_id)
    assert row2 is not None
    assert row2.original_drive_file_id == row.original_drive_file_id
