"""Contract tests for PriceScout integration endpoints and CORS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from scout_api.core.config import get_settings
from scout_api.core.rate_limit import reset_rate_limiter
from scout_api.main import app


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch):
    secret = "test-hs256-secret-for-pricescout-contract"
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    reset_rate_limiter()
    yield secret
    get_settings.cache_clear()
    reset_rate_limiter()


def _mint(secret: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "exp": now + timedelta(minutes=15),
            "iat": now,
            "app_metadata": {"provider": "google"},
            "user_metadata": {"full_name": "Contract User"},
        },
        secret,
        algorithm="HS256",
    )


def test_cors_preflight_allows_patch_delete_and_authorization(
    auth_settings: str,
) -> None:
    client = TestClient(app)
    for method in ("PATCH", "DELETE"):
        response = client.options(
            "/products/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code in {200, 204}
        assert (
            response.headers.get("access-control-allow-origin")
            == "http://localhost:3000"
        )
        allow_methods = response.headers.get("access-control-allow-methods", "")
        assert method in allow_methods
        allow_headers = response.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allow_headers


def test_stores_requires_auth(auth_settings: str) -> None:
    client = TestClient(app)
    assert client.get("/stores").status_code == 401


def test_stores_lists_registry(auth_settings: str) -> None:
    client = TestClient(app)
    token = _mint(auth_settings)
    response = client.get("/stores", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert "stores" in body
    assert any(item["key"] == "kabum" for item in body["stores"])
    kabum = next(item for item in body["stores"] if item["key"] == "kabum")
    assert kabum["implemented"] is True
    assert kabum["country"] == "BR"
    assert kabum["supported_country_currency_pairs"] == [["BR", "BRL"]]
    assert "kabum.com.br" in kabum["domains"]
    visaovip = next(item for item in body["stores"] if item["key"] == "visaovip")
    assert visaovip["country"] == "PY"
    assert visaovip["currency"] == "USD"
    assert visaovip["supported_country_currency_pairs"] == [["PY", "USD"]]
    shoppingchina = next(
        item for item in body["stores"] if item["key"] == "shoppingchina"
    )
    assert shoppingchina["supported_country_currency_pairs"] == [
        ["PY", "PYG"],
        ["PY", "BRL"],
    ]


def test_store_metadata_update_requires_admin(auth_settings: str) -> None:
    client = TestClient(app)
    token = _mint(auth_settings)
    response = client.patch(
        "/admin/stores/kabum",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "Nome"},
    )
    assert response.status_code == 403


def test_store_metadata_update_allows_dev_bypass_only(
    auth_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    client = TestClient(app)

    bypass_response = client.patch(
        "/admin/stores/kabum", json={"display_name": "Nome local"}
    )
    assert bypass_response.status_code == 503  # Passou auth; DB está desativado.

    user_token = _mint(auth_settings)
    user_response = client.patch(
        "/admin/stores/kabum",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"display_name": "Nome local"},
    )
    assert user_response.status_code == 403


def test_store_logo_upload_allows_only_admin_or_dev_bypass(
    auth_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    client = TestClient(app)
    files = {"file": ("logo.png", b"png", "image/png")}

    bypass_response = client.post("/admin/stores/kabum/logo", files=files)
    assert bypass_response.status_code == 503  # Passou auth; DB está desativado.

    user_token = _mint(auth_settings)
    user_response = client.post(
        "/admin/stores/kabum/logo",
        headers={"Authorization": f"Bearer {user_token}"},
        files=files,
    )
    assert user_response.status_code == 403


def test_products_list_requires_auth(auth_settings: str) -> None:
    client = TestClient(app)
    assert client.get("/products").status_code == 401


def test_product_resolve_requires_auth(auth_settings: str) -> None:
    client = TestClient(app)
    response = client.post(
        "/products/resolve",
        json={"title": "Sem auth", "gtin": "7891991010863"},
    )
    assert response.status_code == 401


def test_openapi_includes_pricescout_routes(auth_settings: str) -> None:
    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/products" in paths
    assert "post" in paths["/products/resolve"]
    assert "get" in paths["/products"]
    assert "patch" in paths["/products/{product_id}"]
    assert "delete" in paths["/products/{product_id}"]
    assert "/stores" in paths
    assert "/admin/stores/{store_key}" in paths
    assert "patch" in paths["/admin/stores/{store_key}"]
    assert "/match/stream" not in paths
    assert "/products/{product_id}/match-runs" in paths
    assert "/match-runs/{run_id}" in paths
    assert "/notifications" in paths
    assert "delete" in paths["/notifications"]
    assert "delete" in paths["/notifications/{notification_id}"]
    # Product images gallery (ADR 0029)
    assert "/products/{product_id}/images" in paths
    assert "get" in paths["/products/{product_id}/images"]
    assert "post" in paths["/products/{product_id}/images"]
    assert "patch" in paths["/products/{product_id}/images"]
    assert "delete" in paths["/products/{product_id}/images/{image_id}"]
    assert "/products/{product_id}/images/{image_id}/content" in paths
    assert "/products/{product_id}/images/{image_id}/retry-optimization" in paths
    # Register accepts approved images after preview review.
    register_body = paths["/products"]["post"]["requestBody"]
    schema_ref = register_body["content"]["application/json"]["schema"]
    components = schema["components"]["schemas"]
    register_name = schema_ref.get("$ref", "").split("/")[-1]
    register_schema = components[register_name]
    assert "images" in register_schema.get("properties", {})
    # Crawl exposes external candidates (not persisted).
    crawl_item = components.get("ProductPriceItem") or {}
    crawl_props = crawl_item.get("properties", {})
    assert "image_candidates" in crawl_props


def test_images_gallery_requires_auth(auth_settings: str) -> None:
    client = TestClient(app)
    product_id = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/products/{product_id}/images").status_code == 401
    assert (
        client.get(f"/products/{product_id}/images/{product_id}/content").status_code
        == 401
    )
